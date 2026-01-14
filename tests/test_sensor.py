"""Tests for sensor.py."""
from unittest.mock import MagicMock
import pytest

from homeassistant.const import UnitOfElectricPotential

from custom_components.sigma_connect_ha.sensor import SigmaSensor
from custom_components.sigma_connect_ha.const import DOMAIN


class TestSigmaSensor:
    """Tests for SigmaSensor entity."""

    @pytest.fixture
    def sensor(self, mock_coordinator, mock_config_entry):
        """Create a sensor instance."""
        return SigmaSensor(
            coordinator=mock_coordinator,
            entry=mock_config_entry,
            name="Alarm Status",
            value_fn=lambda d: d.get("status"),
        )

    @pytest.fixture
    def battery_sensor(self, mock_coordinator, mock_config_entry):
        """Create a battery sensor instance."""
        return SigmaSensor(
            coordinator=mock_coordinator,
            entry=mock_config_entry,
            name="Battery Voltage",
            value_fn=lambda d: d.get("battery_volt"),
            unit=UnitOfElectricPotential.VOLT,
        )

    def test_sensor_unique_id_format(self, sensor, mock_config_entry):
        """Test sensor unique ID format."""
        expected_id = f"{DOMAIN}_{mock_config_entry.entry_id}_alarm_status"
        assert sensor._attr_unique_id == expected_id

    def test_sensor_name_has_sigma_prefix(self, sensor):
        """Test sensor name includes 'Sigma' prefix."""
        assert sensor._attr_name == "Sigma Alarm Status"

    def test_sensor_native_value_from_coordinator(self, sensor, sample_coordinator_data):
        """Test sensor value comes from coordinator data."""
        assert sensor.native_value == "Disarmed"

    def test_battery_sensor_has_unit(self, battery_sensor):
        """Test battery sensor has correct unit."""
        assert battery_sensor._attr_native_unit_of_measurement == UnitOfElectricPotential.VOLT

    def test_battery_sensor_value(self, battery_sensor):
        """Test battery sensor value."""
        assert battery_sensor.native_value == 13.5

    def test_sensor_device_info(self, sensor, mock_config_entry):
        """Test sensor device info structure."""
        device_info = sensor.device_info

        assert "identifiers" in device_info
        assert (DOMAIN, mock_config_entry.entry_id) in device_info["identifiers"]
        assert device_info["name"] == "Sigma Alarm"
        assert device_info["manufacturer"] == "Sigma"
        assert device_info["model"] == "Ixion"

    def test_sensor_with_none_value(self, mock_coordinator, mock_config_entry):
        """Test sensor handles None value gracefully."""
        mock_coordinator.data = {"status": None}

        sensor = SigmaSensor(
            coordinator=mock_coordinator,
            entry=mock_config_entry,
            name="Test",
            value_fn=lambda d: d.get("status"),
        )

        assert sensor.native_value is None


class TestZoneSensors:
    """Tests for zone-specific sensors."""

    def test_zone_status_sensor(self, mock_coordinator, mock_config_entry, sample_coordinator_data):
        """Test zone status sensor."""
        zone_id = "1"

        sensor = SigmaSensor(
            coordinator=mock_coordinator,
            entry=mock_config_entry,
            name=f"Zone {zone_id} - Front Door Status",
            value_fn=lambda d, zid=zone_id: next(
                z for z in d["zones"] if z["zone"] == zid
            )["status"],
        )

        assert sensor.native_value == "Closed"

    def test_zone_bypass_sensor(self, mock_coordinator, mock_config_entry, sample_coordinator_data):
        """Test zone bypass sensor."""
        zone_id = "3"

        sensor = SigmaSensor(
            coordinator=mock_coordinator,
            entry=mock_config_entry,
            name=f"Zone {zone_id} - Window Bypass",
            value_fn=lambda d, zid=zone_id: next(
                z for z in d["zones"] if z["zone"] == zid
            )["bypass"],
        )

        # Zone 3 has bypass=True in sample data
        assert sensor.native_value is True

    def test_zone_sensor_unique_id(self, mock_coordinator, mock_config_entry):
        """Test zone sensor unique ID format."""
        sensor = SigmaSensor(
            coordinator=mock_coordinator,
            entry=mock_config_entry,
            name="Zone 1 - Front Door Status",
            value_fn=lambda d: "test",
        )

        expected = f"{DOMAIN}_{mock_config_entry.entry_id}_zone_1_-_front_door_status"
        assert sensor._attr_unique_id == expected


class TestAcPowerSensor:
    """Tests for AC power sensor."""

    def test_ac_power_sensor_true(self, mock_coordinator, mock_config_entry, sample_coordinator_data):
        """Test AC power sensor with power available."""
        sensor = SigmaSensor(
            coordinator=mock_coordinator,
            entry=mock_config_entry,
            name="AC Power",
            value_fn=lambda d: d.get("ac_power"),
        )

        assert sensor.native_value is True

    def test_ac_power_sensor_false(self, mock_coordinator, mock_config_entry):
        """Test AC power sensor with power unavailable."""
        mock_coordinator.data = {"ac_power": False}

        sensor = SigmaSensor(
            coordinator=mock_coordinator,
            entry=mock_config_entry,
            name="AC Power",
            value_fn=lambda d: d.get("ac_power"),
        )

        assert sensor.native_value is False
