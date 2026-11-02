"""Coordinator/entity regressions for the optional second partition."""
import asyncio
import gc
import weakref
from copy import deepcopy
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from homeassistant.exceptions import HomeAssistantError
from homeassistant.components.alarm_control_panel import AlarmControlPanelState

from custom_components.sigma_connect_ha.coordinator import SigmaCoordinator
import custom_components.sigma_connect_ha.coordinator as coordinator_module
from custom_components.sigma_connect_ha.alarm_control_panel import PartitionAlarmPanel as SigmaAlarmPanel
from custom_components.sigma_connect_ha.sensor import PartitionSensor as SigmaSensor


def raw_status():
    return {"alarm_status": "AΦOΠΛIΣMENO", "battery_volt": 13.7, "ac_power": True,
            "zones": [{"zone": "21", "description": "Example", "status": "κλειστή", "bypass": "OXI"}]}


@pytest.fixture
def dual(mock_hass, mock_config_entry):
    mock_config_entry.options["partition_2_pin"] = "2222"
    with patch("homeassistant.helpers.frame.report_usage", create=True), \
         patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient"), \
         patch("custom_components.sigma_connect_ha.coordinator.PartitionClient") as cls:
        client = cls.return_value
        client._active_partition = "1"
        client.safe_get_partition_status.side_effect = lambda part: raw_status()
        client.parse_alarm_status.return_value = ("Disarmed", None)
        client._to_openclosed.return_value = "Closed"
        client._to_bool.return_value = False
        coordinator = SigmaCoordinator(mock_hass, mock_config_entry)
        yield coordinator, client, mock_config_entry


def test_single_partition_keeps_original_client(mock_hass, mock_config_entry):
    with patch("homeassistant.helpers.frame.report_usage", create=True), \
         patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient") as old, \
         patch("custom_components.sigma_connect_ha.coordinator.PartitionClient") as new:
        c = SigmaCoordinator(mock_hass, mock_config_entry)
        assert c.client is old.return_value
        assert c.partition_ids == ("1",)
        new.assert_not_called()


def test_dual_fetch_order_starts_with_active_partition(dual):
    c, client, _ = dual
    client._active_partition = "2"
    result = c._fetch()
    assert [call.args[0] for call in client.safe_get_partition_status.call_args_list] == ["2", "1"]
    assert result["status"] == result["partition_2"]["status"] == "Disarmed"
    assert result["zones_available"] is True
    assert result["partition_2"]["zones_available"] is True


def test_transient_second_failure_uses_grace_period_then_marks_unavailable(dual):
    c, client, _ = dual
    c._last_data = c._fetch()
    def get(part):
        if part == "2":
            raise ConnectionError("simulated session failure")
        return raw_status()
    client.safe_get_partition_status.side_effect = get
    for _ in range(c.max_consecutive_failures - 1):
        result = c._fetch()
        assert result["partition_2"]["status"] == "Disarmed"
    result = c._fetch()
    assert result["partition_2"]["available"] is False
    assert result["status"] == "Disarmed"
    client.safe_get_partition_status.side_effect = lambda part: raw_status()
    assert c._fetch()["partition_2"]["status"] == "Disarmed"
    assert c._partition_failures["2"] == 0


def test_first_failure_does_not_hide_fresh_second_partition(dual):
    c, client, _ = dual
    c._last_data = c._fetch()
    c._partition_failures["1"] = c.max_consecutive_failures - 1
    client.safe_get_partition_status.side_effect = lambda part: raw_status() if part == "2" else (_ for _ in ()).throw(ConnectionError("simulated"))
    result = c._fetch()
    assert result["available"] is False
    assert result["partition_2"]["status"] == "Disarmed"


def test_action_publish_preserves_other_partition(dual):
    c, _, _ = dual
    c.data = c._fetch()
    before = dict(c.data)
    c.async_set_updated_data = MagicMock()
    c.async_set_partition_data("2", {"status": "Armed", "zones": []})
    updated = c.async_set_updated_data.call_args.args[0]
    assert updated["status"] == before["status"]
    assert updated["partition_2"]["status"] == "Armed"
    c.data = updated
    c.async_set_partition_data("1", {"status": "Armed Perimeter", "zones": []})
    assert c.async_set_updated_data.call_args.args[0]["partition_2"]["status"] == "Armed"


