"""Bootstrap for the Sigma Alarm integration."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import SigmaCoordinator

PLATFORMS = ["sensor", "alarm_control_panel"]


async def async_setup(hass: HomeAssistant, _: ConfigType) -> bool:
    """YAML setup (unused)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Create the config‑entry and a device so the tile is shown."""
    coordinator = SigmaCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # ---- mandatory: register one device so HA shows a card ----
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Sigma",
        name="Sigma Alarm",
        model="Ixion",
        sw_version="1.0.0",
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "config": entry.data,
    }

    # ---- forward exactly once (duplicates hide the tile) ----
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
