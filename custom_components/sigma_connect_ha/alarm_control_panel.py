import asyncio
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Sigma alarm control panel entity."""
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

    @property
    def alarm_state(self):
        """Map internal alarm state to HA AlarmControlPanelState."""
        status = self.coordinator.data.get("status")
        if status == "Disarmed":
            return AlarmControlPanelState.DISARMED
        if status == "Armed":
            return AlarmControlPanelState.ARMED_AWAY
        if status == "Armed Perimeter":
            return AlarmControlPanelState.ARMED_HOME
        return AlarmControlPanelState.UNKNOWN

    @property
    def device_info(self):
        """Device registry metadata."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": "Sigma Alarm",
            "manufacturer": "Sigma",
            "model": "Ixion",
            "sw_version": "1.0.0",
        }

    async def _double_refresh(self):
        """Force full re-auth and refresh state twice."""
        await asyncio.sleep(1)  # Give panel time to apply state
        await self.hass.async_add_executor_job(self.coordinator.client.logout)
        await self.coordinator.async_request_refresh()
        await asyncio.sleep(1)
        await self.coordinator.async_request_refresh()

    async def _safe_alarm_action(self, action: str):
        """Perform arm/disarm operation with lock and refresh."""
        async with self.coordinator._lock:
            success = await self.hass.async_add_executor_job(
                self.coordinator.client.perform_action, action
            )
        if not success:
            raise HomeAssistantError(f"Sigma alarm action '{action}' failed")
        await self._double_refresh()

    async def async_alarm_disarm(self, code=None):
        await self._safe_alarm_action("disarm")

    async def async_alarm_arm_away(self, code=None):
        await self._safe_alarm_action("arm")

    async def async_alarm_arm_home(self, code=None):
        await self._safe_alarm_action("stay")
