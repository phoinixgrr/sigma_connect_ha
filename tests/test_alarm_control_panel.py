"""Tests for alarm_control_panel.py."""
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# conftest.py patches AlarmControlPanelState for older HA versions
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)

from custom_components.sigma_connect_ha.alarm_control_panel import SigmaAlarmPanel
from custom_components.sigma_connect_ha.const import DOMAIN


class TestSigmaAlarmPanel:
    """Tests for SigmaAlarmPanel entity."""

    @pytest.fixture
    def alarm_panel(self, mock_coordinator, mock_config_entry):
        """Create an alarm panel instance."""
        panel = SigmaAlarmPanel(mock_coordinator, mock_config_entry)
        panel.hass = mock_coordinator.hass
        return panel

    def test_alarm_panel_unique_id(self, alarm_panel, mock_config_entry):
        """Test alarm panel unique ID format."""
        expected_id = f"{DOMAIN}_{mock_config_entry.entry_id}_panel"
        assert alarm_panel._attr_unique_id == expected_id

    def test_alarm_panel_name(self, alarm_panel):
        """Test alarm panel name."""
        assert alarm_panel._attr_name == "Sigma Alarm Panel"

    def test_supported_features(self, alarm_panel):
        """Test alarm panel supported features."""
        expected = (
            AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_HOME
        )
        assert alarm_panel._attr_supported_features == expected

    def test_code_format_none(self, alarm_panel):
        """Test alarm panel doesn't require code format."""
        assert alarm_panel._attr_code_format is None

    def test_code_arm_not_required(self, alarm_panel):
        """Test alarm panel doesn't require code for arming."""
        assert alarm_panel._attr_code_arm_required is False


class TestAlarmState:
    """Tests for alarm state mapping."""

    @pytest.fixture
    def alarm_panel(self, mock_coordinator, mock_config_entry):
        """Create an alarm panel instance."""
        return SigmaAlarmPanel(mock_coordinator, mock_config_entry)

    def test_alarm_state_disarmed(self, alarm_panel, mock_coordinator):
        """Test disarmed state mapping."""
        mock_coordinator.data = {"status": "Disarmed"}

        assert alarm_panel.alarm_state == AlarmControlPanelState.DISARMED

    def test_alarm_state_armed_away(self, alarm_panel, mock_coordinator):
        """Test armed away state mapping."""
        mock_coordinator.data = {"status": "Armed"}

        assert alarm_panel.alarm_state == AlarmControlPanelState.ARMED_AWAY

    def test_alarm_state_armed_home(self, alarm_panel, mock_coordinator):
        """Test armed home (perimeter) state mapping."""
        mock_coordinator.data = {"status": "Armed Perimeter"}

        assert alarm_panel.alarm_state == AlarmControlPanelState.ARMED_HOME

    def test_alarm_state_unknown(self, alarm_panel, mock_coordinator):
        """Test unknown state returns None."""
        mock_coordinator.data = {"status": "Unknown"}

        assert alarm_panel.alarm_state is None

    def test_alarm_state_none(self, alarm_panel, mock_coordinator):
        """Test None status returns None."""
        mock_coordinator.data = {"status": None}

        assert alarm_panel.alarm_state is None


class TestDeviceInfo:
    """Tests for device info."""

    def test_device_info_structure(self, mock_coordinator, mock_config_entry):
        """Test device info has required fields."""
        panel = SigmaAlarmPanel(mock_coordinator, mock_config_entry)
        device_info = panel.device_info

        assert "identifiers" in device_info
        assert (DOMAIN, mock_config_entry.entry_id) in device_info["identifiers"]
        assert device_info["name"] == "Sigma Alarm"
        assert device_info["manufacturer"] == "Sigma"
        assert device_info["model"] == "Ixion"
        assert device_info["sw_version"] == "1.0.0"


class TestAlarmActions:
    """Tests for alarm control actions."""

    @pytest.fixture
    def alarm_panel(self, mock_coordinator, mock_config_entry):
        """Create an alarm panel instance with mocked client."""
        mock_coordinator.client = MagicMock()
        mock_coordinator.client.perform_action = MagicMock(return_value=True)

        panel = SigmaAlarmPanel(mock_coordinator, mock_config_entry)
        panel.hass = mock_coordinator.hass
        return panel

    @pytest.mark.asyncio
    async def test_async_alarm_disarm_calls_client(self, alarm_panel, mock_coordinator):
        """Test disarm action calls client perform_action."""
        await alarm_panel.async_alarm_disarm()

        # Verify perform_action was called with "disarm"
        mock_coordinator.hass.async_add_executor_job.assert_called()
        call_args = mock_coordinator.hass.async_add_executor_job.call_args
        assert call_args[0][0] == mock_coordinator.client.perform_action
        assert call_args[0][1] == "disarm"

    @pytest.mark.asyncio
    async def test_async_alarm_arm_away_calls_client(self, alarm_panel, mock_coordinator):
        """Test arm away action calls client perform_action."""
        await alarm_panel.async_alarm_arm_away()

        call_args = mock_coordinator.hass.async_add_executor_job.call_args
        assert call_args[0][0] == mock_coordinator.client.perform_action
        assert call_args[0][1] == "arm"

    @pytest.mark.asyncio
    async def test_async_alarm_arm_home_calls_client(self, alarm_panel, mock_coordinator):
        """Test arm home (stay) action calls client perform_action."""
        await alarm_panel.async_alarm_arm_home()

        call_args = mock_coordinator.hass.async_add_executor_job.call_args
        assert call_args[0][0] == mock_coordinator.client.perform_action
        assert call_args[0][1] == "stay"

    @pytest.mark.asyncio
    async def test_async_alarm_disarm_requests_refresh(self, alarm_panel, mock_coordinator):
        """Test disarm action requests coordinator refresh."""
        await alarm_panel.async_alarm_disarm()

        mock_coordinator.hass.async_create_task.assert_called()

    @pytest.mark.asyncio
    async def test_async_alarm_arm_away_requests_refresh(self, alarm_panel, mock_coordinator):
        """Test arm away action requests coordinator refresh."""
        await alarm_panel.async_alarm_arm_away()

        mock_coordinator.hass.async_create_task.assert_called()

    @pytest.mark.asyncio
    async def test_async_alarm_arm_home_requests_refresh(self, alarm_panel, mock_coordinator):
        """Test arm home action requests coordinator refresh."""
        await alarm_panel.async_alarm_arm_home()

        mock_coordinator.hass.async_create_task.assert_called()

    @pytest.mark.asyncio
    async def test_alarm_disarm_ignores_code_parameter(self, alarm_panel, mock_coordinator):
        """Test disarm accepts and ignores code parameter."""
        await alarm_panel.async_alarm_disarm(code="1234")

        # Should still work, code is ignored
        mock_coordinator.hass.async_add_executor_job.assert_called()
