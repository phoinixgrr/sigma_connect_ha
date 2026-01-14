"""Tests for sigma_client.py."""
import time
from unittest.mock import MagicMock, patch, PropertyMock
import pytest
from bs4 import BeautifulSoup

from custom_components.sigma_connect_ha.sigma_client import (
    SigmaClient,
    retry_html_request,
    post_installation_analytics,
)

from .conftest import (
    SAMPLE_ZONES_HTML,
    SAMPLE_ZONES_HTML_ARMED,
    SAMPLE_ZONES_HTML_PERIMETER,
    SAMPLE_LOGIN_HTML,
)


class TestEncryption:
    """Tests for the encryption method."""

    def test_encrypt_returns_hex_string(self, mock_sigma_client):
        """Verify encryption returns a hex string."""
        encrypted, length = mock_sigma_client._encrypt("test123", "abcdefghijklmnop")

        # Result should be hex characters only
        assert all(c in "0123456789abcdef" for c in encrypted)
        # Length should be numeric string
        assert length.isdigit()

    def test_encrypt_output_length(self, mock_sigma_client):
        """Verify encryption output has correct length."""
        encrypted, length = mock_sigma_client._encrypt("pass", "1234567890123456")

        # Output should be 2 hex chars per character (each char becomes 2 hex digits)
        assert len(encrypted) == int(length) * 2

    @patch("random.randint", return_value=3)
    def test_encrypt_deterministic_with_fixed_random(self, mock_randint, mock_sigma_client):
        """Verify encryption is deterministic when random is mocked."""
        token = "abcdefghijklmnop"
        secret = "test"

        result1, len1 = mock_sigma_client._encrypt(secret, token)
        result2, len2 = mock_sigma_client._encrypt(secret, token)

        assert result1 == result2
        assert len1 == len2


class TestToBool:
    """Tests for _to_bool static method."""

    def test_to_bool_greek_yes_uppercase(self):
        """Test Greek NAI (yes) uppercase."""
        assert SigmaClient._to_bool("ΝΑΙ") is True

    def test_to_bool_greek_yes_latin(self):
        """Test Greek NAI written with latin characters."""
        assert SigmaClient._to_bool("NAI") is True

    def test_to_bool_greek_no(self):
        """Test Greek OXI (no)."""
        assert SigmaClient._to_bool("OXI") is False

    def test_to_bool_english_yes(self):
        """Test English YES."""
        assert SigmaClient._to_bool("YES") is True
        assert SigmaClient._to_bool("yes") is True

    def test_to_bool_english_no(self):
        """Test English NO."""
        assert SigmaClient._to_bool("NO") is False
        assert SigmaClient._to_bool("no") is False

    def test_to_bool_true_false_strings(self):
        """Test TRUE/FALSE strings."""
        assert SigmaClient._to_bool("TRUE") is True
        assert SigmaClient._to_bool("FALSE") is False

    def test_to_bool_none_input(self):
        """Test None input returns None."""
        assert SigmaClient._to_bool(None) is None

    def test_to_bool_empty_string(self):
        """Test empty string returns None."""
        assert SigmaClient._to_bool("") is None

    def test_to_bool_unknown_value(self):
        """Test unknown value returns None."""
        assert SigmaClient._to_bool("maybe") is None

    def test_to_bool_with_whitespace(self):
        """Test values with whitespace are handled."""
        assert SigmaClient._to_bool("  ΝΑΙ  ") is True
        assert SigmaClient._to_bool(" OXI ") is False


class TestToOpenClosed:
    """Tests for _to_openclosed static method."""

    def test_to_openclosed_greek_closed(self):
        """Test Greek 'κλειστή' (closed)."""
        assert SigmaClient._to_openclosed("κλειστή") == "Closed"

    def test_to_openclosed_greek_open(self):
        """Test Greek 'ανοικτή' (open)."""
        assert SigmaClient._to_openclosed("ανοικτή") == "Open"

    def test_to_openclosed_none(self):
        """Test None input returns None."""
        assert SigmaClient._to_openclosed(None) is None

    def test_to_openclosed_empty_string(self):
        """Test empty string returns None."""
        assert SigmaClient._to_openclosed("") is None

    def test_to_openclosed_passthrough(self):
        """Test unknown values are passed through."""
        assert SigmaClient._to_openclosed("Unknown") == "Unknown"