@pytest.mark.asyncio
async def test_second_entity_calls_partition_aware_upstream_action(dual):
    c, client, entry = dual
    entities, refresh_tasks = prepare_action_entities(c, entry)
    entity = entities[1]
    c.hass.async_add_executor_job = AsyncMock(return_value=True)
    await entity.async_alarm_arm_away()
    await finish_action_tasks([], refresh_tasks)
    c.hass.async_add_executor_job.assert_awaited_once_with(client.perform_partition_action, "arm", "2")
    assert c._action_in_progress is False
    c.async_request_refresh.assert_awaited_once()



def prepare_action_entities(coordinator, entry):
    """Run entity background refreshes on this test's active event loop."""
    refresh_tasks = []

    def schedule(coro):
        task = asyncio.create_task(coro)
        refresh_tasks.append(task)
        task.add_done_callback(lambda done: refresh_tasks.remove(done))
        return task

    coordinator.hass.async_create_task = MagicMock(side_effect=schedule)
    coordinator.async_request_refresh = AsyncMock()
    entities = [SigmaAlarmPanel(coordinator, entry, part) for part in ("1", "2")]
    for entity in entities:
        entity.hass = coordinator.hass
    return entities, refresh_tasks


async def finish_action_tasks(executor_tasks, refresh_tasks):
    """Drain shielded workers before refreshes, including assertion failures."""
    if executor_tasks:
        await asyncio.wait_for(
            asyncio.gather(*executor_tasks, return_exceptions=True), timeout=1
        )
    if refresh_tasks:
        await asyncio.wait_for(
            asyncio.gather(*refresh_tasks, return_exceptions=True), timeout=1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("second_partition", ["1", "2"])
async def test_busy_command_is_rejected_before_executor_across_shared_entities(
    dual, second_partition
):
    c, client, entry = dual
    entities, refresh_tasks = prepare_action_entities(c, entry)
    first_entity = entities[1]
    second_entity = entities[int(second_partition) - 1]
    started, release = asyncio.Event(), asyncio.Event()
    executor_tasks = []

    async def executor(func, *args):
        executor_tasks.append(asyncio.current_task())
        started.set()
        await release.wait()
        return True

    c.hass.async_add_executor_job = AsyncMock(side_effect=executor)
    first = asyncio.create_task(first_entity.async_alarm_arm_home())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert c._action_in_progress is True
        with pytest.raises(HomeAssistantError, match="command is in progress"):
            await asyncio.wait_for(second_entity.async_alarm_disarm(), timeout=0.2)
        c.hass.async_add_executor_job.assert_awaited_once_with(
            client.perform_partition_action, "stay", "2"
        )
        assert c._action_in_progress is True
        c.async_request_refresh.assert_not_called()
    finally:
        release.set()
        await asyncio.wait_for(first, timeout=1)
        await finish_action_tasks(executor_tasks, refresh_tasks)

    assert c._action_in_progress is False
    c.async_request_refresh.assert_awaited_once()
    # The rejected disarm was not queued for later execution.
    assert c.hass.async_add_executor_job.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "unconfirmed", "transition", "error"])
