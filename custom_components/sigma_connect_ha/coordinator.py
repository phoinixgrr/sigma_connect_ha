from datetime import timedelta
import logging
import re
import time
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD

from .const import (
    DOMAIN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    CONF_RETRY_TOTAL,
    DEFAULT_RETRY_TOTAL,
    CONF_RETRY_BACKOFF_FACTOR,
    DEFAULT_RETRY_BACKOFF_FACTOR,
    CONF_RETRY_ATTEMPTS_FOR_HTML,
    DEFAULT_RETRY_ATTEMPTS_FOR_HTML,
    CONF_MAX_TOTAL_ATTEMPTS,
    DEFAULT_MAX_TOTAL_ATTEMPTS,
    CONF_MAX_ACTION_ATTEMPTS,
    DEFAULT_MAX_ACTION_ATTEMPTS,
    CONF_ACTION_BASE_DELAY,
    DEFAULT_ACTION_BASE_DELAY,
    CONF_POST_ACTION_EXTRA_DELAY,
    DEFAULT_POST_ACTION_EXTRA_DELAY,
    CONF_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    CONF_ENABLE_ANALYTICS,
    DEFAULT_ENABLE_ANALYTICS,
)
from . import sigma_client

_LOGGER = logging.getLogger(__name__)


def sanitize_host(raw_host: str) -> str:
    """Strip protocol and port from host string."""
    host = re.sub(r"^https?://", "", raw_host)
    host = re.sub(r":\d+$", "", host)
    return host.strip()


class SigmaCoordinator(DataUpdateCoordinator):
    """Coordinates data fetching and applies advanced options."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._last_data = None
        self._consecutive_failures = 0

        opts = entry.options
        # Override module constants
        sigma_client.RETRY_TOTAL = opts.get(CONF_RETRY_TOTAL, DEFAULT_RETRY_TOTAL)
        sigma_client.RETRY_BACKOFF_FACTOR = opts.get(
            CONF_RETRY_BACKOFF_FACTOR, DEFAULT_RETRY_BACKOFF_FACTOR
        )
        sigma_client.RETRY_ATTEMPTS_FOR_HTML = opts.get(
            CONF_RETRY_ATTEMPTS_FOR_HTML, DEFAULT_RETRY_ATTEMPTS_FOR_HTML
        )
        sigma_client.MAX_ACTION_ATTEMPTS = opts.get(
            CONF_MAX_ACTION_ATTEMPTS, DEFAULT_MAX_ACTION_ATTEMPTS
        )
        sigma_client.ACTION_BASE_DELAY = opts.get(
            CONF_ACTION_BASE_DELAY, DEFAULT_ACTION_BASE_DELAY
        )
        sigma_client.POST_ACTION_EXTRA_DELAY = opts.get(
            CONF_POST_ACTION_EXTRA_DELAY, DEFAULT_POST_ACTION_EXTRA_DELAY
        )

        self.max_total_attempts = opts.get(
            CONF_MAX_TOTAL_ATTEMPTS, DEFAULT_MAX_TOTAL_ATTEMPTS
        )
        self.max_consecutive_failures = opts.get(
            CONF_MAX_CONSECUTIVE_FAILURES, DEFAULT_MAX_CONSECUTIVE_FAILURES
        )

        interval = opts.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        update_interval = timedelta(seconds=interval)

        # Locks: one for polling, one to serialize actions
        self._poll_lock = asyncio.Lock()
        self.lock = asyncio.Lock()

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )

        base = sanitize_host(entry.data[CONF_HOST])
        self.client = sigma_client.SigmaClient(
            f"http://{base}:5053",
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            coordinator=self,
            send_analytics=opts.get(CONF_ENABLE_ANALYTICS, DEFAULT_ENABLE_ANALYTICS),
        )
        self.client._config.update(entry.options)

    async def _async_update_data(self):
        async with self._poll_lock:
            try:
                data = await self.hass.async_add_executor_job(
                    self._retry_with_backoff
                )
                self._last_data = data
                self._consecutive_failures = 0
                return data
            except Exception as err:
                self._consecutive_failures += 1
                if (
                    self._last_data is not None
                    and self._consecutive_failures < self.max_consecutive_failures
                ):
                    _LOGGER.warning(
                        "Sigma fetch failed (%d/%d), returning last known data: %s",
                        self._consecutive_failures,
                        self.max_consecutive_failures,
                        err,
                    )
                    return self._last_data
                _LOGGER.error(
                    "Sigma fetch failed (%d/%d), marking unavailable: %s",
                    self._consecutive_failures,
                    self.max_consecutive_failures,
                    err,
                )
                raise UpdateFailed(
                    f"Sigma fetch failed ({self._consecutive_failures}/{self.max_consecutive_failures}): {err}"
                )

    def _retry_with_backoff(self):
        for i in range(1, self.max_total_attempts + 1):
            try:
                _LOGGER.debug("Fetch attempt %d/%d", i, self.max_total_attempts)
                return self._fetch()
            except Exception as ex:
                _LOGGER.warning("Attempt %d failed: %s", i, ex)
                if i < self.max_total_attempts:
                    time.sleep(sigma_client.RETRY_BACKOFF_FACTOR * (2 ** (i - 1)))
        raise UpdateFailed("All fetch attempts failed")

    def _fetch(self):
        data = self.client.safe_get_status()
        parsed, bypass = self.client.parse_alarm_status(data.get("alarm_status"))
        if not parsed or data.get("battery_volt") is None or not data.get("zones"):
            raise ValueError("Incomplete data")

        return {
            "status": parsed,
            "zones_bypassed": bypass,
            "battery_volt": data.get("battery_volt"),
            "ac_power": data.get("ac_power"),
            "zones": [
                {
                    **z,
                    "status": self.client._to_openclosed(z["status"]),
                    "bypass": self.client._to_bool(z["bypass"]),
                }
                for z in data["zones"]
            ],
        }
