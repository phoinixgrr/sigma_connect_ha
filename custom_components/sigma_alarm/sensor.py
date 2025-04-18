# custom_components/sigma_alarm/sensor.py

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfElectricPotential

from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sensors = []

    sensors.append(
        SigmaSensor(coordinator, entry.entry_id, "Alarm Status", lambda d: d.get("status"))
    )
    sensors.append(
        SigmaSensor(coordinator, entry.entry_id, "Zones Bypassed", lambda d: d.get("zones_bypassed"))
    )
    sensors.append(
        SigmaSensor(
            coordinator,
            entry.entry_id,
            "Battery Voltage",
            lambda d: d.get("battery_volt"),
            UnitOfElectricPotential.VOLT,
        )
    )
    sensors.append(
        SigmaSensor(coordinator, entry.entry_id, "AC Power", lambda d: d.get("ac_power"))
    )

    for zone in coordinator.data.get("zones", []):
        zid = zone["zone"]
        name = zone["description"]
        sensors.append(
            SigmaSensor(
                coordinator,
                entry.entry_id,
                f"Zone {zid} - {name} Status",
                lambda d, zid=zid: next(z for z in d["zones"] if z["zone"] == zid)["status"],
            )
        )
        sensors.append(
            SigmaSensor(
                coordinator,
                entry.entry_id,
                f"Zone {zid} - {name} Bypass",
                lambda d, zid=zid: next(z for z in d["zones"] if z["zone"] == zid)["bypass"],
            )
        )

    async_add_entities(sensors)


class SigmaSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry_id, name, value_fn, unit=None):