async def test_action_gate_clears_and_refreshes_after_every_executor_outcome(dual, outcome):
    from custom_components.sigma_connect_ha.partition_client import ActionNotAllowedError

    c, client, entry = dual
    entities, refresh_tasks = prepare_action_entities(c, entry)
    entity = entities[1]
    executor = AsyncMock(return_value=True)
    c.hass.async_add_executor_job = executor
    if outcome == "unconfirmed":
        executor.return_value = False
        error_type, message = None, None
    elif outcome == "transition":
        executor.side_effect = ActionNotAllowedError("Transition requires an explicit panel action")
        error_type, message = HomeAssistantError, "requires an explicit panel action"
    elif outcome == "error":
        executor.side_effect = RuntimeError("simulated executor failure")
        error_type, message = RuntimeError, "simulated executor failure"

    try:
        if outcome in ("success", "unconfirmed"):
            await entity.async_alarm_arm_home()
        else:
            with pytest.raises(error_type, match=message):
                await entity.async_alarm_arm_home()
    finally:
        await finish_action_tasks([], refresh_tasks)

    executor.assert_awaited_once_with(client.perform_partition_action, "stay", "2")
    assert c._action_in_progress is False
    c.async_request_refresh.assert_awaited_once()

    # A completed or failed request must not prevent a later explicit command.
    executor.side_effect = None
    executor.return_value = True
    await entity.async_alarm_disarm()
    await finish_action_tasks([], refresh_tasks)
    assert executor.await_args.args == (client.perform_partition_action, "disarm", "2")
    assert executor.await_count == 2
    assert c._action_in_progress is False
    assert c.async_request_refresh.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_caller_keeps_shared_gate_until_actual_action_finishes(dual):
    c, client, entry = dual
    entities, refresh_tasks = prepare_action_entities(c, entry)
    started, release = asyncio.Event(), asyncio.Event()
    executor_tasks = []

    async def executor(func, *args):
        executor_tasks.append(asyncio.current_task())
        started.set()
        await release.wait()
        return True

    c.hass.async_add_executor_job = AsyncMock(side_effect=executor)
    caller = asyncio.create_task(entities[1].async_alarm_arm_home())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        assert c._action_in_progress is True
        assert not executor_tasks[0].done()
        with pytest.raises(HomeAssistantError, match="command is in progress"):
            await asyncio.wait_for(entities[0].async_alarm_disarm(), timeout=0.2)
        c.hass.async_add_executor_job.assert_awaited_once_with(
            client.perform_partition_action, "stay", "2"
        )
        c.async_request_refresh.assert_not_called()
    finally:
        release.set()
        await finish_action_tasks(executor_tasks, refresh_tasks)
        if not caller.done():
            await asyncio.wait_for(caller, timeout=1)

    assert c._action_in_progress is False
    assert c.hass.async_add_executor_job.await_count == 1
    c.async_request_refresh.assert_awaited_once()



@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["error", "unconfirmed"])
async def test_cancelled_caller_retains_worker_and_consumes_its_late_failure(dual, outcome):
    c, client, entry = dual
    entities, refresh_tasks = prepare_action_entities(c, entry)
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

    async def executor(func, *args):
        started.set()
        try:
            await release.wait()
            if outcome == "error":
                raise RuntimeError("simulated late worker failure")
            return False
        finally:
            finished.set()

    c.hass.async_add_executor_job = AsyncMock(side_effect=executor)
    caller = asyncio.create_task(entities[1].async_alarm_arm_home())
    worker_ref = None
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        worker_ref = weakref.ref(c._action_task)
        assert worker_ref() is not None
        assert not worker_ref().done()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        # Drop the cancelled caller, which can retain its own exception traceback.
        caller = None
        await asyncio.sleep(0)

        assert c._action_in_progress is True
        assert c._action_task is worker_ref()
        assert not worker_ref().done()
        with pytest.raises(HomeAssistantError, match="command is in progress"):
            await asyncio.wait_for(entities[0].async_alarm_disarm(), timeout=0.2)
        c.hass.async_add_executor_job.assert_awaited_once_with(
            client.perform_partition_action, "stay", "2"
        )
        c.async_request_refresh.assert_not_called()

        release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        # Do not await/gather the worker or call its exception(): that would mask
        # a missing production completion callback that must consume late errors.
        for _ in range(3):
            await asyncio.sleep(0)
        await finish_action_tasks([], refresh_tasks)
        assert c._action_in_progress is False
        assert c._action_task is None
        c.async_request_refresh.assert_awaited_once()
        assert c.hass.async_add_executor_job.await_count == 1

        for _ in range(3):
            gc.collect()
            await asyncio.sleep(0)
        assert worker_ref() is None
        assert not [context for context in unhandled
                    if "exception was never retrieved" in context.get("message", "").lower()]
    finally:
        if caller is not None and not caller.done():
            caller.cancel()
            try:
                await caller
            except asyncio.CancelledError:
                pass
        release.set()
        if started.is_set():
            await asyncio.wait_for(finished.wait(), timeout=1)
        for _ in range(3):
            await asyncio.sleep(0)
        await finish_action_tasks([], refresh_tasks)
        gc.collect()
        await asyncio.sleep(0)
        loop.set_exception_handler(previous_handler)



