"""Exercise real one-PIN queueing after the optional partition refactor."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from custom_components.sigma_connect_ha.sigma_client import SigmaClient
from custom_components.sigma_connect_ha.alarm_control_panel import (
    SigmaAlarmPanel, PartitionAlarmPanel, async_setup_entry,
)
from custom_components.sigma_connect_ha.coordinator import SigmaCoordinator
from custom_components.sigma_connect_ha.const import DOMAIN


@pytest.mark.asyncio
async def test_single_pin_overlapping_arm_then_disarm_both_reach_panel(mock_config_entry, monkeypatch):
    lock = asyncio.Lock()
    loop = asyncio.get_running_loop()
    tasks = []
    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task
    hass = SimpleNamespace(loop=loop, async_add_executor_job=lambda func, *args: asyncio.to_thread(func, *args),
                           async_create_task=schedule)
    coordinator = SimpleNamespace(hass=hass, lock=lock, partition_ids=("1",),
                                  data={"status": "Disarmed"}, async_request_refresh=AsyncMock())
    coordinator.async_set_updated_data = lambda data: setattr(coordinator, "data", data)
    client = SigmaClient("http://alarm.example:5053", "example", "example", "1111",
                         coordinator, send_analytics=False)
    client.session.close()
    coordinator.client = client
    client.select_partition = MagicMock(return_value=None)
    client._extract_zones_url = MagicMock(return_value="zones.html")
    state = {"value": "AΦOΠΛIΣMENO"}
    commands = []
    arm_started, release_arm = threading.Event(), threading.Event()
    def get(url, **kwargs):
        path = url.rsplit("/", 1)[-1]
        if path == "arm.html":
            commands.append("arm")
            arm_started.set()
            if not release_arm.wait(2):
                raise RuntimeError("Test did not release arm")
            state["value"] = "OΠΛIΣMENO"
        elif path == "disarm.html":
            commands.append("disarm")
            state["value"] = "AΦOΠΛIΣMENO"
        response = MagicMock(text="<html></html>", url=url, status_code=200)
        return response
    client.session = SimpleNamespace(get=get)
    client.parse_zones_html = lambda soup: {"alarm_status": state["value"], "zones": [],
                                           "battery_volt": 13.7, "ac_power": True}
    monkeypatch.setattr("custom_components.sigma_connect_ha.sigma_client.time.sleep", lambda _: None)
    entity = SigmaAlarmPanel(coordinator, mock_config_entry)
    entity.hass = hass
    arm = asyncio.create_task(entity.async_alarm_arm_away())
    disarm = None
    try:
        assert await asyncio.to_thread(arm_started.wait, 1)
        disarm = asyncio.create_task(entity.async_alarm_disarm())
        await asyncio.sleep(0)
        assert not disarm.done()
        assert commands == ["arm"]
        release_arm.set()
        await asyncio.wait_for(asyncio.gather(arm, disarm), 2)
        await asyncio.gather(*tasks)
        assert commands == ["arm", "disarm"]
        assert state["value"] == "AΦOΠΛIΣMENO"
        assert coordinator.data["status"] == "Disarmed"
        assert not lock.locked()
    finally:
        release_arm.set()
        await asyncio.gather(*[task for task in (arm, disarm) if task is not None], return_exceptions=True)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("parts, expected", [(("1",), SigmaAlarmPanel), (("1", "2"), PartitionAlarmPanel)])
async def test_setup_selects_original_entities_only_for_single_pin(mock_hass, mock_config_entry, parts, expected):
    coordinator = MagicMock(partition_ids=parts)
    mock_hass.data = {DOMAIN: {mock_config_entry.entry_id: {"coordinator": coordinator}}}
    add = MagicMock()
    await async_setup_entry(mock_hass, mock_config_entry, add)
    entities = add.call_args.args[0]
    assert len(entities) == len(parts)
    assert all(type(entity) is expected for entity in entities)


@pytest.mark.parametrize("second_pin, requested, expected", [("", 1, 1), ("2222", 1, 60), ("2222", 120, 120)])
def test_polling_floor_applies_only_to_two_pin_entries(mock_hass, mock_config_entry, second_pin, requested, expected):
    mock_config_entry.options.update({"partition_2_pin": second_pin, "update_interval": requested})
    with patch("homeassistant.helpers.frame.report_usage", create=True), \
         patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient"), \
         patch("custom_components.sigma_connect_ha.coordinator.PartitionClient"):
        coordinator = SigmaCoordinator(mock_hass, mock_config_entry)
    assert coordinator.update_interval.total_seconds() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("dual", [False, True])
async def test_unconfirmed_results_keep_original_service_behavior(mock_config_entry, dual):
    tasks = []
    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.append(task)
        return task
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=False), async_create_task=schedule)
    coordinator = SimpleNamespace(hass=hass, client=MagicMock(), _action_in_progress=False,
                                  _action_task=None, async_request_refresh=AsyncMock())
    entity = PartitionAlarmPanel(coordinator, mock_config_entry, "2") if dual else SigmaAlarmPanel(coordinator, mock_config_entry)
    entity.hass = hass
    await entity.async_alarm_arm_away()
    await asyncio.gather(*tasks)
    coordinator.async_request_refresh.assert_awaited_once()
