# custom_components/sigma_alarm/__init__.py

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import device_registry as dr

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

    # Register a device explicitly in the device registry
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Sigma",
        name="Sigma Alarm",
        model="Ixion",
        sw_version="1.0.0",
    )

    # Store everything under hass.data[DOMAIN][entry_id]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "config": entry.data,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
