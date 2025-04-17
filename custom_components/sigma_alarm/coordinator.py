from datetime import timedelta
import logging
import re
import time

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD, CONF_HOST

from .sigma_client import SigmaClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = timedelta(seconds=20)

# Retry constants
MAX_TOTAL_ATTEMPTS = 3
RETRY_BACKOFF_FACTOR = 0.5

def sanitize_host(raw_host):
    # Strip protocol and port
    host = re.sub(r"^https?://", "", raw_host)           # remove http:// or https://
    host = re.sub(r":\d+$", "", host)                     # remove :port if present
    return host.strip()

class SigmaCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config_entry):
        self.hass = hass
        self.config_entry = config_entry

        self.host = sanitize_host(config_entry.data[CONF_HOST])
        self.username = config_entry.data[CONF_USERNAME]
        self.password = config_entry.data[CONF_PASSWORD]

        self.client = SigmaClient(f"http://{self.host}:5053", self.username, self.password)
        self._last_data = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self):
        try:
            new_data = await self.hass.async_add_executor_job(self._retry_fetch_data_with_backoff)
            self._last_data = new_data
            return new_data
        except Exception as err:
            if self._last_data:
                _LOGGER.warning("Fetch failed, using last known good data: %s", err)
                return self._last_data
            raise UpdateFailed(f"Error communicating with Sigma Alarm: {err}") from err

    def _retry_fetch_data_with_backoff(self):
        for attempt in range(1, MAX_TOTAL_ATTEMPTS + 1):
            try:
                _LOGGER.debug("SigmaCoordinator attempt %d/%d", attempt, MAX_TOTAL_ATTEMPTS)
                return self._fetch_data()
            except Exception as e:
                _LOGGER.warning("SigmaCoordinator full flow failed on attempt %d: %s", attempt, e)
                if attempt < MAX_TOTAL_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_FACTOR * (2 ** (attempt - 1)))
                else:
                    raise

    def _fetch_data(self):
        self.client.login()
        soup = self.client.select_partition()
        status = self.client.get_part_status(soup)
        zones = self.client.get_zones(soup)

        parsed_status, zones_bypassed = self.client.parse_alarm_status(status.get("alarm_status"))

        if not parsed_status or status.get("battery_volt") is None or not zones:
            raise ValueError("Parsed data incomplete or invalid, retrying full flow")

        return {
            "status": parsed_status,
            "zones_bypassed": zones_bypassed,
            "battery_volt": status.get("battery_volt"),
            "ac_power": status.get("ac_power"),
            "zones": [
                {
                    "zone":        z["zone"],
                    "description": z["description"],
                    "status":      self.client._to_openclosed(z["status"]),
                    "bypass":      self.client._to_bool(z["bypass"]),
                }
                for z in zones
            ],
        }
