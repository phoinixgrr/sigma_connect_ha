# custom_components/sigma_alarm/__init__.py

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import SigmaCoordinator

PLATFORMS = ["sensor", "alarm_control_panel"]

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Sigma Alarm integration from configuration.yaml (not used)."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sigma Alarm from a config entry."""

    coordinator = SigmaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # Store everything under hass.data[DOMAIN][entry_id]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "config": entry.data,
        "device_info": {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Sigma Alarm",
            "manufacturer": "Sigma",
            "model": "Ixion",
            "sw_version": "1.0.0",
        },
    }

    # Forward entry to each platform (sensor, alarm)
    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, platform)
        )

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
