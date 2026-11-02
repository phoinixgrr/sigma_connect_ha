# custom_components/sigma_connect_ha/sensor.py

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfElectricPotential

from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    if getattr(coordinator, "partition_ids", ("1",)) == ("1", "2"):
        return await _async_setup_partition_sensors(hass, entry, async_add_entities)
    sensors = []

    sensors.append(
        SigmaSensor(coordinator, entry, "Alarm Status", lambda d: d.get("status"))
    )
    sensors.append(
        SigmaSensor(coordinator, entry, "Zones Bypassed", lambda d: d.get("zones_bypassed"))
    )
    sensors.append(
        SigmaSensor(
            coordinator,
            entry,
            "Battery Voltage",
            lambda d: d.get("battery_volt"),
            UnitOfElectricPotential.VOLT,
        )
    )
    sensors.append(
        SigmaSensor(coordinator, entry, "AC Power", lambda d: d.get("ac_power"))
    )

    for zone in coordinator.data.get("zones", []):
        zid = zone["zone"]
        name = zone["description"]
        sensors.append(
            SigmaSensor(
                coordinator,
                entry,
                f"Zone {zid} - {name} Status",
                lambda d, zid=zid: next(z for z in d["zones"] if z["zone"] == zid)["status"],
            )
        )
        sensors.append(
            SigmaSensor(
                coordinator,
                entry,
                f"Zone {zid} - {name} Bypass",
                lambda d, zid=zid: next(z for z in d["zones"] if z["zone"] == zid)["bypass"],
            )
        )

    async_add_entities(sensors)


class SigmaSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, name, value_fn, unit=None):
        super().__init__(coordinator)
        self.entry = entry
        self._attr_name = f"Sigma {name}"
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{name.lower().replace(' ', '_')}"
        self._value_fn = value_fn
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self):
        try:
            return self._value_fn(self.coordinator.data)
        except (KeyError, StopIteration):
            return None

    @property
    def device_info(self):
        """Attach to the Sigma Alarm device."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": "Sigma Alarm",
            "manufacturer": "Sigma",
            "model": "Ixion",
            "sw_version": "1.0.0",
        }


async def _async_setup_partition_sensors(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sensors = []

    sensors.append(
        PartitionSensor(coordinator, entry, "Alarm Status", lambda d: d.get("status"))
    )
    sensors.append(
        PartitionSensor(coordinator, entry, "Zones Bypassed", lambda d: d.get("zones_bypassed"))
    )
    sensors.append(
        PartitionSensor(
            coordinator,
            entry,
            "Battery Voltage",
            lambda d: d.get("battery_volt"),
            UnitOfElectricPotential.VOLT,
        )
    )
    sensors.append(
        PartitionSensor(coordinator, entry, "AC Power", lambda d: d.get("ac_power"))
    )

    for zone in coordinator.data.get("zones", []):
        zid = zone["zone"]
        name = zone["description"]
        sensors.append(
            PartitionSensor(
                coordinator,
                entry,
                f"Zone {zid} - {name} Status",
                lambda d, zid=zid: next(z for z in d["zones"] if z["zone"] == zid)["status"],
                requires_zones=True,
            )
        )
        sensors.append(
            PartitionSensor(
                coordinator,
                entry,
                f"Zone {zid} - {name} Bypass",
                lambda d, zid=zid: next(z for z in d["zones"] if z["zone"] == zid)["bypass"],
                requires_zones=True,
            )
        )

    if "2" in coordinator.partition_ids:
        second = (coordinator.data or {}).get("partition_2") or {}
        for label, key, unit in (
            ("Alarm Status", "status", None),
            ("Zones Bypassed", "zones_bypassed", None),
        ):
            sensors.append(PartitionSensor(coordinator, entry, f"Partition 2 {label}",
                lambda d, key=key: (d.get("partition_2") or {}).get(key), unit, "2"))
        for zone in second.get("zones", []):
            for label, key in (("Status", "status"), ("Bypass", "bypass")):
                zid = zone["zone"]
                sensors.append(PartitionSensor(coordinator, entry,
                    f"Partition 2 Zone {zid} - {zone['description']} {label}",
                    lambda d, zid=zid, key=key: next(
                        (z.get(key) for z in (d.get("partition_2") or {}).get("zones", [])
                         if z["zone"] == zid), None), partition="2", requires_zones=True))
    async_add_entities(sensors)


class PartitionSensor(SigmaSensor):
    def __init__(self, coordinator, entry, name, value_fn, unit=None, partition="1", requires_zones=False):
        super().__init__(coordinator, entry, name, value_fn, unit)
        self.partition = partition
        self._requires_zones = requires_zones

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        part = data if self.partition == "1" else (data.get("partition_2") or {})
        if self._requires_zones and not part.get("zones_available", True):
            return None
        try:
            return self._value_fn(self.coordinator.data or {})
        except (KeyError, StopIteration):
            return None

    @property
    def available(self):
        data = self.coordinator.data or {}
        if self.partition == "2":
            data = data.get("partition_2")
        return (super().available and data is not None and data.get("available", True)
                and (not self._requires_zones or data.get("zones_available", True)))
