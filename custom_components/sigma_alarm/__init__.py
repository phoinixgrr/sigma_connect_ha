from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.device_registry import DeviceEntryType

from .const import DOMAIN
from .coordinator import SigmaCoordinator

PLATFORMS = ["sensor", "alarm_control_panel"]

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Sigma Alarm integration from configuration.yaml (not used)."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sigma Alarm from a config entry."""

    # Initialize and refresh the data coordinator
    coordinator = SigmaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator and config in hass.data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "config": entry.data,
    }

    # Load platforms
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
