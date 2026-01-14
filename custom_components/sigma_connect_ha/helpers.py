"""Helper functions for Sigma Alarm integration."""
from typing import Dict, Any
from homeassistant.config_entries import ConfigEntry
from .const import DOMAIN


def get_device_info(entry: ConfigEntry) -> Dict[str, Any]:
    """Get standardized device info for Sigma Alarm entities."""
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": "Sigma Alarm",
        "manufacturer": "Sigma",
        "model": "Ixion",
        "sw_version": "1.0.0",
    }
