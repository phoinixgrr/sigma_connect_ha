"""Two-partition (two PIN) behaviour, end to end against a simulated PIN-scoped panel.

Each PIN unlocks one partition, as on the Aeolus installations two-partition support was
built for. These tests skip until the optional partition client is part of the integration.
"""
import asyncio
import random

import pytest

pytest.importorskip("custom_components.sigma_connect_ha.partition_client")

from homeassistant.exceptions import HomeAssistantError  # noqa: E402

from tests.simulator import ARMED, DISARMED, PERIMETER  # noqa: E402
from tests.simulator.integration import start_integration  # noqa: E402

TWO_PINS = {"partition_2_pin": "5678"}
METHODS = {"arm": "async_alarm_arm_away", "disarm": "async_alarm_disarm", "stay": "async_alarm_arm_home"}


@pytest.fixture
def panel(sim_panel_factory):
    return sim_panel_factory(mode="pin_scoped")


async def two_pin_integration(panel, **options):
    sim = await start_integration(panel, options={**TWO_PINS, **options})
    first, second = sim.alarm_panels
    return sim, first, second


async def test_setup_creates_entities_for_both_partitions(panel):
    sim, first, second = await two_pin_integration(panel, update_interval=10.0)

    assert type(sim.coordinator.client).__name__ == "PartitionClient"
    assert sim.coordinator.update_interval.total_seconds() == 60.0  # two-PIN minimum
    assert first.unique_id == "sigma_connect_ha_simulated_panel"  # unchanged for partition 1
    assert second.unique_id == "sigma_connect_ha_simulated_panel_partition_2"
    names = {e.name for e in sim.sensors}
    assert {
        "Sigma Alarm Status", "Sigma Zone 1 - Front Door Status",
        "Sigma Partition 2 Alarm Status", "Sigma Partition 2 Zone 5 - Garage Status",
        "Sigma Partition 2 Zone 6 - Shed Bypass",
    } <= names
    assert sim.entity("Sigma Partition 2 Zone 6 - Shed Bypass").native_value is True
    assert (first.alarm_state.value, second.alarm_state.value) == ("disarmed", "disarmed")


@pytest.mark.parametrize("partition", ["1", "2"])
@pytest.mark.parametrize(
    "start, action, end, ha_state",
    [
        (DISARMED, "arm", ARMED, "armed_away"),
        (DISARMED, "stay", PERIMETER, "armed_home"),
        (ARMED, "disarm", DISARMED, "disarmed"),
    ],
)
async def test_commands_only_reach_their_own_partition(panel, partition, start, action, end, ha_state):
    other = "2" if partition == "1" else "1"
    panel.set_state(partition, start)
    sim, first, second = await two_pin_integration(panel)
    entity = first if partition == "1" else second

    await getattr(entity, METHODS[action])()
    await sim.settle()

    assert panel.commands == [f"{action}@{partition}"]
    assert panel.state(partition) == end
    assert panel.state(other) == DISARMED
    assert entity.alarm_state.value == ha_state


async def test_an_armed_partition_cannot_switch_straight_to_perimeter(panel):
    panel.set_state("2", ARMED)
    sim, _, second = await two_pin_integration(panel)

    with pytest.raises(HomeAssistantError, match="perimeter"):
        await second.async_alarm_arm_home()
    await sim.settle()

    assert panel.commands == []
    assert panel.state("2") == ARMED


async def test_one_action_on_both_partitions_changes_both(panel):
    """HA runs an action that targets several entities in parallel (asyncio.gather)."""
    sim, first, second = await two_pin_integration(panel)

    await asyncio.gather(first.async_alarm_arm_away(), second.async_alarm_arm_away())
    await sim.settle()
    assert sorted(panel.commands) == ["arm@1", "arm@2"]
    assert (panel.state("1"), panel.state("2")) == (ARMED, ARMED)

    await asyncio.gather(first.async_alarm_disarm(), second.async_alarm_disarm())
    await sim.settle()
    assert (panel.state("1"), panel.state("2")) == (DISARMED, DISARMED)
    assert not sim.coordinator.lock.locked()


async def test_arm_then_disarm_on_one_partition_both_reach_the_panel(panel):
    sim, _, second = await two_pin_integration(panel)
    loop = asyncio.get_running_loop()

    with panel.holding_commands():
        arm = asyncio.create_task(second.async_alarm_arm_away())
        assert await loop.run_in_executor(None, panel.command_started.wait, 5)
        disarm = asyncio.create_task(second.async_alarm_disarm())
        await asyncio.sleep(0.05)
    await asyncio.gather(arm, disarm)
    await sim.settle()

    assert panel.commands == ["arm@2", "disarm@2"]
    assert panel.state("2") == DISARMED


async def test_concurrent_commands_never_reach_the_wrong_partition(panel):
    sim, first, second = await two_pin_integration(panel)
    entities = {"1": first, "2": second}
    rng = random.Random(7)

    for _ in range(15):
        plan = [(rng.choice("12"), rng.choice(list(METHODS))) for _ in range(rng.randint(2, 4))]
        panel.clear_log()

        async def run(partition, action):
            await asyncio.sleep(rng.random() / 100)
            try:
                await getattr(entities[partition], METHODS[action])()
            except HomeAssistantError as err:
                assert "perimeter" in str(err)  # the only refusal allowed

        await asyncio.gather(*(run(p, a) for p, a in plan), sim.refresh())
        await sim.settle()

        for sent in panel.commands:
            action, partition = sent.split("@")
            assert (partition, action) in plan, f"{sent} was never requested: {plan}"
        assert not sim.coordinator.lock.locked()
    states = {"disarmed": DISARMED, "armed_away": ARMED, "armed_home": PERIMETER}
    assert states[first.alarm_state.value] == panel.state("1")
    assert states[second.alarm_state.value] == panel.state("2")


async def test_expired_sessions_recover_on_both_partitions(panel):
    sim, first, second = await two_pin_integration(panel)
    panel.set_state("2", ARMED)
    panel.expire_sessions()

    await sim.refresh()

    assert first.alarm_state.value == "disarmed"
    assert second.alarm_state.value == "armed_away"


async def test_partition_2_read_failures_use_the_grace_period_then_recover(panel):
    sim, first, second = await two_pin_integration(panel)
    garage = sim.entity("Sigma Partition 2 Zone 5 - Garage Status")
    front_door = sim.entity("Sigma Zone 1 - Front Door Status")
    panel.empty_zones.add("2")

    for _ in range(2):  # below max_consecutive_failures (3): keep the last data
        await sim.refresh()
        assert second.available and garage.available
    await sim.refresh()
    assert not second.available and not garage.available
    assert first.available and front_door.available  # partition 1 unaffected
    assert front_door.native_value == "Closed"

    panel.empty_zones.clear()
    await sim.refresh()
    assert second.available and garage.available
    assert garage.native_value == "Closed"


async def test_a_wrong_second_pin_fails_setup(panel):
    with pytest.raises(Exception, match="fetch failed"):
        await start_integration(panel, options={"partition_2_pin": "0000"})
