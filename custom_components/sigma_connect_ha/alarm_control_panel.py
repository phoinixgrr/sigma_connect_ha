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
    async_add_entities([SigmaAlarmPanel(coordinator, entry, part)
                        for part in coordinator.partition_ids])


class SigmaAlarmPanel(CoordinatorEntity, AlarmControlPanelEntity):
    """Representation of the Sigma alarm panel in Home Assistant."""

    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
    )
    _attr_code_format = None
    _attr_code_arm_required = False

    def __init__(self, coordinator, entry, partition="1"):
        super().__init__(coordinator)
        self.entry = entry
        self.partition = partition
        self._attr_name = "Sigma Alarm Panel"
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_panel"
        if partition != "1":
            self._attr_name = f"Sigma Alarm Partition {partition}"
            self._attr_unique_id += f"_partition_{partition}"

    # ---------------------------------------------------------------------
    # Properties
    # ---------------------------------------------------------------------

    @property
    def alarm_state(self):
        """Translate integration status to HA alarm panel states."""
        data = self.coordinator.data or {}
        if self.partition == "2":
            data = data.get("partition_2") or {}
        status = data.get("status")
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
        self.hass.async_create_task(self.coordinator.async_request_refresh())
        await asyncio.sleep(1)
        self.hass.async_create_task(self.coordinator.async_request_refresh())

    # ---------------------------------------------------------------------
    # AlarmControlPanelEntity callbacks
    # ---------------------------------------------------------------------

    @property
    def available(self):
        return super().available and (
            self.partition == "1" or
            (self.coordinator.data or {}).get("partition_2") is not None
        )

    async def async_alarm_disarm(self, code=None):
        await self.coordinator.async_partition_action("disarm", self.partition)

    async def async_alarm_arm_away(self, code=None):
        await self.coordinator.async_partition_action("arm", self.partition)

    async def async_alarm_arm_home(self, code=None):
        await self.coordinator.async_partition_action("stay", self.partition)