@pytest.mark.parametrize("partition", ["1", "2"])
def test_confirmed_partial_action_survives_full_poll_grace_and_restores_fresh_zones(
    dual, partition
):
    c, client, entry = dual
    c.last_update_success = True
    coordinator_module.PartitionClient.resolve_bypass.side_effect = lambda state, bypass, zones: bypass
    other_partition = "2" if partition == "1" else "1"

    def raw(part, armed=True, zone_open=False):
        return {
            "alarm_status": "OΠΛIΣMENO" if armed else "AΦOΠΛIΣMENO",
            "battery_volt": 13.7, "ac_power": True,
            "zones": [{"zone": "1" if part == "1" else "21", "description": "Example",
                       "status": "ανοικτή" if zone_open else "κλειστή", "bypass": "OXI"}],
        }

    def part_data(data, part):
        if part == "2":
            return data["partition_2"]
        return {key: value for key, value in data.items() if key != "partition_2"}

    client.safe_get_partition_status.side_effect = lambda part: raw(part)
    client.parse_alarm_status.side_effect = lambda value: (
        ("Armed", False) if value == "OΠΛIΣMENO" else ("Disarmed", None)
    )
    client._to_openclosed.side_effect = lambda value: "Open" if value == "ανοικτή" else "Closed"
    c.data = c._fetch()
    c._last_data = deepcopy(c.data)
    before_other = deepcopy(part_data(c.data, other_partition))
    assert part_data(c.data, partition)["status"] == "Armed"
    assert part_data(c.data, partition)["zones"][0]["status"] == "Closed"

    zid = "1" if partition == "1" else "21"
    zone = SigmaSensor(
        c, entry, f"Zone {zid} - Example Status",
        lambda data: next(z["status"] for z in part_data(data, partition)["zones"] if z["zone"] == zid),
        partition=partition, requires_zones=True,
    )
    state = SigmaSensor(
        c, entry, "Alarm Status", lambda data: part_data(data, partition).get("status"),
        partition=partition,
    )
    alarm = SigmaAlarmPanel(c, entry, partition)
    unique_ids = (zone._attr_unique_id, state._attr_unique_id, alarm._attr_unique_id)
    assert zone.available is True
    assert zone.native_value == "Closed"

    # A verified disarm can arrive with a header-only zones table. It must
    # replace the old armed state and old contacts in both live data and cache.
    confirmed = {
        "status": "Disarmed", "zones_bypassed": None, "battery_volt": None,
        "ac_power": None, "zones": [], "zones_available": False,
    }
    c.async_set_updated_data = MagicMock(side_effect=lambda data: setattr(c, "data", data))
    c._partition_failures[partition] = c.max_consecutive_failures - 1
    c.async_set_partition_data(partition, deepcopy(confirmed))
    assert part_data(c.data, partition) == confirmed
    assert part_data(c._last_data, partition) == confirmed
    assert part_data(c.data, other_partition) == before_other
    assert part_data(c._last_data, other_partition) == before_other
    assert c._partition_failures[partition] == 0
    assert state.available is True
    assert state.native_value == "Disarmed"
    assert alarm.available is True
    assert alarm.alarm_state == AlarmControlPanelState.DISARMED
    assert zone.available is False
    assert zone.native_value is None

    def incomplete(part):
        if part == partition:
            raise ValueError("valid alarm header but missing zones")
        return raw(part)

    client.safe_get_partition_status.side_effect = incomplete
    for failures in range(1, c.max_consecutive_failures):
        result = c._fetch()
        # Mirror the normal successful coordinator update, including its cache.
        c.data = result
        c._last_data = result
        assert c._partition_failures[partition] == failures
        assert part_data(result, partition) == confirmed
        assert part_data(result, other_partition) == before_other
        assert state.available is True
        assert state.native_value == "Disarmed"
        assert alarm.alarm_state == AlarmControlPanelState.DISARMED
        assert zone.available is False
        assert zone.native_value is None

    # A later complete response restores the same zone entity with its new
    # observed Open value, rather than manufacturing the former Closed value.
    client.safe_get_partition_status.side_effect = lambda part: raw(
        part, armed=part != partition, zone_open=part == partition
    )
    recovered = c._fetch()
    c.data = recovered
    c._last_data = recovered
    assert part_data(recovered, partition)["zones_available"] is True
    assert part_data(recovered, partition)["status"] == "Disarmed"
    assert part_data(recovered, other_partition) == before_other
    assert c._partition_failures[partition] == 0
    assert state.native_value == "Disarmed"
    assert alarm.alarm_state == AlarmControlPanelState.DISARMED
    assert zone.available is True
    assert zone.native_value == "Open"
    assert (zone._attr_unique_id, state._attr_unique_id, alarm._attr_unique_id) == unique_ids
