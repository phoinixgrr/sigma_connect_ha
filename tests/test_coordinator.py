"""Tests for coordinator.py."""
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from custom_components.sigma_connect_ha.coordinator import sanitize_host, SigmaCoordinator
from custom_components.sigma_connect_ha.const import (
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_RETRY_TOTAL,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
)


# Patch the frame helper for all coordinator tests
@pytest.fixture(autouse=True)
def patch_frame_helper():
    """Patch the frame helper to avoid RuntimeError."""
    with patch("homeassistant.helpers.frame.report_usage", create=True):
        yield


class TestSanitizeHost:
    """Tests for sanitize_host function."""

    def test_sanitize_host_strips_http(self):
        """Test http:// is stripped."""
        assert sanitize_host("http://192.168.1.100") == "192.168.1.100"

    def test_sanitize_host_strips_https(self):
        """Test https:// is stripped."""
        assert sanitize_host("https://192.168.1.100") == "192.168.1.100"

    def test_sanitize_host_strips_port(self):
        """Test port is stripped."""
        assert sanitize_host("192.168.1.100:5053") == "192.168.1.100"

    def test_sanitize_host_strips_protocol_and_port(self):
        """Test both protocol and port are stripped."""
        assert sanitize_host("http://192.168.1.100:5053") == "192.168.1.100"

    def test_sanitize_host_strips_whitespace(self):
        """Test whitespace is stripped."""
        assert sanitize_host("  192.168.1.100  ") == "192.168.1.100"

    def test_sanitize_host_handles_hostname(self):
        """Test hostname without protocol passes through."""
        assert sanitize_host("alarm.local") == "alarm.local"

    def test_sanitize_host_full_url(self):
        """Test full URL is sanitized."""
        assert sanitize_host("https://alarm.local:8080") == "alarm.local"


class TestCoordinatorInit:
    """Tests for SigmaCoordinator initialization."""

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_coordinator_uses_default_interval(self, mock_client_class, mock_hass, mock_config_entry):
        """Test coordinator uses default update interval."""
        coordinator = SigmaCoordinator(mock_hass, mock_config_entry)

        assert coordinator.update_interval.total_seconds() == DEFAULT_UPDATE_INTERVAL

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_coordinator_uses_custom_interval(self, mock_client_class, mock_hass, mock_config_entry_with_options):
        """Test coordinator uses custom update interval from options."""
        coordinator = SigmaCoordinator(mock_hass, mock_config_entry_with_options)

        assert coordinator.update_interval.total_seconds() == 15.0

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_coordinator_uses_default_max_failures(self, mock_client_class, mock_hass, mock_config_entry):
        """Test coordinator uses default max consecutive failures."""
        coordinator = SigmaCoordinator(mock_hass, mock_config_entry)

        assert coordinator.max_consecutive_failures == DEFAULT_MAX_CONSECUTIVE_FAILURES

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_coordinator_uses_custom_max_failures(self, mock_client_class, mock_hass, mock_config_entry_with_options):
        """Test coordinator uses custom max failures from options."""
        coordinator = SigmaCoordinator(mock_hass, mock_config_entry_with_options)

        assert coordinator.max_consecutive_failures == 5

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_coordinator_creates_client_with_sanitized_host(self, mock_client_class, mock_hass, mock_config_entry):
        """Test coordinator sanitizes host when creating client."""
        mock_config_entry.data["host"] = "http://192.168.1.100:5053"

        SigmaCoordinator(mock_hass, mock_config_entry)

        # Client should be created with sanitized host
        call_args = mock_client_class.call_args
        base_url = call_args[0][0] if call_args[0] else call_args.kwargs.get("base_url")
        assert base_url == "http://192.168.1.100:5053"

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_coordinator_pin_from_options(self, mock_client_class, mock_hass, mock_config_entry):
        """Test coordinator uses PIN from options if available."""
        mock_config_entry.options["pin"] = "9999"

        SigmaCoordinator(mock_hass, mock_config_entry)

        call_args = mock_client_class.call_args
        pin = call_args.kwargs.get("pin") or call_args[0][3]
        assert pin == "9999"

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_coordinator_pin_falls_back_to_data(self, mock_client_class, mock_hass, mock_config_entry):
        """Test coordinator falls back to data PIN if not in options."""
        SigmaCoordinator(mock_hass, mock_config_entry)

        call_args = mock_client_class.call_args
        pin = call_args.kwargs.get("pin") or call_args[0][3]
        assert pin == "1234"

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_coordinator_pin_falls_back_to_password(self, mock_client_class, mock_hass, mock_config_entry):
        """Test coordinator falls back to password if no PIN."""
        del mock_config_entry.data["pin"]
        mock_config_entry.data["pin"] = None

        SigmaCoordinator(mock_hass, mock_config_entry)

        call_args = mock_client_class.call_args
        pin = call_args.kwargs.get("pin") or call_args[0][3]
        # Falls back to password
        assert pin == "password123"

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_coordinator_disables_analytics_from_options(self, mock_client_class, mock_hass, mock_config_entry_with_options):
        """Test coordinator respects analytics setting from options."""
        SigmaCoordinator(mock_hass, mock_config_entry_with_options)

        call_args = mock_client_class.call_args
        send_analytics = call_args.kwargs.get("send_analytics")
        assert send_analytics is False


