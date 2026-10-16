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
    CONF_PIN,
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
    CONF_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    CONF_ENABLE_ANALYTICS,
    DEFAULT_ENABLE_ANALYTICS,
)
from . import sigma_client
from .partition_client import PartitionClient
from homeassistant.exceptions import HomeAssistantError

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

        pin = (
            entry.options.get(CONF_PIN)
            or entry.data.get(CONF_PIN)
            or entry.data[CONF_PASSWORD]
        )

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
        self.client = PartitionClient(
            f"http://{base}:5053",
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            pin,
            coordinator=self,
            send_analytics=False,
            second_pin=opts.get("partition_2_pin", entry.data.get("partition_2_pin", "")),
        )
        self.client._config.update(entry.options)

    async def _async_update_data(self):
        # Polling must not run while an arm/disarm action is in progress.
        async with self._poll_lock:
            async with self.lock:
                try:
                    data = await self.hass.async_add_executor_job(self._retry_with_backoff)
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

    @property
    def partition_ids(self):
        return tuple(self.client.partition_pins)

    def _fetch(self):
        first = self.client.read_partition("1")
        if "2" in self.partition_ids:
            try:
                first["partition_2"] = self.client.read_partition("2")
            except Exception:
                if self._last_data is None:
                    raise
                _LOGGER.warning("Partition 2 unavailable; check its PIN and permissions")
                first["partition_2"] = None
        return first

    async def async_partition_action(self, action, partition):
        async with self.lock:
            try:
                await self.hass.async_add_executor_job(
                    self.client.action_partition, action, partition
                )
            except Exception as err:
                raise HomeAssistantError(str(err)) from err
            finally:
                # A command with an uncertain response still requires a fresh status.
                self.hass.async_create_task(self.async_request_refresh())
