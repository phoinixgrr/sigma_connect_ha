"""Run the real integration code against a FakePanel, without a full Home Assistant.

The coordinator, client, sensors and alarm entities are the real ones. Only the Home
Assistant core is stubbed: executor jobs run in threads, tasks are tracked so tests can
wait for them, and refreshes call the coordinator's update method directly.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from custom_components.sigma_connect_ha import alarm_control_panel, sensor
from custom_components.sigma_connect_ha.const import DOMAIN
from custom_components.sigma_connect_ha.coordinator import SigmaCoordinator

from .panel import FakePanel


class SimulatedIntegration:
    def __init__(self, panel: FakePanel, hass, entry, coordinator: SigmaCoordinator) -> None:
        self.panel = panel
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.sensors: list = []
        self.alarm_panels: list = []
        self._tasks: list[asyncio.Task] = []

    async def refresh(self) -> None:
        """One coordinator poll, like Home Assistant's scheduled refresh."""
        self.coordinator.data = await self.coordinator._async_update_data()
        self.coordinator.last_update_success = True

    async def settle(self) -> None:
        """Wait for background tasks, such as the refresh after a command."""
        # Tasks can start more tasks (a command schedules a refresh), so loop until all are done.
        while pending := [task for task in self._tasks if not task.done()]:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()

    @property
    def alarm(self):
        return self.alarm_panels[0]

    def entity(self, name: str):
        return next(e for e in self.sensors + self.alarm_panels if e.name == name)

    def snapshot(self) -> list[tuple]:
        """(unique_id, name, value, available) for every entity."""
        rows = []
        for e in self.sensors + self.alarm_panels:
            if hasattr(e, "alarm_state"):
                value = e.alarm_state.value if e.alarm_state is not None else None
            else:
                value = e.native_value
            rows.append((e.unique_id, e.name, value, e.available))
        return rows


def _make_hass(tracked: list):
    loop = asyncio.get_running_loop()
    hass = MagicMock()
    hass.loop = loop
    hass.data = {}
    hass.async_add_executor_job = lambda func, *args: loop.run_in_executor(None, func, *args)

    def create_task(coro, *args, **kwargs):
        task = loop.create_task(coro)
        tracked.append(task)
        return task

    hass.async_create_task = create_task
    return hass


async def start_integration(
    panel: FakePanel,
    *,
    options: dict | None = None,
    pin: str = "1234",
    username: str | None = None,
    password: str | None = None,
) -> SimulatedIntegration:
    """Set up the integration against ``panel``, like async_setup_entry does."""
    tracked: list[asyncio.Task] = []
    hass = _make_hass(tracked)
    entry = MagicMock()
    entry.entry_id = "simulated"
    entry.data = {
        "host": "sigma.invalid",
        "username": username or panel.username,
        "password": password or panel.password,
        "pin": pin,
    }
    entry.options = dict(options or {})

    coordinator = SigmaCoordinator(hass, entry)
    # The integration always builds http://<host>:5053; point it at the simulator instead.
    coordinator.client.base_url = panel.url
    coordinator.client._config["version"] = "simulated"

    sim = SimulatedIntegration(panel, hass, entry, coordinator)
    sim._tasks = tracked
    coordinator.async_request_refresh = sim.refresh
    coordinator.async_set_updated_data = lambda data: setattr(coordinator, "data", data)

    await sim.refresh()  # like async_config_entry_first_refresh
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "config": entry.data}}
    await sensor.async_setup_entry(hass, entry, sim.sensors.extend)
    await alarm_control_panel.async_setup_entry(hass, entry, sim.alarm_panels.extend)
    for entity in sim.sensors + sim.alarm_panels:
        entity.hass = hass
    return sim