class TestParseAlarmStatus:
    """Tests for parse_alarm_status method."""

    def test_parse_alarm_status_disarmed(self, mock_sigma_client):
        """Test disarmed status parsing."""
        status, bypass = mock_sigma_client.parse_alarm_status("AΦOΠΛIΣMENO")
        assert status == "Disarmed"
        assert bypass is None

    def test_parse_alarm_status_armed(self, mock_sigma_client):
        """Test armed status parsing."""
        status, bypass = mock_sigma_client.parse_alarm_status("OΠΛIΣMENO")
        assert status == "Armed"
        assert bypass is False

    def test_parse_alarm_status_armed_with_bypass(self, mock_sigma_client):
        """Test armed with bypass zones."""
        status, bypass = mock_sigma_client.parse_alarm_status("OΠΛIΣMENO ME ZΩNEΣ BYPASS")
        assert status == "Armed"
        assert bypass is True

    def test_parse_alarm_status_perimeter(self, mock_sigma_client):
        """Test perimeter armed status."""
        status, bypass = mock_sigma_client.parse_alarm_status("ΠEPIMETPIKH OΠΛIΣH")
        assert status == "Armed Perimeter"
        assert bypass is False

    def test_parse_alarm_status_perimeter_with_bypass(self, mock_sigma_client):
        """Test perimeter armed with bypass."""
        status, bypass = mock_sigma_client.parse_alarm_status("ΠEPIMETPIKH OΠΛIΣH ME ZΩNEΣ BYPASS")
        assert status == "Armed Perimeter"
        assert bypass is True

    def test_parse_alarm_status_unknown(self, mock_sigma_client):
        """Test unknown status returns None."""
        status, bypass = mock_sigma_client.parse_alarm_status("UNKNOWN STATUS")
        assert status is None
        assert bypass is None


class TestParseZonesHtml:
    """Tests for parse_zones_html method."""

    def test_parse_zones_html_disarmed(self, mock_sigma_client):
        """Test parsing zones HTML with disarmed status."""
        soup = BeautifulSoup(SAMPLE_ZONES_HTML, "html.parser")
        result = mock_sigma_client.parse_zones_html(soup)

        assert result["alarm_status"] == "AΦOΠΛIΣMENO"
        assert result["battery_volt"] == 13.5
        assert result["ac_power"] is True
        assert len(result["zones"]) == 3

    def test_parse_zones_html_armed(self, mock_sigma_client):
        """Test parsing zones HTML with armed status."""
        soup = BeautifulSoup(SAMPLE_ZONES_HTML_ARMED, "html.parser")
        result = mock_sigma_client.parse_zones_html(soup)

        assert result["alarm_status"] == "OΠΛIΣMENO"
        assert result["battery_volt"] == 12.8
        assert result["ac_power"] is True

    def test_parse_zones_html_perimeter(self, mock_sigma_client):
        """Test parsing zones HTML with perimeter status."""
        soup = BeautifulSoup(SAMPLE_ZONES_HTML_PERIMETER, "html.parser")
        result = mock_sigma_client.parse_zones_html(soup)

        assert result["alarm_status"] == "ΠEPIMETPIKH OΠΛIΣH"
        assert result["ac_power"] is False

    def test_parse_zones_html_zone_details(self, mock_sigma_client):
        """Test zone details are parsed correctly."""
        soup = BeautifulSoup(SAMPLE_ZONES_HTML, "html.parser")
        result = mock_sigma_client.parse_zones_html(soup)

        zones = result["zones"]
        assert zones[0]["zone"] == "1"
        assert zones[0]["description"] == "Front Door"
        assert zones[0]["status"] == "κλειστή"
        assert zones[0]["bypass"] == "OXI"

        assert zones[1]["status"] == "ανοικτή"
        assert zones[2]["bypass"] == "ΝΑΙ"

    def test_parse_zones_html_missing_table(self, mock_sigma_client):
        """Test handling HTML without zones table."""
        html = "<html><body><div>Τμήμα 1: AΦOΠΛIΣMENO</div></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        result = mock_sigma_client.parse_zones_html(soup)

        assert result["zones"] == []

    def test_parse_zones_html_missing_battery(self, mock_sigma_client):
        """Test handling HTML without battery info."""
        html = "<html><body><div>Τμήμα 1: AΦOΠΛIΣMENO</div></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        result = mock_sigma_client.parse_zones_html(soup)

        assert result["battery_volt"] is None


