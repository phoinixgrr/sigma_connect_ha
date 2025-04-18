from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
)
from homeassistant.const import (
    STATE_ALARM_DISARMED,
    STATE_ALARM_ARMED_AWAY,
    STATE_ALARM_ARMED_HOME,
    STATE_UNKNOWN,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([SigmaAlarmPanel(coordinator)])


class SigmaAlarmPanel(CoordinatorEntity, AlarmControlPanelEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "sigma_alarm"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = "sigma_alarm_panel"
        self._attr_supported_features = (
            AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_HOME
            | AlarmControlPanelEntityFeature.DISARM
        )

    @property
    def name(self):
        return "Sigma Alarm"

    @property
    def state(self):
        status = self.coordinator.data.get("status")
        if status == "Disarmed":
            return STATE_ALARM_DISARMED
        elif status == "Armed":
            return STATE_ALARM_ARMED_AWAY
        elif status == "Perimeter Armed":
            return STATE_ALARM_ARMED_HOME
        return STATE_UNKNOWN

    @property
    def device_info(self):
        entry_id = self.coordinator.config_entry.entry_id
        return self.coordinator.hass.data[DOMAIN][entry_id]["device_info"]

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
