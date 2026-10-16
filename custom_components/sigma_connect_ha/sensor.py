# custom_components/sigma_connect_ha/sensor.py

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfElectricPotential

from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
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

    if "2" in coordinator.partition_ids:
        second = (coordinator.data or {}).get("partition_2") or {}
        for label, key, unit in (
            ("Alarm Status", "status", None),
            ("Zones Bypassed", "zones_bypassed", None),
        ):
            sensors.append(SigmaSensor(coordinator, entry, f"Partition 2 {label}",
                lambda d, key=key: (d.get("partition_2") or {}).get(key), unit, "2"))
        for zone in second.get("zones", []):
            for label, key in (("Status", "status"), ("Bypass", "bypass")):
                zid = zone["zone"]
                sensors.append(SigmaSensor(coordinator, entry,
                    f"Partition 2 Zone {zid} - {zone['description']} {label}",
                    lambda d, zid=zid, key=key: next(
                        (z.get(key) for z in (d.get("partition_2") or {}).get("zones", [])
                         if z["zone"] == zid), None), partition="2"))
    async_add_entities(sensors)


class SigmaSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, name, value_fn, unit=None, partition="1"):
        super().__init__(coordinator)
        self.entry = entry
        self.partition = partition
        self._attr_name = f"Sigma {name}"
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{name.lower().replace(' ', '_')}"
        self._value_fn = value_fn
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self):
        try:
            return self._value_fn(self.coordinator.data or {})
        except (KeyError, StopIteration):
            return None

    @property
    def available(self):
        return super().available and (
            self.partition == "1" or
            (self.coordinator.data or {}).get("partition_2") is not None
        )

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
