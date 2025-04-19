# custom_components/sigma_alarm/sensor.py

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
        return self._value_fn(self.coordinator.data)

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
