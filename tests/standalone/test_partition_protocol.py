"""Protocol regressions with a simulated Ixion; no private fixtures or network."""
import asyncio
import importlib.util
import json
import pathlib
import sys
import types
import unittest
import requests
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = "partition_protocol_test_pkg"
package = types.ModuleType(PKG)
package.__path__ = [str(ROOT / "custom_components" / "sigma_connect_ha")]
sys.modules[PKG] = package
client_module = __import__(PKG + ".partition_client", fromlist=["PartitionClient"])
base_module = __import__(PKG + ".sigma_client", fromlist=["SigmaClient"])
PartitionClient = client_module.PartitionClient
from bs4 import BeautifulSoup


def html(part, kind="zones", state="AΦOΠΛIΣMENO"):
    header = f"<p>Τμήμα {part}: {state}</p><div>Μπαταρία: 13.7 Volt</div><div>Παροχή 230V: NAI</div>"
    if kind == "zones":
        return header + '<table class="normaltable"><tr><th>Zone</th></tr><tr><td>21</td><td>Example</td><td>Κλειστή</td><td>OXI</td></tr></table>'
    if kind == "access":
        return header + '<a href="/arm">Arm</a><a href="/disarm">Disarm</a><a href="/stay">Stay</a>'
    return header + '<a href="zones.html">Κατάσταση ζωνών</a>'


class Response:
    def __init__(self, text, path="zones.html"):
        self.text = text
        self.url = "http://alarm.example:5053/" + path
        self.status_code = 200

    def raise_for_status(self):
        pass


class Ixion:
    def __init__(self):
        self.auth = False
        self.part = "1"
        self.states = {"1": "AΦOΠΛIΣMENO", "2": "AΦOΠΛIΣMENO"}
        self.logins = 0
        self.pin_submissions = 0
        self.expire_on_command = False
        self.reject_pin_once = False
        self.wrong_zones = False
        self.commands = []
        self.explicit_selection = False
        self.cookies = {}

    def close(self):
        pass

    def get(self, url, **kwargs):
        path = url.rsplit("/", 1)[-1]
        if path == "logout.html":
            self.auth = False
            return Response("")
        if path == "login.html" or not self.auth:
            return Response('<input name="gen_input" value="abcdefghijklmnop">', "login.html")
        if path == "user.html":
            return Response('<input name="gen_input" value="abcdefghijklmnop">', path)
        if path in ("arm", "disarm", "stay", "arm.html", "disarm.html", "stay.html"):
            if self.expire_on_command:
                self.expire_on_command = False
                self.auth = False
                return Response('<input name="gen_input" value="abcdefghijklmnop">', "login.html")
            action = path.split(".")[0]
            self.commands.append((self.part, action))
            self.states[self.part] = {"arm": "OΠΛIΣMENO", "disarm": "AΦOΠΛIΣMENO", "stay": "ΠEPIMETPIKH OΠΛIΣH"}[action]
        if path == "panel.html" and self.explicit_selection:
            return Response('<form action="part.cgi"></form>', path)
        kind = "zones" if path == "zones.html" else "access" if path == "access.html" else "panel"
        part = ("1" if self.part == "2" else "2") if self.wrong_zones and kind == "zones" else self.part
        return Response(html(part, kind, self.states[self.part]), path)

    def post(self, url, data, **kwargs):
        path = url.rsplit("/", 1)[-1]
        if path == "login.html":
            self.logins += 1
            self.auth = True
        elif path == "ucode":
            self.pin_submissions += 1
            if self.reject_pin_once:
                self.reject_pin_once = False
                self.auth = False
            else:
                self.part = "1" if data["password"] == "pin-one" else "2"
        elif path == "part.cgi":
            self.part = data["part"].replace("part", "")
        return Response(html(self.part, "panel", self.states[self.part]), path)


