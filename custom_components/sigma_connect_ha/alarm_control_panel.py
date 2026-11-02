import asyncio  # NEW: brief delay & double refresh
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
from homeassistant.exceptions import HomeAssistantError
from .partition_client import ActionNotAllowedError


async def async_setup_entry(hass, entry, async_add_entities):
    """Create the Sigma alarm control‑panel entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    if getattr(coordinator, "partition_ids", ("1",)) == ("1", "2"):
        async_add_entities([PartitionAlarmPanel(coordinator, entry, part) for part in ("1", "2")])
    else:
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
        self.hass.async_create_task(self.coordinator.async_request_refresh())
        await asyncio.sleep(1)
        self.hass.async_create_task(self.coordinator.async_request_refresh())

    # ---------------------------------------------------------------------
    # AlarmControlPanelEntity callbacks
    # ---------------------------------------------------------------------

    async def async_alarm_disarm(self, code=None):
        await self.hass.async_add_executor_job(
            self.coordinator.client.perform_action, "disarm"
        )
        self.hass.async_create_task(self.coordinator.async_request_refresh())

    async def async_alarm_arm_away(self, code=None):
        await self.hass.async_add_executor_job(
            self.coordinator.client.perform_action, "arm"
        )
        self.hass.async_create_task(self.coordinator.async_request_refresh())

    async def async_alarm_arm_home(self, code=None):
        await self.hass.async_add_executor_job(
            self.coordinator.client.perform_action, "stay"
        )
        self.hass.async_create_task(self.coordinator.async_request_refresh())


class PartitionAlarmPanel(SigmaAlarmPanel):
    """Entity behavior enabled only for entries configured with two PINs."""

    def __init__(self, coordinator, entry, partition="1"):
        super().__init__(coordinator, entry)
        self.partition = partition
        if partition != "1":
            self._attr_name = f"Sigma Alarm Partition {partition}"
            self._attr_unique_id += f"_partition_{partition}"

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
    def available(self):
        data = self.coordinator.data or {}
        if self.partition == "2":
            data = data.get("partition_2")
        return super().available and data is not None and data.get("available", True)

    async def _async_action(self, action):
        # Shared by both partition entities, before occupying an executor thread.
        if getattr(self.coordinator, "_action_in_progress", False) is True:
            raise HomeAssistantError(
                "Another Sigma alarm command is in progress; wait for it to finish"
            )
        self.coordinator._action_in_progress = True
        # Keep ownership until the executor actually stops, even if the service
        # caller is cancelled. Retain the task and consume a possible late error.
        task = self.hass.async_create_task(self._async_execute_action(action))
        self.coordinator._action_task = task
        task.add_done_callback(self._action_finished)
        return await asyncio.shield(task)

    def _action_finished(self, task):
        if self.coordinator._action_task is task:
            self.coordinator._action_task = None
        if not task.cancelled():
            task.exception()

    async def _async_execute_action(self, action):
        try:
            # Like the original entity, unconfirmed False results are logged by
            # the client and followed by a refresh, rather than a service error.
            await self.hass.async_add_executor_job(
                self.coordinator.client.perform_partition_action, action, self.partition
            )
        except ActionNotAllowedError as error:
            raise HomeAssistantError(str(error)) from error
        finally:
            self.coordinator._action_in_progress = False
            self.hass.async_create_task(self.coordinator.async_request_refresh())

    async def async_alarm_disarm(self, code=None):
        await self._async_action("disarm")

    async def async_alarm_arm_away(self, code=None):
        await self._async_action("arm")

    async def async_alarm_arm_home(self, code=None):
        await self._async_action("stay")
