"""Single-partition behaviour, end to end against the simulated panel.

These tests pin down what a single-partition installation sends to the panel and what Home
Assistant shows. Most installations are single-partition, so a change that alters any of
this should fail here and be made on purpose, never by accident.
"""
import asyncio
import contextlib

import pytest

from tests.simulator import ARMED, DISARMED, PERIMETER
from tests.simulator.integration import start_integration

LOGIN = [
    ("GET", "logout.html"),
    ("GET", "login.html"),
    ("POST", "login.html"),
    ("GET", "user.html"),
    ("POST", "ucode"),
]
SELECT = [("GET", "panel.html"), ("POST", "part.cgi")]
READ = SELECT + [("GET", "zones.html")]


def command(path):
    """A command's requests: status check, trigger, confirming read, then the refresh."""
    return READ + SELECT + [("GET", path)] + READ + READ


async def test_first_poll_logs_in_and_reads_the_status(sim_panel):
    sim = await start_integration(sim_panel)

    assert sim_panel.requests == LOGIN + READ
    assert sim.coordinator.data == {
        "status": "Disarmed",
        "zones_bypassed": None,
        "battery_volt": 13.5,
        "ac_power": True,
        "zones": [
            {"zone": "1", "description": "Front Door", "status": "Closed", "bypass": False},
            {"zone": "2", "description": "Back Door", "status": "Open", "bypass": False},
            {"zone": "3", "description": "Window", "status": "Closed", "bypass": True},
        ],
    }


async def test_next_poll_reuses_the_session(sim_panel):
    sim = await start_integration(sim_panel)
    sim_panel.clear_log()

    await sim.refresh()

    assert sim_panel.requests == READ


async def test_entities_keep_their_names_and_unique_ids(sim_panel):
    sim = await start_integration(sim_panel)

    assert type(sim.alarm).__name__ == "SigmaAlarmPanel"
    assert sim.snapshot() == [
        ("sigma_connect_ha_simulated_alarm_status", "Sigma Alarm Status", "Disarmed", True),
        ("sigma_connect_ha_simulated_zones_bypassed", "Sigma Zones Bypassed", None, True),
        ("sigma_connect_ha_simulated_battery_voltage", "Sigma Battery Voltage", 13.5, True),
        ("sigma_connect_ha_simulated_ac_power", "Sigma AC Power", True, True),
        ("sigma_connect_ha_simulated_zone_1_-_front_door_status", "Sigma Zone 1 - Front Door Status", "Closed", True),
        ("sigma_connect_ha_simulated_zone_1_-_front_door_bypass", "Sigma Zone 1 - Front Door Bypass", False, True),
        ("sigma_connect_ha_simulated_zone_2_-_back_door_status", "Sigma Zone 2 - Back Door Status", "Open", True),
        ("sigma_connect_ha_simulated_zone_2_-_back_door_bypass", "Sigma Zone 2 - Back Door Bypass", False, True),
        ("sigma_connect_ha_simulated_zone_3_-_window_status", "Sigma Zone 3 - Window Status", "Closed", True),
        ("sigma_connect_ha_simulated_zone_3_-_window_bypass", "Sigma Zone 3 - Window Bypass", True, True),
        ("sigma_connect_ha_simulated_panel", "Sigma Alarm Panel", "disarmed", True),
    ]


@pytest.mark.parametrize(
    "start, method, path, end, ha_state",
    [
        (DISARMED, "async_alarm_arm_away", "arm.html", ARMED, "armed_away"),
        (DISARMED, "async_alarm_arm_home", "stay.html", PERIMETER, "armed_home"),
        (ARMED, "async_alarm_disarm", "disarm.html", DISARMED, "disarmed"),
    ],
)
async def test_commands_reach_the_panel(sim_panel, start, method, path, end, ha_state):
    sim_panel.set_state("1", start)
    sim = await start_integration(sim_panel)
    sim_panel.clear_log()

    await getattr(sim.alarm, method)()
    await sim.settle()

    assert sim_panel.requests == command(path)
    assert sim_panel.commands == [f"{path[:-5]}@1"]
    assert sim_panel.state("1") == end
    assert sim.alarm.alarm_state.value == ha_state


async def test_command_for_the_current_state_sends_nothing(sim_panel):
    sim = await start_integration(sim_panel)
    sim_panel.clear_log()

    await sim.alarm.async_alarm_disarm()
    await sim.settle()

    assert sim_panel.commands == []
    assert sim_panel.requests == READ + READ  # status check, then the refresh


