"""Tests for config_flow.py."""
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType

from custom_components.sigma_connect_ha.config_flow import (
    SigmaAlarmConfigFlow,
    OptionsFlowHandler,
)
from custom_components.sigma_connect_ha.const import DOMAIN, CONF_PIN


class TestUserConfigFlow:
    """Tests for the user configuration flow."""

    @pytest.fixture
    def config_flow(self):
        """Create a config flow instance."""
        flow = SigmaAlarmConfigFlow()
        flow.hass = MagicMock()
        return flow

    @pytest.mark.asyncio
    async def test_user_step_shows_form(self, config_flow):
        """Test user step shows form when no input provided."""
        result = await config_flow.async_step_user(user_input=None)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert "data_schema" in result

    @pytest.mark.asyncio
    async def test_user_step_creates_entry(self, config_flow):
        """Test user step creates entry with valid input."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password123",
            CONF_PIN: "1234",
        }

        result = await config_flow.async_step_user(user_input=user_input)

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Sigma Alarm"
        assert result["data"][CONF_HOST] == "192.168.1.100"
        assert result["data"][CONF_USERNAME] == "admin"
        assert result["data"][CONF_PASSWORD] == "password123"
        assert result["data"][CONF_PIN] == "1234"

    @pytest.mark.asyncio
    async def test_user_step_sanitizes_pin(self, config_flow):
        """Test user step strips whitespace from PIN."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password123",
            CONF_PIN: "  1234  ",
        }

        result = await config_flow.async_step_user(user_input=user_input)

        assert result["data"][CONF_PIN] == "1234"

    @pytest.mark.asyncio
    async def test_user_step_handles_empty_pin(self, config_flow):
        """Test user step handles empty PIN."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password123",
            CONF_PIN: "",
        }

        result = await config_flow.async_step_user(user_input=user_input)

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_PIN] == ""

    @pytest.mark.asyncio
    async def test_user_step_handles_none_pin(self, config_flow):
        """Test user step handles None PIN."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password123",
            CONF_PIN: None,
        }

        result = await config_flow.async_step_user(user_input=user_input)

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_PIN] == ""


class TestOptionsFlow:
    """Tests for the options flow."""

    @pytest.fixture
    def options_flow(self, mock_config_entry):
        """Create an options flow instance."""
        flow = OptionsFlowHandler(mock_config_entry)
        flow.hass = MagicMock()
        flow.hass.async_create_task = MagicMock()
        # Set the config_entry property that the flow accesses
        flow._entry = mock_config_entry
        type(flow).config_entry = property(lambda self: self._entry)
        return flow

    @pytest.mark.asyncio
    async def test_options_init_shows_form(self, options_flow, mock_config_entry):
        """Test options init step shows form."""
        # Ensure config_entry is accessible
        options_flow._entry = mock_config_entry
        result = await options_flow.async_step_init(user_input=None)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"
        assert "data_schema" in result

    @pytest.mark.asyncio
    @patch("custom_components.sigma_connect_ha.config_flow.async_create_notification")
    async def test_options_saves_options(self, mock_notification, options_flow):
        """Test options flow saves options."""
        user_input = {
            CONF_PIN: "9999",
            "update_interval": 20.0,
            "retry_total": 3,
            "retry_backoff_factor": 1.0,
            "retry_attempts_for_html": 5,
            "max_total_attempts": 5,
            "max_action_attempts": 3,
            "action_base_delay": 3.0,
            "post_action_extra_delay": 6.0,
            "max_consecutive_failures": 5,
            "enable_analytics": False,
        }

        result = await options_flow.async_step_init(user_input=user_input)

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["update_interval"] == 20.0
        assert result["data"]["enable_analytics"] is False

    @pytest.mark.asyncio
    @patch("custom_components.sigma_connect_ha.config_flow.async_create_notification")
    async def test_options_creates_notification(self, mock_notification, options_flow):
        """Test options flow creates restart notification."""
        user_input = {
            CONF_PIN: "",
            "update_interval": 10.0,
            "retry_total": 5,
            "retry_backoff_factor": 0.5,
            "retry_attempts_for_html": 3,
            "max_total_attempts": 3,
            "max_action_attempts": 5,
            "action_base_delay": 2.0,
            "post_action_extra_delay": 5.0,
            "max_consecutive_failures": 3,
            "enable_analytics": True,
        }

        await options_flow.async_step_init(user_input=user_input)

        mock_notification.assert_called_once()
        call_args = mock_notification.call_args
        # Check notification mentions restart
        assert "restart" in call_args[0][1].lower()

    @pytest.mark.asyncio
    @patch("custom_components.sigma_connect_ha.config_flow.async_create_notification")
    async def test_options_sanitizes_pin(self, mock_notification, options_flow):
        """Test options flow strips whitespace from PIN."""
        user_input = {
            CONF_PIN: "  5678  ",
            "update_interval": 10.0,
            "retry_total": 5,
            "retry_backoff_factor": 0.5,
            "retry_attempts_for_html": 3,
            "max_total_attempts": 3,
            "max_action_attempts": 5,
            "action_base_delay": 2.0,
            "post_action_extra_delay": 5.0,
            "max_consecutive_failures": 3,
            "enable_analytics": True,
        }

        result = await options_flow.async_step_init(user_input=user_input)

        assert result["data"][CONF_PIN] == "5678"


class TestConfigFlowOptionsHandler:
    """Tests for async_get_options_flow static method."""

    def test_async_get_options_flow_returns_handler(self, mock_config_entry):
        """Test async_get_options_flow returns OptionsFlowHandler."""
        handler = SigmaAlarmConfigFlow.async_get_options_flow(mock_config_entry)

        assert isinstance(handler, OptionsFlowHandler)
