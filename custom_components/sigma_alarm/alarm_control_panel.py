from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([SigmaAlarmPanel(coordinator)])

class SigmaAlarmPanel(CoordinatorEntity, AlarmControlPanelEntity):
    _attr_name = "Sigma Alarm Panel"
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY |
        AlarmControlPanelEntityFeature.ARM_HOME
    )  # ⚠️ DISARM is no longer a valid feature and is implicit

    def __init__(self, coordinator):
        super().__init__(coordinator)

    @property
    def state(self):
        status = self.coordinator.data.get("status")
        if status == "Disarmed":
            return AlarmControlPanelState.DISARMED
        elif status == "Armed":
            return AlarmControlPanelState.ARMED_AWAY
        elif status == "Perimeter Armed":
            return AlarmControlPanelState.ARMED_HOME
        return AlarmControlPanelState.UNKNOWN

    async def async_alarm_disarm(self, code=None):
        await self.hass.async_add_executor_job(
            self.coordinator.client.perform_action, "disarm"
        )
        await self.coordinator.async_request_refresh()

    async def async_alarm_arm_away(self, code=None):
        await self.hass.async_add_executor_job(
            self.coordinator.client.perform_action, "arm"
        )
        await self.coordinator.async_request_refresh()

    async def async_alarm_arm_home(self, code=None):
        await self.hass.async_add_executor_job(
            self.coordinator.client.perform_action, "stay"
        )
        await self.coordinator.async_request_refresh()