class ObservedLock(asyncio.Lock):
    """Signal once an action is actually queued behind the current owner."""
    def __init__(self):
        super().__init__()
        self.waiter_started = asyncio.Event()

    async def acquire(self):
        if self.locked():
            self.waiter_started.set()
        return await super().acquire()


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.panel = Ixion()
        loop = asyncio.get_running_loop()
        self.coordinator = types.SimpleNamespace(
            lock=asyncio.Lock(), hass=types.SimpleNamespace(loop=loop),
            updates=[], async_set_partition_data=lambda p, d: self.coordinator.updates.append((p, d)),
        )
        self.client = PartitionClient("http://alarm.example:5053", "example", "example", "1111",
                                      self.coordinator, second_pin="2222", send_analytics=False)
        self.client.session.close()
        self.client.session = self.panel
        self.client._create_session = lambda: self.panel
        self.client._encrypt = lambda secret, token: ("pin-one" if secret == "1111" else "pin-two", "1")
        self.sleep_patch = patch(PKG + ".sigma_client.time.sleep")
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def test_partition_switch_discards_old_session(self):
        for part in ("1", "2", "2", "1"):
            data = self.client.safe_get_partition_status(part)
            self.assertEqual(data["battery_volt"], 13.7)
        self.assertEqual(self.panel.logins, 3)
        self.assertEqual(self.panel.pin_submissions, 3)

    def test_expired_session_recovers(self):
        self.client.safe_get_partition_status("1")
        self.panel.auth = False
        self.client.safe_get_partition_status("1")
        self.assertEqual(self.panel.logins, 2)

    def test_pin_switch_failure_recovers_with_login(self):
        self.client.safe_get_partition_status("1")
        self.panel.reject_pin_once = True
        self.client.safe_get_partition_status("2")
        self.assertEqual(self.panel.part, "2")
        self.assertEqual(self.panel.logins, 3)

    def test_explicit_selection_without_header(self):
        self.panel.explicit_selection = True
        self.client.safe_get_partition_status("2")
        self.assertEqual(self.panel.part, "2")

    def test_wrong_zones_rejected(self):
        self.panel.wrong_zones = True
        with self.assertRaises(ValueError):
            self.client.safe_get_partition_status("2")
        self.assertEqual(self.panel.commands, [])

    async def test_actions_reuse_upstream_loop_and_preserve_other_partition(self):
        ok = await asyncio.to_thread(self.client.perform_partition_action, "arm", "2")
        self.assertTrue(ok)
        await asyncio.sleep(0)
        self.assertEqual(self.panel.commands, [("2", "arm")])
        self.assertEqual(self.panel.states["1"], "AΦOΠΛIΣMENO")
        self.assertEqual(self.coordinator.updates[-1][0], "2")
        self.assertFalse(self.coordinator.lock.locked())

    async def test_expired_action_redirect_recovers(self):
        self.panel.expire_on_command = True
        ok = await asyncio.to_thread(self.client.perform_partition_action, "arm", "2")
        self.assertTrue(ok)
        self.assertEqual(self.panel.commands, [("2", "arm")])
        self.assertGreaterEqual(self.panel.logins, 2)

    async def test_command_network_failure_uses_upstream_retry(self):
        original = self.panel.get
        failed = False
        def get(url, **kwargs):
            nonlocal failed
            if url.endswith("/arm") and not failed:
                failed = True
                raise ConnectionError("simulated network failure")
            return original(url, **kwargs)
        self.panel.get = get
        self.assertTrue(await asyncio.to_thread(self.client.perform_partition_action, "arm", "2"))
        self.assertGreaterEqual(self.panel.logins, 2)
        self.assertEqual(self.panel.commands, [("2", "arm")])

    async def test_disarm_uses_confirmed_control_headers_when_zone_table_is_empty(self):
        self.panel.states["2"] = "\u039f\u03a0\u039b\u0399\u03a3\u039c\u0395\u039d\u039f"
        other_partition = self.panel.states["1"]
        original = self.panel.get
        def get(url, **kwargs):
            if url.endswith("/zones.html"):
                return Response(html("2", kind="panel") + '<table class="normaltable"><tr><th>Zone</th></tr></table>')
            return original(url, **kwargs)
        self.panel.get = get
        with patch.object(self.panel, "get", wraps=self.panel.get) as calls:
            self.assertTrue(await asyncio.to_thread(self.client.perform_partition_action, "disarm", "2"))
        await asyncio.sleep(0)
        paths = [call.args[0].rsplit("/", 1)[-1] for call in calls.call_args_list]
        self.assertNotIn("zones.html", paths)
        self.assertEqual(paths.count("panel.html"), 3)
        self.assertEqual(paths.count("access.html"), 1)
        self.assertEqual(self.panel.commands, [("2", "disarm")])
        self.assertEqual(self.panel.states["1"], other_partition)
        self.assertEqual(self.panel.logins, 1)
        published = self.coordinator.updates[-1][1]
        self.assertEqual(published["status"], "Disarmed")
        self.assertEqual(published["zones"], [])
        self.assertIs(published["zones_available"], False)
        self.assertNotIn(unittest.mock.call(1.0), base_module.time.sleep.call_args_list)
        self.assertNotIn(unittest.mock.call(2.0), base_module.time.sleep.call_args_list)
        self.assertFalse(self.coordinator.lock.locked())

    async def test_wrong_partition_control_header_never_sends_command(self):
        original = self.panel.get
        states_before = self.panel.states.copy()
        def get(url, **kwargs):
            if url.endswith("/access.html"):
                return Response(html("1"), "access.html")
            return original(url, **kwargs)
        self.panel.get = get
        with patch(PKG + ".sigma_client.MAX_ACTION_ATTEMPTS", 1):
            self.assertFalse(await asyncio.to_thread(self.client.perform_partition_action, "arm", "2"))
        await asyncio.sleep(0)
        self.assertEqual(self.panel.commands, [])
        self.assertEqual(self.panel.states, states_before)
        self.assertEqual(self.coordinator.updates, [])
        self.assertFalse(self.coordinator.lock.locked())

    async def test_unknown_or_authenticated_form_control_page_never_sends_command(self):
        original = self.panel.get
        pages = {
            "unknown_state": html("2", state="UNRECOGNIZED STATE"),
            "web_login": html("2") + '<form action="login.html"><input name="username" value="PRIVATE_USER"><input name="password" value="PRIVATE_PASS"><input name="gen_input" value="PRIVATE_TOKEN"></form>',
            "pin": html("2") + '<form action="ucode"><input name="password" value="PRIVATE_PIN"><input name="gen_input" value="PRIVATE_TOKEN"></form>',
        }
        for kind, page in pages.items():
            with self.subTest(kind=kind):
                self.panel.get = original
                def get(url, **kwargs):
                    if url.endswith("/access.html"):
                        return Response(page, "access.html")
                    return original(url, **kwargs)
                self.panel.get = get
                with patch(PKG + ".sigma_client.MAX_ACTION_ATTEMPTS", 1):
                    self.assertFalse(await asyncio.to_thread(self.client.perform_partition_action, "arm", "2"))
                await asyncio.sleep(0)
                self.assertEqual(self.panel.commands, [])
                self.assertEqual(self.coordinator.updates, [])
                self.assertFalse(self.coordinator.lock.locked())

    async def test_wrong_partition_after_command_cannot_confirm_success(self):
        original_get, original_post = self.panel.get, self.panel.post
        def get(url, **kwargs):
            if url.endswith("/panel.html") and self.panel.commands:
                return Response(html("1", state=self.panel.states["2"]), "panel.html")
            return original_get(url, **kwargs)
        def post(url, data, **kwargs):
            if url.endswith("/part.cgi") and self.panel.commands:
                return Response(html("1", state=self.panel.states["2"]), "part.cgi")
            return original_post(url, data, **kwargs)
        self.panel.get, self.panel.post = get, post
        with patch(PKG + ".sigma_client.MAX_ACTION_ATTEMPTS", 1), \
                patch.object(self.client, "POLLS_PER_ATTEMPT", 2):
            self.assertFalse(await asyncio.to_thread(self.client.perform_partition_action, "arm", "2"))
        await asyncio.sleep(0)
        self.assertEqual(self.panel.commands, [("2", "arm")])
        self.assertEqual(self.coordinator.updates, [])
        self.assertFalse(self.coordinator.lock.locked())

    async def test_unaccepted_disarm_does_not_publish_false_success(self):
        self.panel.states["2"] = "\u039f\u03a0\u039b\u0399\u03a3\u039c\u0395\u039d\u039f"
        armed = self.panel.states["2"]
        original = self.panel.get
        def get(url, **kwargs):
            if url.endswith("/disarm"):
                self.panel.commands.append((self.panel.part, "disarm"))
                return Response(html(self.panel.part, kind="panel", state=armed), "disarm")
            return original(url, **kwargs)
        self.panel.get = get
        with patch(PKG + ".sigma_client.MAX_ACTION_ATTEMPTS", 1), \
                patch.object(self.client, "POLLS_PER_ATTEMPT", 2):
            self.assertFalse(await asyncio.to_thread(self.client.perform_partition_action, "disarm", "2"))
        await asyncio.sleep(0)
        self.assertEqual(self.panel.commands, [("2", "disarm")])
        self.assertEqual(self.panel.states["2"], armed)
        self.assertEqual(self.coordinator.updates, [])
        self.assertFalse(self.coordinator.lock.locked())

    async def test_already_disarmed_publishes_fresh_confirmation_without_command(self):
        self.coordinator.updates.append(("2", {"status": "Armed", "zones_available": True,
                                                "zones": [{"zone_id": 21, "status": "Closed"}]}))
        with patch.object(self.panel, "get", wraps=self.panel.get) as calls:
            self.assertTrue(await asyncio.to_thread(self.client.perform_partition_action, "disarm", "2"))
        await asyncio.sleep(0)
        self.assertEqual(self.panel.commands, [])
        self.assertEqual(len(self.coordinator.updates), 2)
        part, published = self.coordinator.updates[-1]
        self.assertEqual(part, "2")
        self.assertEqual(published["status"], "Disarmed")
        self.assertEqual(published["zones"], [])
        self.assertIs(published["zones_available"], False)
        paths = [call.args[0].rsplit("/", 1)[-1] for call in calls.call_args_list]
        self.assertNotIn("access.html", paths)
        self.assertNotIn("zones.html", paths)
        self.assertFalse(self.coordinator.lock.locked())

    async def test_single_partition_already_desired_does_not_overwrite_zone_data(self):
        # The original client historically returns early without publishing an empty table.
        base = base_module.SigmaClient("http://alarm.example:5053", "example", "example", "1111",
                                       self.coordinator, send_analytics=False)
        base.session.close()
        base.session = self.panel
        base._session_authenticated = True
        self.panel.auth = True
        original = self.panel.get
        def get(url, **kwargs):
            if url.endswith("/zones.html"):
                return Response(html("1", kind="panel"), "zones.html")
            return original(url, **kwargs)
        self.panel.get = get
        existing = ("1", {"status": "Disarmed", "zones": [{"zone_id": 1, "status": "Closed"}]})
        self.coordinator.updates.append(existing)
        with patch.object(base, "_publish_action_data", create=True) as publish:
            self.assertTrue(await asyncio.to_thread(base.perform_action, "disarm"))
        await asyncio.sleep(0)
        publish.assert_not_called()
        self.assertEqual(self.coordinator.updates, [existing])
        self.assertEqual(self.panel.commands, [])
        self.assertFalse(self.coordinator.lock.locked())

    async def test_headerless_control_pages_use_authoritative_zones_fallback(self):
        self.panel.explicit_selection = True
        original_get, original_post = self.panel.get, self.panel.post
        headerless = '<a href="zones.html">Zones</a><a href="/arm">Arm</a><a href="/disarm">Disarm</a><a href="/stay">Stay</a>'
        def get(url, **kwargs):
            if url.endswith("/access.html"):
                return Response(headerless, "access.html")
            return original_get(url, **kwargs)
        def post(url, data, **kwargs):
            result = original_post(url, data, **kwargs)
            return Response(headerless, "part.cgi") if url.endswith("/part.cgi") else result
        self.panel.get, self.panel.post = get, post
        with patch.object(self.panel, "get", wraps=self.panel.get) as calls:
            self.assertTrue(await asyncio.to_thread(self.client.perform_partition_action, "arm", "2"))
        await asyncio.sleep(0)
        paths = [call.args[0].rsplit("/", 1)[-1] for call in calls.call_args_list]
        self.assertEqual(paths.count("zones.html"), 3)
        self.assertEqual(self.panel.commands, [("2", "arm")])
        published = self.coordinator.updates[-1][1]
        self.assertEqual(published["status"], "Armed")
        self.assertTrue(published["zones"])
        self.assertIs(published["zones_available"], True)
        self.assertFalse(self.coordinator.lock.locked())

    async def test_action_lock_timeout_cancels_waiter_without_unlocking_another_owner(self):
        lock = ObservedLock()
        self.coordinator.lock = lock
        await lock.acquire()
        self.client.ACTION_LOCK_TIMEOUT = 0.02
        with patch.object(self.panel, "get", wraps=self.panel.get) as get:
            task = asyncio.create_task(asyncio.to_thread(self.client.perform_partition_action, "arm", "2"))
            await asyncio.wait_for(lock.waiter_started.wait(), timeout=1)
            self.assertFalse(await asyncio.wait_for(task, timeout=2))
            get.assert_not_called()
        self.assertTrue(lock.locked())  # The original owner retains its lock.
        self.assertEqual(self.panel.commands, [])
        self.assertEqual(self.panel.logins, 0)
        lock.release()  # Release strictly after the waiter's deadline has expired.
        # An abandoned acquisition would consume this release and strand the next owner.
        await asyncio.wait_for(lock.acquire(), timeout=1)
        await asyncio.sleep(0)
        self.assertTrue(lock.locked())  # The timed-out action cannot release this third owner.
        self.assertEqual(self.panel.commands, [])
        lock.release()
        self.client.ACTION_LOCK_TIMEOUT = 1
        self.assertTrue(await asyncio.wait_for(
            asyncio.to_thread(self.client.perform_partition_action, "arm", "2"), timeout=2))
        await asyncio.sleep(0)
        self.assertEqual(self.panel.commands, [("2", "arm")])
        self.assertFalse(lock.locked())

    async def test_action_lock_owner_release_before_deadline_runs_waiting_action(self):
        lock = ObservedLock()
        self.coordinator.lock = lock
        await lock.acquire()
        self.client.ACTION_LOCK_TIMEOUT = 1
        with patch.object(self.panel, "get", wraps=self.panel.get) as get:
            task = asyncio.create_task(asyncio.to_thread(self.client.perform_partition_action, "stay", "2"))
            await asyncio.wait_for(lock.waiter_started.wait(), timeout=1)
            get.assert_not_called()
            self.assertEqual(self.panel.commands, [])
            lock.release()  # The loop-owned waiter is still within its deadline.
            self.assertTrue(await asyncio.wait_for(task, timeout=2))
        await asyncio.sleep(0)
        self.assertEqual(self.panel.commands, [("2", "stay")])
        self.assertFalse(lock.locked())

    async def test_stay_from_fully_armed_variants_is_nonretryable_and_sends_no_command(self):
        other_partition = self.panel.states["1"]
        for state in ("\u039f\u03a0\u039b\u0399\u03a3\u039c\u0395\u039d\u039f",
                      "\u039f\u03a0\u039b\u0399\u03a3\u039c\u0395\u039d\u039f AUTO",
                      "\u039f\u03a0\u039b\u0399\u03a3\u039c\u0395\u039d\u039f \u039c\u0395 \u0396\u03a9\u039d\u0395\u03a3 BYPASS"):
            with self.subTest(state=state):
                self.panel.states["2"] = state
                with patch.object(self.panel, "get", wraps=self.panel.get) as get, \
                        patch(PKG + ".sigma_client.MAX_ACTION_ATTEMPTS", 5):
                    with self.assertRaises(client_module.ActionNotAllowedError):
                        await asyncio.to_thread(self.client.perform_partition_action, "stay", "2")
                await asyncio.sleep(0)
                paths = [call.args[0].rsplit("/", 1)[-1] for call in get.call_args_list]
                self.assertEqual(paths.count("panel.html"), 1)
                self.assertEqual(paths.count("zones.html"), 0)
                self.assertNotIn("access.html", paths)
                self.assertEqual(self.panel.commands, [])
                self.assertEqual(self.panel.states["2"], state)
                self.assertEqual(self.panel.states["1"], other_partition)
                self.assertFalse(self.coordinator.lock.locked())
        self.assertEqual(self.coordinator.updates, [])

    async def test_stay_already_perimeter_returns_success_without_a_command(self):
        self.panel.states["2"] = "\u03a0\u0395\u03a1\u0399\u039c\u0395\u03a4\u03a1\u0399\u039a\u0397 \u039f\u03a0\u039b\u0399\u03a3\u0397"
        states_before = self.panel.states.copy()
        with patch.object(self.panel, "get", wraps=self.panel.get) as get:
            self.assertTrue(await asyncio.to_thread(self.client.perform_partition_action, "stay", "2"))
        await asyncio.sleep(0)
        paths = [call.args[0].rsplit("/", 1)[-1] for call in get.call_args_list]
        self.assertEqual(paths.count("zones.html"), 0)
        self.assertNotIn("access.html", paths)
        self.assertEqual(self.panel.commands, [])
        self.assertEqual(self.panel.states, states_before)
        self.assertEqual(self.coordinator.updates[-1][1]["status"], "Armed Perimeter")
        self.assertEqual(self.coordinator.updates[-1][1]["zones"], [])
        self.assertIs(self.coordinator.updates[-1][1]["zones_available"], False)
        self.assertFalse(self.coordinator.lock.locked())

    async def test_stay_from_disarmed_still_sends_one_partition_two_command(self):
        other_partition = self.panel.states["1"]
        self.assertTrue(await asyncio.to_thread(self.client.perform_partition_action, "stay", "2"))
        await asyncio.sleep(0)
        self.assertEqual(self.panel.commands, [("2", "stay")])
        self.assertEqual(self.panel.states["1"], other_partition)
        self.assertEqual(self.coordinator.updates[-1][0], "2")
        self.assertEqual(self.coordinator.updates[-1][1]["status"], "Armed Perimeter")
        self.assertFalse(self.coordinator.lock.locked())

    async def test_external_full_arming_before_preflight_blocks_stay_without_retries(self):
        original = self.panel.get
        reads = 0
        accesses = 0
        armed = "\u039f\u03a0\u039b\u0399\u03a3\u039c\u0395\u039d\u039f"
        other_partition = self.panel.states["1"]
        def get(url, **kwargs):
            nonlocal reads, accesses
            if url.endswith("/zones.html"):
                reads += 1
            if url.endswith("/access.html"):
                accesses += 1
                self.panel.states["2"] = armed
            return original(url, **kwargs)
        self.panel.get = get
        with patch(PKG + ".sigma_client.MAX_ACTION_ATTEMPTS", 5):
            with self.assertRaises(client_module.ActionNotAllowedError):
                await asyncio.to_thread(self.client.perform_partition_action, "stay", "2")
        await asyncio.sleep(0)
        self.assertEqual(reads, 0)  # Initial and preflight states came from fresh control headers.
        self.assertEqual(accesses, 1)
        self.assertEqual(self.panel.logins, 1)
        self.assertEqual(self.panel.commands, [])
        self.assertEqual(self.panel.states["2"], armed)
        self.assertEqual(self.panel.states["1"], other_partition)
        self.assertEqual(self.coordinator.updates, [])
        self.assertFalse(self.coordinator.lock.locked())

    async def test_external_full_arming_on_preflight_panel_blocks_stay_without_command(self):
        original = self.panel.get
        panel_reads = 0
        armed = "\u039f\u03a0\u039b\u0399\u03a3\u039c\u0395\u039d\u039f"
        def get(url, **kwargs):
            nonlocal panel_reads
            if url.endswith("/panel.html"):
                panel_reads += 1
                if panel_reads == 2:
                    self.panel.states["2"] = armed
            return original(url, **kwargs)
        self.panel.get = get
        with patch(PKG + ".sigma_client.MAX_ACTION_ATTEMPTS", 5):
            with self.assertRaises(client_module.ActionNotAllowedError):
                await asyncio.to_thread(self.client.perform_partition_action, "stay", "2")
        await asyncio.sleep(0)
        self.assertEqual(panel_reads, 2)
        self.assertEqual(self.panel.commands, [])
        self.assertEqual(self.coordinator.updates, [])
        self.assertFalse(self.coordinator.lock.locked())

    def test_control_parser_never_claims_zone_rows_from_panel_or_access(self):
        # The same valid rows are useful only when fetched from the actual zones endpoint.
        soup = BeautifulSoup(html("2"), "html.parser")
        with self.client.partition_scope("2"):
            control = self.client._parse_control_status(soup)
            zones = self.client._parse_control_status(soup, zones_page=True)
        self.assertEqual(self.client.parse_alarm_status(control["alarm_status"])[0], "Disarmed")
        self.assertEqual(control["zones"], [])
        self.assertTrue(zones["zones"])


    async def test_valid_control_headers_do_not_request_wrong_unused_zones(self):
        self.panel.wrong_zones = True
        with patch.object(self.panel, "get", wraps=self.panel.get) as calls:
            self.assertTrue(await asyncio.to_thread(self.client.perform_partition_action, "arm", "2"))
        await asyncio.sleep(0)
        self.assertEqual(self.panel.commands, [("2", "arm")])
        self.assertFalse(any(call.args[0].endswith("/zones.html") for call in calls.call_args_list))
        self.assertFalse(self.coordinator.lock.locked())

    async def test_concurrent_actions_are_serialized(self):
        results = await asyncio.gather(
            asyncio.to_thread(self.client.perform_partition_action, "arm", "1"),
            asyncio.to_thread(self.client.perform_partition_action, "stay", "2"),
        )
        self.assertTrue(all(results))
        self.assertCountEqual(self.panel.commands, [("1", "arm"), ("2", "stay")])
        self.assertFalse(self.coordinator.lock.locked())

    def test_analytics_does_not_send_partition_pins(self):
        with patch(PKG + ".sigma_client.requests.post") as post:
            base_module.post_installation_analytics("http://alarm.example", {"pin": "PIN_SECRET_ONE", "partition_2_pin": "PIN_SECRET_TWO", "update_interval": 60})
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("PIN_SECRET", str(payload))
        self.assertEqual(payload["config"]["update_interval"], 60)

    def test_greek_and_mixed_status_variants(self):
        cases = [
            ("ΑΦΟΠΛΙΣΜΕΝΟ", ("Disarmed", None)),
            ("ΟΠΛΙΣΜΕΝΟ", ("Armed", False)),
            ("ΟΠΛΙΣΜΕΝΟ ΜΕ ΖΩΝΕΣ BYPASS", ("Armed", True)),
            ("ΠΕΡΙΜΕΤΡΙΚΗ ΟΠΛΙΣΗ", ("Armed Perimeter", False)),
            ("ΠΕΡΙΜΕΤΡΙΚΗ ΟΠΛΙΣΗ ΜΕ ΖΩΝΕΣ BYPASS", ("Armed Perimeter", True)),
            ("  Όπλισμένο   με  ζώνες BYPASS ", ("Armed", True)),
            ("OΠΛIΣMENO", ("Armed", False)),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(self.client.parse_alarm_status(raw), expected)

    def test_unknown_state_reports_which_field_failed(self):
        with self.client.partition_scope("2"):
            with self.assertRaisesRegex(ValueError, "invalid alarm_status"):
                self.client.parse_zones_html(BeautifulSoup(html("2", state="UNRECOGNIZED STATE"), "html.parser"))

    def test_missing_battery_does_not_invalidate_alarm(self):
        with self.client.partition_scope("2"):
            page = html("2").replace("<div>Μπαταρία: 13.7 Volt</div>", "")
            result = self.client.parse_zones_html(BeautifulSoup(page, "html.parser"))
            self.assertIsNone(result["battery_volt"])
            self.assertTrue(result["zones"])

    def test_comma_decimal_battery_and_greek_no(self):
        with self.client.partition_scope("2"):
            result = self.client.parse_zones_html(BeautifulSoup(html("2").replace("13.7", "13,7"), "html.parser"))
            self.assertEqual(result["battery_volt"], 13.7)
            self.assertIs(self.client._to_bool("ΟΧΙ"), False)

    def test_missing_zones_reports_field(self):
        with self.client.partition_scope("2"):
            with self.assertRaisesRegex(ValueError, "invalid zones"):
                self.client.parse_zones_html(BeautifulSoup(html("2", kind="panel"), "html.parser"))

    def test_switch_resets_server_that_ignores_pin_changes(self):
        original = self.panel.post
        authenticated_part = None
        def post(url, data, **kwargs):
            nonlocal authenticated_part
            if url.endswith("login.html"):
                authenticated_part = None
            if url.endswith("ucode") and authenticated_part is not None:
                return Response(html(authenticated_part, "panel"))
            response = original(url, data, **kwargs)
            if url.endswith("ucode"):
                authenticated_part = self.panel.part
            return response
        self.panel.post = post
        self.client.safe_get_partition_status("1")
        self.client.safe_get_partition_status("2")
        self.assertEqual(self.panel.part, "2")
        self.assertEqual(self.panel.logins, 2)

    def test_greek_armed_status_does_not_cause_endless_failure(self):
        self.panel.states["2"] = "ΟΠΛΙΣΜΕΝΟ ΜΕ ΖΩΝΕΣ BYPASS"
        for part in ("1", "2", "2", "1", "1", "2"):
            result = self.client.safe_get_partition_status(part)
            self.assertIsNotNone(self.client.parse_alarm_status(result["alarm_status"])[0])


    def test_auto_armed_variants(self):
        for raw in ("ΟΠΛΙΣΜΕΝΟ AUTO", "OΠΛIΣMENO AUTO", "  Όπλισμένο auto  "):
            with self.subTest(raw=raw):
                self.assertEqual(self.client.parse_alarm_status(raw), ("Armed", None))

    def test_auto_bypass_is_read_from_zones(self):
        resolve = self.client.resolve_bypass
        self.assertIs(resolve("Armed", None, [{"bypass": "ΟΧΙ"}]), False)
        self.assertIs(resolve("Armed", None, [{"bypass": "NAI"}]), True)
        self.assertIsNone(resolve("Armed", None, [{"bypass": "unknown"}]))
        self.assertIsNone(resolve("Armed", None, []))
        self.assertIs(resolve("Armed", None, [{"bypass": False}]), False)

    def test_auto_unknown_state_still_rejected(self):
        self.assertEqual(self.client.parse_alarm_status("UNRECOGNIZED AUTO"), (None, None))

    async def test_automatic_arming_then_remote_disarming(self):
        self.panel.states["2"] = "ΟΠΛΙΣΜΕΝΟ AUTO"
        result = self.client.safe_get_partition_status("2")
        self.assertEqual(self.client.parse_alarm_status(result["alarm_status"])[0], "Armed")
        self.assertTrue(await asyncio.to_thread(self.client.perform_partition_action, "disarm", "2"))
        await asyncio.sleep(0)
        self.assertEqual(self.coordinator.updates[-1][1]["status"], "Disarmed")
        self.assertEqual(self.panel.commands, [("2", "disarm")])


    def test_missing_zones_recovers_with_bounded_rereads(self):
        original = self.panel.get
        reads = 0
        selections = 0
        def get(url, **kwargs):
            nonlocal reads, selections
            if url.endswith("/panel.html"):
                selections += 1
            if url.endswith("/zones.html"):
                reads += 1
                if reads <= 2:
                    return Response(html(self.panel.part, kind="panel"))
            return original(url, **kwargs)
        self.panel.get = get
        data = self.client.safe_get_partition_status("2")
        self.assertTrue(data["zones"])
        self.assertEqual(reads, 3)
        self.assertEqual(selections, 1)
        self.assertEqual(self.panel.logins, 1)
        self.assertEqual(self.panel.commands, [])
        self.assertEqual(base_module.time.sleep.call_args_list[-2:],
                         [unittest.mock.call(1.0), unittest.mock.call(2.0)])

    def test_persistent_missing_zones_stops_after_two_batches(self):
        original = self.panel.get
        reads = 0
        def get(url, **kwargs):
            nonlocal reads
            if url.endswith("/zones.html"):
                reads += 1
                return Response(html(self.panel.part, kind="panel"))
            return original(url, **kwargs)
        self.panel.get = get
        with self.assertRaises(client_module.MissingZonesError):
            self.client.safe_get_partition_status("2")
        self.assertEqual(reads, 6)
        self.assertEqual(self.panel.logins, 2)
        self.assertEqual(self.panel.commands, [])

    def test_wrong_partition_is_not_treated_as_missing_zones(self):
        original = self.panel.get
        reads = 0
        def get(url, **kwargs):
            nonlocal reads
            if url.endswith("/zones.html"):
                reads += 1
                return Response(html("1", kind="panel"))
            return original(url, **kwargs)
        self.panel.get = get
        with self.assertRaisesRegex(ValueError, "did not confirm partition 2"):
            self.client.safe_get_partition_status("2")
        self.assertEqual(reads, 1)
        self.assertEqual(self.panel.commands, [])

    def test_unknown_status_without_zones_is_not_retried_as_empty_zones(self):
        original = self.panel.get
        reads = 0
        def get(url, **kwargs):
            nonlocal reads
            if url.endswith("/zones.html"):
                reads += 1
                return Response(html("2", kind="panel", state="UNRECOGNIZED STATE"))
            return original(url, **kwargs)
        self.panel.get = get
        with self.assertRaisesRegex(ValueError, "invalid alarm_status,zones"):
            self.client.safe_get_partition_status("2")
        self.assertEqual(reads, 1)







    def test_waits_for_ready_table_without_resetting_selection(self):
        original = self.panel.get
        clock = 0.0
        ready_at = 0.0
        events = []
        def sleep(seconds):
            nonlocal clock
            clock += seconds
            events.append(("wait", seconds))
        base_module.time.sleep.side_effect = sleep
        def get(url, **kwargs):
            nonlocal ready_at
            if url.endswith("/panel.html"):
                ready_at = clock + 2.0
                events.append(("select", self.panel.part))
            if url.endswith("/zones.html"):
                events.append(("zones", self.panel.part))
                if clock < ready_at:
                    return Response(html(self.panel.part, kind="panel"), "zones.html")
            return original(url, **kwargs)
        self.panel.get = get
        data = self.client.safe_get_partition_status("2")
        self.assertTrue(data["zones"])
        self.assertEqual(events, [("select", "2"), ("wait", 1.0), ("zones", "2"),
                                  ("wait", 1.0), ("zones", "2")])
        self.assertEqual(self.panel.logins, 1)
        self.assertEqual(self.panel.commands, [])

    def test_reconnects_once_after_empty_batch_then_recovers(self):
        original = self.panel.get
        reads = 0
        selections = 0
        def get(url, **kwargs):
            nonlocal reads, selections
            if url.endswith("/panel.html"):
                selections += 1
            if url.endswith("/zones.html"):
                reads += 1
                if self.panel.logins == 1:
                    return Response(html(self.panel.part, kind="panel"), "zones.html")
            return original(url, **kwargs)
        self.panel.get = get
        self.assertTrue(self.client.safe_get_partition_status("2")["zones"])
        self.assertEqual(reads, 4)
        self.assertEqual(selections, 2)
        self.assertEqual(self.panel.logins, 2)
        self.assertEqual(self.panel.commands, [])

    def test_wrong_partition_during_zones_retry_is_rejected(self):
        original = self.panel.get
        reads = 0
        def get(url, **kwargs):
            nonlocal reads
            if url.endswith("/zones.html"):
                reads += 1
                return Response(html("2" if reads == 1 else "1", kind="panel"), "zones.html")
            return original(url, **kwargs)
        self.panel.get = get
        with self.assertRaisesRegex(ValueError, "did not confirm partition 2"):
            self.client.safe_get_partition_status("2")
        self.assertEqual(reads, 2)
        self.assertEqual(self.panel.logins, 1)
        self.assertEqual(self.panel.commands, [])

    def test_unknown_status_during_retry_is_rejected(self):
        original = self.panel.get
        reads = 0
        def get(url, **kwargs):
            nonlocal reads
            if url.endswith("/zones.html"):
                reads += 1
                return Response(html("2", kind="panel", state="AΦOΠΛIΣMENO" if reads == 1 else "UNRECOGNIZED"), "zones.html")
            return original(url, **kwargs)
        self.panel.get = get
        with self.assertRaisesRegex(ValueError, "invalid alarm_status,zones"):
            self.client.safe_get_partition_status("2")
        self.assertEqual(reads, 2)
        self.assertEqual(self.panel.logins, 1)

    def test_expired_session_during_retry_gets_one_fresh_login(self):
        original = self.panel.get
        reads = 0
        def get(url, **kwargs):
            nonlocal reads
            if url.endswith("/zones.html"):
                reads += 1
                if reads == 1:
                    return Response(html("2", kind="panel"), "zones.html")
                if reads == 2:
                    self.panel.auth = False
                    return Response('<input name="gen_input" value="example">', "login.html")
            return original(url, **kwargs)
        self.panel.get = get
        self.assertTrue(self.client.safe_get_partition_status("2")["zones"])
        self.assertEqual(reads, 3)
        self.assertEqual(self.panel.logins, 2)
        self.assertEqual(self.panel.commands, [])

    def test_analytics_still_sends_once_after_zone_recovery(self):
        original = self.panel.get
        reads = 0
        def get(url, **kwargs):
            nonlocal reads
            if url.endswith("/zones.html"):
                reads += 1
                if reads == 1:
                    return Response(html("2", kind="panel"), "zones.html")
            return original(url, **kwargs)
        self.panel.get = get
        self.client._send_analytics = True
        self.client._config.update({"pin": "SECRET_ONE", "partition_2_pin": "SECRET_TWO"})
        with patch(PKG + ".sigma_client.requests.post") as post:
            self.client.safe_get_partition_status("2")
            self.client.safe_get_partition_status("2")
        post.assert_called_once()
        self.assertNotIn("SECRET", str(post.call_args.kwargs["json"]))















if __name__ == "__main__":
    unittest.main(verbosity=2)
