# custom_components/sigma_alarm/sensor.py

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfElectricPotential

from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sensors = []

    sensors.append(
        SigmaSensor(coordinator, "Alarm Status", lambda d: d.get("status"))
    )
    sensors.append(
        SigmaSensor(coordinator, "Zones Bypassed", lambda d: d.get("zones_bypassed"))
    )
    sensors.append(
        SigmaSensor(
            coordinator,
            "Battery Voltage",
            lambda d: d.get("battery_volt"),
            UnitOfElectricPotential.VOLT,
        )
    )
    sensors.append(
        SigmaSensor(coordinator, "AC Power", lambda d: d.get("ac_power"))
    )

    for zone in coordinator.data.get("zones", []):
        zid = zone["zone"]
        name = zone["description"]
        sensors.append(
            SigmaSensor(
                coordinator,
                f"Zone {zid} - {name} Status",
                lambda d, zid=zid: next(z for z in d["zones"] if z["zone"] == zid)["status"],
            )
        )
        sensors.append(
            SigmaSensor(
                coordinator,
                f"Zone {zid} - {name} Bypass",
                lambda d, zid=zid: next(z for z in d["zones"] if z["zone"] == zid)["bypass"],
            )
        )

    async_add_entities(sensors)


class SigmaSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name, value_fn, unit=None):
        super().__init__(coordinator)
        self._attr_name = f"Sigma {name}"
        self._value_fn = value_fn
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = f"{DOMAIN}_{name.lower().replace(' ', '_')}"

    @property
    def native_value(self):
        return self._value_fn(self.coordinator.data)

    @property
    def device_info(self):
        """Return the device info for this sensor."""
        entry_id = self.coordinator.config_entry.entry_id
        return self.coordinator.hass.data[DOMAIN][entry_id]["device_info"]