class TestRetryHtmlRequestDecorator:
    """Tests for retry_html_request decorator."""

    def test_retry_decorator_success_first_try(self):
        """Test decorator returns on first successful call."""
        call_count = 0

        @retry_html_request
        def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = success_func()
        assert result == "success"
        assert call_count == 1

    def test_retry_decorator_retries_on_attribute_error(self):
        """Test decorator retries on AttributeError."""
        call_count = 0

        @retry_html_request
        def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise AttributeError("test error")
            return "success"

        result = fail_then_succeed()
        assert result == "success"
        assert call_count == 3

    def test_retry_decorator_retries_on_index_error(self):
        """Test decorator retries on IndexError."""
        call_count = 0

        @retry_html_request
        def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise IndexError("test error")
            return "success"

        result = fail_then_succeed()
        assert result == "success"
        assert call_count == 2

    def test_retry_decorator_raises_after_max_attempts(self):
        """Test decorator raises RuntimeError after max attempts."""
        @retry_html_request
        def always_fail():
            raise TypeError("always fails")

        with pytest.raises(RuntimeError, match="HTML parsing failed after max attempts"):
            always_fail()


class TestSessionCreation:
    """Tests for session creation and configuration."""

    def test_create_session_has_retry_adapter(self, mock_sigma_client):
        """Test session is created with retry adapter."""
        session = mock_sigma_client._create_session()

        # Session should have adapters mounted
        assert "http://" in session.adapters
        assert "https://" in session.adapters


class TestClientInit:
    """Tests for SigmaClient initialization."""

    def test_client_strips_trailing_slash(self, mock_coordinator):
        """Test base_url trailing slash is stripped."""
        with patch("custom_components.sigma_connect_ha.sigma_client.requests.Session"):
            client = SigmaClient(
                base_url="http://192.168.1.100:5053/",
                username="admin",
                password="pass",
                pin="1234",
                coordinator=mock_coordinator,
                send_analytics=False,
            )
            assert client.base_url == "http://192.168.1.100:5053"

    def test_client_uses_password_as_pin_default(self, mock_coordinator):
        """Test PIN defaults to password if not provided."""
        with patch("custom_components.sigma_connect_ha.sigma_client.requests.Session"):
            client = SigmaClient(
                base_url="http://192.168.1.100:5053",
                username="admin",
                password="mypassword",
                pin=None,
                coordinator=mock_coordinator,
                send_analytics=False,
            )
            assert client.pin == "mypassword"

    def test_client_strips_pin_whitespace(self, mock_coordinator):
        """Test PIN whitespace is stripped."""
        with patch("custom_components.sigma_connect_ha.sigma_client.requests.Session"):
            client = SigmaClient(
                base_url="http://192.168.1.100:5053",
                username="admin",
                password="pass",
                pin="  1234  ",
                coordinator=mock_coordinator,
                send_analytics=False,
            )
            assert client.pin == "1234"


class TestAnalytics:
    """Tests for analytics functionality."""

    @patch("custom_components.sigma_connect_ha.sigma_client.requests.post")
    def test_post_analytics_sends_payload(self, mock_post):
        """Test analytics posts expected payload structure."""
        post_installation_analytics(
            base_url="http://192.168.1.100:5053",
            config={"zones": 5},
            version="1.1.0",
        )

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")

        assert "id" in payload
        assert payload["version"] == "1.1.0"
        assert payload["zones"] == 5

    @patch("custom_components.sigma_connect_ha.sigma_client.requests.post")
    def test_post_analytics_handles_failure_gracefully(self, mock_post):
        """Test analytics failure doesn't raise exception."""
        mock_post.side_effect = Exception("Network error")

        # Should not raise
        post_installation_analytics(
            base_url="http://192.168.1.100:5053",
            config={},
            version="1.0.0",
        )

    @patch("custom_components.sigma_connect_ha.sigma_client.requests.post")
    def test_post_analytics_generates_unique_hash(self, mock_post):
        """Test analytics generates a hash for the ID."""
        post_installation_analytics(
            base_url="http://192.168.1.100:5053",
            config={},
            version="1.0.0",
        )

        call_args = mock_post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")

        # Hash should be 64 characters (SHA256 hex)
        assert len(payload["id"]) == 64
        assert all(c in "0123456789abcdef" for c in payload["id"])
