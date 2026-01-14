"""Shared fixtures for sigma_connect_ha tests."""
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from enum import Enum
import pytest

from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD

# Patch AlarmControlPanelState for older HA versions before any test imports
import homeassistant.components.alarm_control_panel as acp_module
if not hasattr(acp_module, "AlarmControlPanelState"):
    class AlarmControlPanelState(Enum):
        DISARMED = "disarmed"
        ARMED_AWAY = "armed_away"
        ARMED_HOME = "armed_home"
        ARMED_NIGHT = "armed_night"
        ARMED_VACATION = "armed_vacation"
        ARMED_CUSTOM_BYPASS = "armed_custom_bypass"
        PENDING = "pending"
        ARMING = "arming"
        DISARMING = "disarming"
        TRIGGERED = "triggered"

    acp_module.AlarmControlPanelState = AlarmControlPanelState


# Sample HTML responses for parsing tests
SAMPLE_ZONES_HTML = """
<!DOCTYPE html>
<html>
<head><title>Zones</title></head>
<body>
<div>Τμήμα 1: AΦOΠΛIΣMENO</div>
<div>Μπαταρία: 13.5 Volt</div>
<div>Παροχή 230V: ΝΑΙ</div>
<table class="normaltable">
    <tr><th>Zone</th><th>Description</th><th>Status</th><th>Bypass</th></tr>
    <tr><td>1</td><td>Front Door</td><td>κλειστή</td><td>OXI</td></tr>
    <tr><td>2</td><td>Back Door</td><td>ανοικτή</td><td>OXI</td></tr>
    <tr><td>3</td><td>Window</td><td>κλειστή</td><td>ΝΑΙ</td></tr>
</table>
</body>
</html>
"""

SAMPLE_ZONES_HTML_ARMED = """
<!DOCTYPE html>
<html>
<body>
<div>Τμήμα 1: OΠΛIΣMENO</div>
<div>Μπαταρία: 12.8 Volt</div>
<div>Παροχή 230V: ΝΑΙ</div>
<table class="normaltable">
    <tr><th>Zone</th><th>Description</th><th>Status</th><th>Bypass</th></tr>
    <tr><td>1</td><td>Front Door</td><td>κλειστή</td><td>OXI</td></tr>
</table>
</body>
</html>
"""

SAMPLE_ZONES_HTML_PERIMETER = """
<!DOCTYPE html>
<body>
<div>Τμήμα 1: ΠEPIMETPIKH OΠΛIΣH</div>
<div>Μπαταρία: 13.2 Volt</div>
<div>Παροχή 230V: OXI</div>
<table class="normaltable">
    <tr><th>Zone</th><th>Description</th><th>Status</th><th>Bypass</th></tr>
    <tr><td>1</td><td>Front Door</td><td>κλειστή</td><td>OXI</td></tr>
</table>
</body>
</html>
"""

SAMPLE_LOGIN_HTML = """
<!DOCTYPE html>
<html>
<body>
<form>
    <input type="hidden" name="gen_input" value="abcdefghijklmnop">
    <input type="text" name="username">
    <input type="password" name="password">
</form>
</body>
</html>
"""

SAMPLE_USER_HTML = """
<!DOCTYPE html>
<html>
<body>
<form>
    <input type="hidden" name="gen_input" value="1234567890123456">
</form>
</body>
</html>
"""

SAMPLE_PART_HTML = """
<!DOCTYPE html>
<html>
<body>
<a href="zones.html">Κατάσταση ζωνών</a>
</body>
</html>
"""


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.loop = asyncio.new_event_loop()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
    hass.async_create_task = MagicMock(side_effect=lambda coro: asyncio.ensure_future(coro, loop=hass.loop))
    hass.data = {}
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        CONF_HOST: "192.168.1.100",
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "password123",
        "pin": "1234",
    }
    entry.options = {}
    return entry


@pytest.fixture
def mock_config_entry_with_options():
    """Create a mock config entry with custom options."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        CONF_HOST: "192.168.1.100",
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "password123",
        "pin": "1234",
    }
    entry.options = {
        "update_interval": 15.0,
        "retry_total": 3,
        "max_consecutive_failures": 5,
        "enable_analytics": False,
    }
    return entry


@pytest.fixture
def sample_coordinator_data():
    """Sample data as returned by coordinator."""
    return {
        "status": "Disarmed",
        "zones_bypassed": None,
        "battery_volt": 13.5,
        "ac_power": True,
        "zones": [
            {"zone": "1", "description": "Front Door", "status": "Closed", "bypass": False},
            {"zone": "2", "description": "Back Door", "status": "Open", "bypass": False},
            {"zone": "3", "description": "Window", "status": "Closed", "bypass": True},
        ],
    }


@pytest.fixture
def mock_coordinator(mock_hass, sample_coordinator_data):
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.hass = mock_hass
    coordinator.data = sample_coordinator_data
    coordinator.lock = asyncio.Lock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_set_updated_data = MagicMock()
    return coordinator


@pytest.fixture
def mock_sigma_client(mock_coordinator):
    """Create a mock SigmaClient."""
    with patch("custom_components.sigma_connect_ha.sigma_client.requests.Session"):
        from custom_components.sigma_connect_ha.sigma_client import SigmaClient

        client = SigmaClient(
            base_url="http://192.168.1.100:5053",
            username="admin",
            password="password123",
            pin="1234",
            coordinator=mock_coordinator,
            send_analytics=False,
        )
        return client
