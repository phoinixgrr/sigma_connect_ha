"""Optional PIN-scoped partitions; use the original client for one partition."""
from contextlib import contextmanager
import re
import threading
import unicodedata
import logging
import time
from urllib.parse import urlsplit
import requests
import asyncio
from bs4 import BeautifulSoup
from . import sigma_client

from .sigma_client import SigmaClient

_LOGGER = logging.getLogger(__name__)


class ActionNotAllowedError(ValueError):
    """The requested transition requires an explicit disarm first."""


class MissingZonesError(ValueError):
    """The correct partition returned a valid status but no readable zones."""


class ZonesSessionExpiredError(ValueError):
    """The zones request returned the login form."""


class SelectionSessionExpiredError(ValueError):
    """Partition selection returned a login form, including after reauthentication."""


class PartitionClient(SigmaClient):
    """Serialize PIN-scoped access and discard session state when switching PIN."""

    def __init__(self, *args, second_pin, **kwargs):
        super().__init__(*args, **kwargs)
        self.partition_pins = {"1": self.pin, "2": second_pin.strip()}
        self._context = threading.local()
        self._active_partition = None
        self._paths = {}


    @property
    def target_partition(self):
        return getattr(self._context, "partition", "1")

    @contextmanager
    def partition_scope(self, partition):
        partition = str(partition)
        if partition not in self.partition_pins:
            raise ValueError("Partition is not configured")
        previous = self.target_partition
        self._context.partition = partition
        try:
            yield
        finally:
            self._context.partition = previous

    def logout(self):
        self._active_partition = None
        super().logout()

    def login(self):
        self.pin = self.partition_pins[self.target_partition]
        self._active_partition = None
        super().login()
        self._active_partition = self.target_partition


    @staticmethod
    def partition_number(soup):
        match = re.search(r"Τμήμα\s*(\d+)\s*(?:\([^)]*\))?\s*:",
                          soup.get_text(" ", strip=True))
        return match.group(1) if match else None

    @staticmethod
    def is_login_page(soup):
        return soup.find("input", {"name": "gen_input"}) is not None

    def _select_once(self):
        if not self._session_authenticated:
            self.login()
        elif self._active_partition != self.target_partition:
            # Some Ixion versions accept ucode but keep the previous partition.
            self.logout()
            self.login()
        panel = self._get_soup("panel.html")
        number = self.partition_number(panel)
        if number is not None:
            if number != self.target_partition:
                selected = super().select_partition(self.target_partition)
                selected_number = self.partition_number(selected)
                if self.is_login_page(selected):
                    raise SelectionSessionExpiredError(f"Requested partition {self.target_partition}; panel returned {selected_number or 'no partition'}")
                if selected_number not in (None, self.target_partition):
                    raise ValueError(f"Requested partition {self.target_partition}; panel returned {selected_number or 'no partition'}")
                return selected
            # PIN-scoped Aeolus pages do not need part.cgi.
            return panel
        if self.is_login_page(panel):
            raise SelectionSessionExpiredError("Ixion session expired")
        # Preserve the original explicit partition-selection flow on other firmware.
        selected = super().select_partition(self.target_partition)
        if self.is_login_page(selected):
            raise SelectionSessionExpiredError("Ixion session expired during partition selection")
        number = self.partition_number(selected)
        if number is not None and number != self.target_partition:
            raise ValueError("Panel returned a different partition")
        return selected

    def select_partition(self, part_id=None):
        """Recover once from PIN rejection, expired sessions or wrong selection."""
        try:
            return self._select_once()
        except Exception as error:
            self.logout()
            self.login()
            return self._select_once()

    def parse_zones_html(self, soup):
        # Validate the authoritative zones page even when selection pages lack a header.
        if self.partition_number(soup) != self.target_partition:
            raise ValueError(f"Status page did not confirm partition {self.target_partition}")
        try:
            data = super().parse_zones_html(soup)
        except Exception:
            raise
        if data.get("battery_volt") is None:
            match = re.search(r"Μπαταρία\s*:\s*([\d.,]+)\s*Volt", soup.get_text(" ", strip=True), re.I)
            if match:
                data["battery_volt"] = float(match.group(1).replace(",", "."))
        try:
            self.validate_status(data)
        except MissingZonesError:
            raise
        except ValueError:
            raise
        return data

    @staticmethod
    def normalize_status(value):
        # Ixion mixes Latin and Greek lookalikes; accented Greek also occurs.
        text = unicodedata.normalize("NFD", str(value or "").upper())
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.translate(str.maketrans("ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ", "ABEZHIKMNOPTYX"))
        return " ".join(text.split())

    def parse_alarm_status(self, raw_status):
        normalized = self.normalize_status(raw_status)
        tokens = normalized.split()
        automatic = "AUTO" in tokens
        if automatic:
            normalized = " ".join(token for token in tokens if token != "AUTO")
        for source in (
            "AΦOΠΛIΣMENO", "OΠΛIΣMENO ME ZΩNEΣ BYPASS", "OΠΛIΣMENO",
            "ΠEPIMETPIKH OΠΛIΣH ME ZΩNEΣ BYPASS", "ΠEPIMETPIKH OΠΛIΣH",
        ):
            if normalized == self.normalize_status(source):
                state, bypass = super().parse_alarm_status(source)
                # AUTO describes the arming origin, not which zones were bypassed.
                if automatic and bypass is False:
                    bypass = None
                return state, bypass
        return None, None

    @staticmethod
    def resolve_bypass(state, bypass, zones):
        if bypass is not None or state not in ("Armed", "Armed Perimeter"):
            return bypass
        flags = [PartitionClient._to_bool(zone.get("bypass")) for zone in zones]
        if any(flag is True for flag in flags):
            return True
        if flags and all(flag is False for flag in flags):
            return False
        return None

    @staticmethod
    def _to_bool(value):
        if isinstance(value, bool):
            return value
        return SigmaClient._to_bool(PartitionClient.normalize_status(value))

    def validate_status(self, data):
        missing = []
        if self.parse_alarm_status(data.get("alarm_status"))[0] is None:
            missing.append("alarm_status")
        if not data.get("zones"):
            missing.append("zones")
        if missing == ["zones"]:
            raise MissingZonesError(
                f"Partition {self.target_partition}: invalid zones; "
                f"alarm_status={str(data.get('alarm_status'))[:100]!r}"
            )
        if missing:
            status = str(data.get("alarm_status") or "missing")[:100]
            raise ValueError(f"Partition {self.target_partition}: invalid {','.join(missing)}; alarm_status={status!r}")

    def _read_selected_zones(self):
        """Allow the panel to settle; repeated reads never reselect the partition."""
        time.sleep(1.0)
        for attempt in range(3):
            try:
                soup = self._get_soup("zones.html")
                if self.is_login_page(soup):
                    raise ZonesSessionExpiredError("Ixion session expired while reading zones")
                # Every response still has to confirm the target partition.
                data = self.parse_zones_html(soup)
                if attempt:
                    _LOGGER.debug("Partition %s zones recovered on read %s/3",
                                  self.target_partition, attempt + 1)
                return data
            except MissingZonesError:
                if attempt == 2:
                    raise
                _LOGGER.debug("Partition %s returned no zones; rereading only zones.html (%s/3)",
                              self.target_partition, attempt + 2)
                time.sleep((1.0, 2.0)[attempt])

    def try_zones_directly(self):
        """Return validated data or raise; do not trigger a third upstream login."""
        for recovery in range(2):
            try:
                self.select_partition()
                return self._read_selected_zones()
            except (MissingZonesError, ZonesSessionExpiredError, requests.RequestException) as error:
                if recovery:
                    raise
                _LOGGER.debug("Partition %s: one fresh login before final zones-read batch",
                              self.target_partition)
                self.logout()
                self.login()


    def _parse_control_status(self, soup, zones_page=False):
        """Require a fresh target/state, independently of the zone table's readiness."""
        if self.is_login_page(soup):
            raise SelectionSessionExpiredError("Ixion requested authentication for control state")
        if self.partition_number(soup) != self.target_partition:
            raise ValueError(f"Control page did not confirm partition {self.target_partition}")
        data = SigmaClient.parse_zones_html(self, soup)
        if self.parse_alarm_status(data.get("alarm_status"))[0] is None:
            raise ValueError("Control page returned an unrecognized alarm state")
        # Tables on panel/access pages cannot establish fresh zone measurements.
        if not zones_page:
            data["zones"] = []
        return data

    def _publish_already_desired_status(self, raw):
        self._publish_action_status(raw)

    def _read_action_status(self):
        panel = self.select_partition()
        if self.partition_number(panel) is not None:
            return self._parse_control_status(panel)
        return self._parse_control_status(self._get_soup("zones.html"), zones_page=True)

    def _validate_action_state(self, action, data):
        state, _ = self.parse_alarm_status(data.get("alarm_status"))
        if action == "stay" and state == "Armed":
            raise ActionNotAllowedError(
                "Cannot change a fully armed partition to perimeter mode directly. "
                "Disarm it manually first when you want to change mode."
            )

    def _prepare_action(self, action):
        return self._action_paths(action=action)

    def _action_paths(self, action=None):
        """Read control links only for optional multi-partition operation."""
        panel = self.select_partition()
        direct = self.partition_number(panel) == self.target_partition
        soup = self._get_soup("access.html")
        number = self.partition_number(soup)
        if self.is_login_page(soup) or number is not None:
            data = self._parse_control_status(soup)
        else:
            data = self._parse_control_status(self._get_soup("zones.html"), zones_page=True)
        if action is not None:
            self._validate_action_state(action, data)
        paths = {"arm": "arm.html", "disarm": "disarm.html", "stay": "stay.html"}
        if direct:
            # Known PIN-scoped Aeolus firmware uses extensionless control paths.
            paths = {action: action for action in paths}
        for link in soup.find_all("a", href=True):
            url = urlsplit(link["href"])
            if url.netloc and url.netloc != urlsplit(self.base_url).netloc:
                continue
            if url.scheme and url.scheme != urlsplit(self.base_url).scheme:
                continue
            for action in paths:
                if url.path.lstrip("/") in (action, action + ".html") and not url.query and not url.fragment:
                    paths[action] = url.path.lstrip("/")
        self._paths[self.target_partition] = paths
        return paths

    def perform_partition_action(self, action, partition):
        with self.partition_scope(partition):
            # The optional action flow owns the shared lock, retries and polls.
            return self.perform_action(action)

    def _publish_action_data(self, data):
        data = dict(data)
        data["zones_available"] = bool(data.get("zones"))
        data["zones_bypassed"] = self.resolve_bypass(
            data.get("status"), data.get("zones_bypassed"), data.get("zones", [])
        )
        partition = self.target_partition
        self.coordinator.hass.loop.call_soon_threadsafe(
            self.coordinator.async_set_partition_data, partition, data
        )

    @sigma_client.retry_html_request
    def _get_soup(self, path):
        response = self.session.get(f"{self.base_url}/{path.lstrip('/')}", timeout=5)
        response.raise_for_status()
        return self._response_soup(response)

    def _response_soup(self, response):
        return BeautifulSoup(response.text, "html.parser")

    def safe_get_partition_status(self, partition):
        with self.partition_scope(partition):
            return super().safe_get_status()

    def _publish_action_status(self, raw):
        parsed, bypass = self.parse_alarm_status(raw.get("alarm_status"))
        self._publish_action_data({
            "status": parsed, "zones_bypassed": bypass,
            "battery_volt": raw.get("battery_volt"), "ac_power": raw.get("ac_power"),
            "zones": [{**zone, "status": self._to_openclosed(zone.get("status")),
                       "bypass": self._to_bool(zone.get("bypass"))}
                      for zone in raw.get("zones", [])],
        })

    def perform_action(self, action: str) -> bool:
        action_map = {"arm": "arm.html", "disarm": "disarm.html", "stay": "stay.html"}
        desired_map = {"arm": "Armed", "disarm": "Disarmed", "stay": "Armed Perimeter"}

        if action not in action_map:
            _LOGGER.error("Invalid action %r", action)
            return False

        _LOGGER.debug(f"[LOCK] Waiting to perform '{action}'")
        try:
            asyncio.run_coroutine_threadsafe(
                self._acquire_action_lock(),
                self.coordinator.hass.loop
            ).result()
        except TimeoutError:
            _LOGGER.error("[ACTION] Timed out waiting for lock after %ss, aborting '%s'", self.ACTION_LOCK_TIMEOUT, action)
            return False
        except Exception as exc:
            _LOGGER.error("[ACTION] Failed to acquire lock for '%s': %s", action, exc)
            return False
        _LOGGER.debug(f"[LOCK] Acquired for '{action}'")

        try:
            # Brief delay to let panel settle after any recent coordinator activity
            time.sleep(0.25)

            desired = desired_map[action]

            # 1) If already desired, exit fast (non-fatal if check fails)
            current_status = None
            try:
                initial_data = self._read_action_status()
                self._validate_action_state(action, initial_data)
                current_status, _ = self.parse_alarm_status(initial_data.get("alarm_status"))
                _LOGGER.debug(f"[ACTION] Current status before '{action}': {current_status}")
                if current_status == desired:
                    _LOGGER.info(f"[ACTION] Alarm already in desired state ({desired})")
                    self._publish_already_desired_status(initial_data)
                    return True
            except ActionNotAllowedError:
                raise
            except Exception as exc:
                _LOGGER.warning("[ACTION] Initial status check failed, proceeding to action: %s", exc)

            # 2) Try action with retries
            last_seen_state = current_status
            last_raw_status = None
            total_polls = 0

            for attempt in range(1, sigma_client.MAX_ACTION_ATTEMPTS + 1):
                try:
                    # Re-login on retry to get a fresh session
                    if attempt > 1:
                        _LOGGER.info("[ACTION] Re-authenticating before attempt %d", attempt)
                        self.logout()
                        self.login()

                    # Preparation validates the selected partition immediately before triggering.
                    action_map = self._prepare_action(action)

                    # Trigger action
                    _LOGGER.debug(f"[ACTION] Attempt {attempt}/{sigma_client.MAX_ACTION_ATTEMPTS} - Triggering '{action}' on panel.")
                    action_resp = self.session.get(
                        f"{self.base_url}/{action_map[action]}",
                        headers={"Referer": f"{self.base_url}/part.cgi"},
                        timeout=5,
                    )
                    action_resp.raise_for_status()

                    # Detect session expiration from response
                    if "gen_input" in action_resp.text or "login" in action_resp.url:
                        _LOGGER.warning("[ACTION] Session expired on attempt %d, re-authenticating", attempt)
                        _LOGGER.debug("[ACTION-DIAG] Expired response snippet: %.500s", action_resp.text[:500])
                        self.logout()
                        self.login()
                        action_map = self._prepare_action(action)
                        action_resp = self.session.get(
                            f"{self.base_url}/{action_map[action]}",
                            headers={"Referer": f"{self.base_url}/part.cgi"},
                            timeout=5,
                        )
                        action_resp.raise_for_status()

                    _LOGGER.debug("[ACTION-DIAG] Response status code: %d", action_resp.status_code)
                    _LOGGER.debug("[ACTION-DIAG] Final URL (after redirects): %s", action_resp.url)
                    _LOGGER.debug("[ACTION-DIAG] Response snippet: %.500s", action_resp.text[:500])
                except ActionNotAllowedError:
                    raise
                except Exception as exc:
                    _LOGGER.warning("[ACTION] Attempt %d trigger failed: %s", attempt, exc)
                    if attempt < sigma_client.MAX_ACTION_ATTEMPTS:
                        time.sleep(0.5)
                        continue
                    break

                # Poll for state change (limited polls per attempt)
                for poll in range(1, self.POLLS_PER_ATTEMPT + 1):
                    total_polls += 1
                    try:
                        parsed_zones = self._read_action_status()
                        raw_alarm_status = parsed_zones.get("alarm_status")
                        new_state, _ = self.parse_alarm_status(raw_alarm_status)
                        last_seen_state = new_state
                        last_raw_status = raw_alarm_status

                        _LOGGER.debug(f"[ACTION] Attempt {attempt}, Poll {poll} - Status: {new_state}")

                        if new_state == desired:
                            _LOGGER.info(f"[ACTION SUCCESS] '{action}' reached {desired} (attempt {attempt}, poll {poll})")

                            # Use the validated read, including header-only optional control state.
                            self._publish_action_status(parsed_zones)

                            return True
                    except Exception as e:
                        _LOGGER.debug("[ACTION] Poll error (will retry): %s", e)

                    time.sleep(0.5)

                # State didn't change after POLLS_PER_ATTEMPT polls
                if attempt < sigma_client.MAX_ACTION_ATTEMPTS:
                    _LOGGER.warning(f"[ACTION] Attempt {attempt} failed, state still '{last_seen_state}', retrying...")
                    time.sleep(0.5)  # Brief pause before retry

            _LOGGER.error(f"[ACTION FAILED] '{action}' did not reach '{desired}' after {sigma_client.MAX_ACTION_ATTEMPTS} attempts")
            _LOGGER.error(f"[ACTION-DIAG] FAILURE SUMMARY: last_seen_state='{last_seen_state}', last_raw_status='{last_raw_status}', total_polls={total_polls}")
            return False

        finally:
            self.coordinator.hass.loop.call_soon_threadsafe(self.coordinator.lock.release)
            _LOGGER.debug(f"[LOCK] Released for '{action}'")
