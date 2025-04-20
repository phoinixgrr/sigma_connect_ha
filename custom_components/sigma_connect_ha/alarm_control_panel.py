import asyncio  # NEW: brief delay & double refresh
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    """Create the Sigma alarm control‑panel entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([SigmaAlarmPanel(coordinator, entry)])


class SigmaAlarmPanel(CoordinatorEntity, AlarmControlPanelEntity):
    """Representation of the Sigma alarm panel in Home Assistant."""

    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
    )
    _attr_code_format = None
    _attr_code_arm_required = False

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self.entry = entry
        self._attr_name = "Sigma Alarm Panel"
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_panel"

    # ---------------------------------------------------------------------
    # Properties
    # ---------------------------------------------------------------------

    @property
    def alarm_state(self):
        """Translate integration status to HA alarm panel states."""
        status = self.coordinator.data.get("status")
        if status == "Disarmed":
            return AlarmControlPanelState.DISARMED
        if status == "Armed":
            return AlarmControlPanelState.ARMED_AWAY
        if status == "Armed Perimeter":
            return AlarmControlPanelState.ARMED_HOME
        return None

    @property
    def device_info(self):
        """Attach this entity to the Sigma Alarm device."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": "Sigma Alarm",
            "manufacturer": "Sigma",
            "model": "Ixion",
            "sw_version": "1.0.0",
        }

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    async def _double_refresh(self):
        """Wait briefly, then refresh twice for reliability."""
        await asyncio.sleep(1)  # allow panel time to update
        await self.coordinator.async_request_refresh()
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    # ---------------------------------------------------------------------
    # AlarmControlPanelEntity callbacks
    # ---------------------------------------------------------------------

    async def async_alarm_disarm(self, code=None):
        await self.hass.async_add_executor_job(
            self.coordinator.client.perform_action, "disarm"
        )
        await self._double_refresh()

    async def async_alarm_arm_away(self, code=None):
        await self.hass.async_add_executor_job(
            self.coordinator.client.perform_action, "arm"
        )
        await self._double_refresh()

    async def async_alarm_arm_home(self, code=None):
        await self.hass.async_add_executor_job(
            self.coordinator.client.perform_action, "stay"
        )
        await self._double_refresh()