class TestCoordinatorFetch:
    """Tests for coordinator fetch logic."""

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_fetch_transforms_data(self, mock_client_class, mock_hass, mock_config_entry):
        """Test _fetch transforms client data correctly."""
        mock_client = MagicMock()
        mock_client.safe_get_status.return_value = {
            "alarm_status": "AΦOΠΛIΣMENO",
            "battery_volt": 13.5,
            "ac_power": True,
            "zones": [
                {"zone": "1", "description": "Door", "status": "κλειστή", "bypass": "OXI"},
            ],
        }
        mock_client.parse_alarm_status.return_value = ("Disarmed", None)
        mock_client._to_openclosed.return_value = "Closed"
        mock_client._to_bool.return_value = False
        mock_client_class.return_value = mock_client

        coordinator = SigmaCoordinator(mock_hass, mock_config_entry)
        result = coordinator._fetch()

        assert result["status"] == "Disarmed"
        assert result["battery_volt"] == 13.5
        assert result["ac_power"] is True
        assert len(result["zones"]) == 1

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_fetch_raises_on_incomplete_data(self, mock_client_class, mock_hass, mock_config_entry):
        """Test _fetch raises ValueError on incomplete data."""
        mock_client = MagicMock()
        mock_client.safe_get_status.return_value = {
            "alarm_status": None,
            "battery_volt": None,
            "zones": [],
        }
        mock_client.parse_alarm_status.return_value = (None, None)
        mock_client_class.return_value = mock_client

        coordinator = SigmaCoordinator(mock_hass, mock_config_entry)

        with pytest.raises(ValueError, match="Incomplete data"):
            coordinator._fetch()

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    @patch("time.sleep")
    def test_retry_with_backoff_retries(self, mock_sleep, mock_client_class, mock_hass, mock_config_entry):
        """Test _retry_with_backoff retries on failure."""
        mock_client = MagicMock()
        call_count = 0

        def mock_safe_get_status():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Temporary error")
            return {
                "alarm_status": "AΦOΠΛIΣMENO",
                "battery_volt": 13.5,
                "ac_power": True,
                "zones": [{"zone": "1", "description": "D", "status": "κλειστή", "bypass": "OXI"}],
            }

        mock_client.safe_get_status = mock_safe_get_status
        mock_client.parse_alarm_status.return_value = ("Disarmed", None)
        mock_client._to_openclosed.return_value = "Closed"
        mock_client._to_bool.return_value = False
        mock_client_class.return_value = mock_client

        coordinator = SigmaCoordinator(mock_hass, mock_config_entry)
        result = coordinator._retry_with_backoff()

        assert call_count == 2
        assert result["status"] == "Disarmed"


class TestCoordinatorFailureTracking:
    """Tests for consecutive failure tracking."""

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_failure_counter_starts_at_zero(self, mock_client_class, mock_hass, mock_config_entry):
        """Test failure counter initializes at zero."""
        coordinator = SigmaCoordinator(mock_hass, mock_config_entry)

        assert coordinator._consecutive_failures == 0

    @patch("custom_components.sigma_connect_ha.coordinator.sigma_client.SigmaClient")
    def test_last_data_starts_none(self, mock_client_class, mock_hass, mock_config_entry):
        """Test last data initializes as None."""
        coordinator = SigmaCoordinator(mock_hass, mock_config_entry)

        assert coordinator._last_data is None