async def test_overlapping_commands_queue_and_both_reach_the_panel(sim_panel):
    sim = await start_integration(sim_panel)
    sim_panel.clear_log()
    loop = asyncio.get_running_loop()

    with sim_panel.holding_commands():
        arm = asyncio.create_task(sim.alarm.async_alarm_arm_away())
        assert await loop.run_in_executor(None, sim_panel.command_started.wait, 5)
        disarm = asyncio.create_task(sim.alarm.async_alarm_disarm())
        await asyncio.sleep(0.05)
        assert not disarm.done()  # waiting for the lock, not rejected
    await asyncio.gather(arm, disarm)
    await sim.settle()

    assert sim_panel.commands == ["arm@1", "disarm@1"]
    assert sim_panel.state("1") == DISARMED
    assert not sim.coordinator.lock.locked()


async def test_expired_session_recovers_with_a_full_login(sim_panel):
    sim = await start_integration(sim_panel)
    sim_panel.clear_log()
    sim_panel.expire_sessions()

    await sim.refresh()

    assert sim_panel.requests == READ + LOGIN + READ  # reuse fails, then a full login
    assert sim.coordinator.data["status"] == "Disarmed"


async def test_ignored_command_is_sent_again(sim_panel):
    sim = await start_integration(sim_panel)
    sim_panel.ignore_commands = 1

    await sim.alarm.async_alarm_arm_away()
    await sim.settle()

    assert sim_panel.commands == ["arm@1", "arm@1"]
    assert sim_panel.state("1") == ARMED


async def test_server_errors_are_retried(sim_panel):
    sim = await start_integration(sim_panel, options={"retry_backoff_factor": 0})
    sim_panel.clear_log()
    sim_panel.fail_requests["zones.html"] = 2

    await sim.refresh()

    assert sim_panel.requests.count(("GET", "zones.html")) == 3
    assert sim.coordinator.data["status"] == "Disarmed"


async def test_a_vanished_zone_is_not_reported_with_stale_data(sim_panel):
    sim = await start_integration(sim_panel)
    sim_panel.remove_zone("1", "3")

    await sim.refresh()

    assert [z["zone"] for z in sim.coordinator.data["zones"]] == ["1", "2"]
    window = sim.entity("Sigma Zone 3 - Window Status")
    # Either no value or an error; never the last known "Closed".
    with contextlib.suppress(StopIteration):
        assert window.native_value is None


async def test_analytics_only_send_the_allowlisted_settings(sim_panel):
    await start_integration(sim_panel, options={"update_interval": 20.0, "pin": "1234"})

    assert len(sim_panel.analytics) == 1
    payload = sim_panel.analytics[0]
    assert payload["zones"] == 3
    assert payload["version"] == "simulated"
    assert set(payload["config"]) == {
        "update_interval", "retry_total", "retry_backoff_factor", "retry_attempts_for_html",
        "max_total_attempts", "max_action_attempts", "max_consecutive_failures",
        "enable_analytics",
    }
    assert payload["config"]["update_interval"] == 20.0
    text = str(payload)
    assert sim_panel.password not in text and "1234" not in text


async def test_analytics_can_be_turned_off(sim_panel):
    await start_integration(sim_panel, options={"enable_analytics": False})

    assert sim_panel.analytics == []


async def test_an_empty_options_pin_falls_back_to_the_setup_pin(sim_panel):
    # What saving the options form with an empty PIN field stores.
    sim = await start_integration(sim_panel, options={"pin": "", "update_interval": 15.0})

    assert sim.coordinator.client.pin == "1234"
    assert sim.coordinator.update_interval.total_seconds() == 15.0
    assert sim.coordinator.data["status"] == "Disarmed"


async def test_an_options_pin_replaces_the_setup_pin(sim_panel_factory):
    panel = sim_panel_factory(pins={"4321": "1"})

    sim = await start_integration(panel, pin="1234", options={"pin": "4321"})

    assert sim.coordinator.client.pin == "4321"
    assert sim.coordinator.data["status"] == "Disarmed"


async def test_a_wrong_pin_fails_setup(sim_panel):
    with pytest.raises(Exception, match="fetch failed"):
        await start_integration(sim_panel, pin="0000")
