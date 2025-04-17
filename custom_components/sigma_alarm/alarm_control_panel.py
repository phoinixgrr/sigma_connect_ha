from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature
)
from homeassistant.const import STATE_ALARM_ARMED_AWAY, STATE_ALARM_DISARMED
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([SigmaAlarmPanel(coordinator)])

class SigmaAlarmPanel(CoordinatorEntity, AlarmControlPanelEntity):
    _attr_name = "Sigma Alarm Panel"
    _attr_supported_features = 0  # No arming/disarming

    def __init__(self, coordinator):
        super().__init__(coordinator)

    @property
    def state(self):
        status = self.coordinator.data.get("status")
        if status == "Disarmed":
            return STATE_ALARM_DISARMED
        if status == "Armed":
            return STATE_ALARM_ARMED_AWAY
        if status == "Perimeter Armed":
            return STATE_ALARM_ARMED_AWAY  # Treat it as Armed for now
        return None
