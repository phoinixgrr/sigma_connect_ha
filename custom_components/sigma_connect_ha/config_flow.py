# File: custom_components/sigma_connect_ha/config_flow.py

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers import config_validation as cv
from homeassistant.components.persistent_notification import async_create as async_create_notification

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
    DEFAULT_MAX_CONSECUTIVE_FAILURES
)


class SigmaAlarmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial configuration flow for Sigma Alarm."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input:
            return self.async_create_entry(
                title="Sigma Alarm",
                data={
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                },
            )

        data_schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id="user", data_schema=data_schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Options flow for advanced settings."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Sigma Alarm integration advanced settings."""

    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            async_create_notification(
                self.hass,
                "Please restart Home Assistant for the Sigma Alarm changes to take effect.",
                title="Sigma Alarm Integration",
            )
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=opts.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
            vol.Optional(
                CONF_RETRY_TOTAL,
                default=opts.get(CONF_RETRY_TOTAL, DEFAULT_RETRY_TOTAL),
            ): vol.All(cv.positive_int),
            vol.Optional(
                CONF_RETRY_BACKOFF_FACTOR,
                default=opts.get(CONF_RETRY_BACKOFF_FACTOR, DEFAULT_RETRY_BACKOFF_FACTOR),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
            vol.Optional(
                CONF_RETRY_ATTEMPTS_FOR_HTML,
                default=opts.get(CONF_RETRY_ATTEMPTS_FOR_HTML, DEFAULT_RETRY_ATTEMPTS_FOR_HTML),
            ): vol.All(cv.positive_int),
            vol.Optional(
                CONF_MAX_TOTAL_ATTEMPTS,
                default=opts.get(CONF_MAX_TOTAL_ATTEMPTS, DEFAULT_MAX_TOTAL_ATTEMPTS),
            ): vol.All(cv.positive_int),
            vol.Optional(
                CONF_MAX_ACTION_ATTEMPTS,
                default=opts.get(CONF_MAX_ACTION_ATTEMPTS, DEFAULT_MAX_ACTION_ATTEMPTS),
            ): vol.All(cv.positive_int),
            vol.Optional(
                CONF_ACTION_BASE_DELAY,
                default=opts.get(CONF_ACTION_BASE_DELAY, DEFAULT_ACTION_BASE_DELAY),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
            vol.Optional(
                CONF_POST_ACTION_EXTRA_DELAY,
                default=opts.get(CONF_POST_ACTION_EXTRA_DELAY, DEFAULT_POST_ACTION_EXTRA_DELAY),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.0)),
            vol.Optional(
                CONF_MAX_CONSECUTIVE_FAILURES,
                default=opts.get(CONF_MAX_CONSECUTIVE_FAILURES, DEFAULT_MAX_CONSECUTIVE_FAILURES),
            ): vol.All(cv.positive_int, vol.Range(min=1)),
        })

        return self.async_show_form(step_id="init", data_schema=schema)
