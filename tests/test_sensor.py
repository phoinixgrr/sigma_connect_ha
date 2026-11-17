"""Tests for sensor.py."""
from unittest.mock import MagicMock
import pytest

from homeassistant.const import UnitOfElectricPotential
from homeassistant.components.alarm_control_panel import AlarmControlPanelState

from custom_components.sigma_connect_ha.sensor import SigmaSensor, PartitionSensor, async_setup_entry
from custom_components.sigma_connect_ha.alarm_control_panel import PartitionAlarmPanel as SigmaAlarmPanel
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



def partition_data(data, partition):
    return data if partition == "1" else data["partition_2"]


@pytest.mark.parametrize("partition", ["1", "2"])
def test_missing_zones_hide_stale_zone_values_but_preserve_fresh_alarm_state(
    mock_coordinator, mock_config_entry, partition
):
    mock_coordinator.last_update_success = True
    mock_coordinator.data = {
        "status": "Disarmed", "zones_bypassed": None,
        "battery_volt": None, "ac_power": None, "zones_available": partition != "1",
        "zones": [{"zone": "1", "status": "Closed", "bypass": False}],
        "partition_2": {
            "status": "Disarmed", "zones_bypassed": None, "zones_available": partition != "2",
            "zones": [{"zone": "21", "status": "Closed", "bypass": False}],
        },
    }
    zid = "1" if partition == "1" else "21"
    readers = []
    zones = []
    for key in ("status", "bypass"):
        reader = MagicMock(side_effect=lambda data, key=key: next(
            z[key] for z in partition_data(data, partition)["zones"] if z["zone"] == zid
        ))
        readers.append(reader)
        zones.append(PartitionSensor(
            mock_coordinator, mock_config_entry, f"Zone {zid} {key}", reader,
            partition=partition, requires_zones=True,
        ))
    status = PartitionSensor(
        mock_coordinator, mock_config_entry, "Alarm Status",
        lambda data: partition_data(data, partition).get("status"), partition=partition,
    )
    aggregate = PartitionSensor(
        mock_coordinator, mock_config_entry, "Zones Bypassed",
        lambda data: partition_data(data, partition).get("zones_bypassed"), partition=partition,
    )
    alarm = SigmaAlarmPanel(mock_coordinator, mock_config_entry, partition)

    for zone, reader in zip(zones, readers):
        assert zone.available is False
        assert zone.native_value is None
        reader.assert_not_called()
    assert status.available is True
    assert status.native_value == "Disarmed"
    assert aggregate.available is True
    assert aggregate.native_value is None
    assert alarm.available is True
    assert alarm.alarm_state == AlarmControlPanelState.DISARMED

    # Fresh values restore the existing entities, without changing their IDs.
    unique_ids = [zone._attr_unique_id for zone in zones]
    current = partition_data(mock_coordinator.data, partition)
    current["zones_available"] = True
    current["zones"] = [{"zone": zid, "status": "Open", "bypass": True}]
    assert all(zone.available is True for zone in zones)
    assert [zone.native_value for zone in zones] == ["Open", True]
    assert [zone._attr_unique_id for zone in zones] == unique_ids


@pytest.mark.asyncio
async def test_setup_marks_both_partitions_zone_entities_and_keeps_unique_ids(
    mock_hass, mock_coordinator, mock_config_entry
):
    mock_coordinator.last_update_success = True
    mock_coordinator.partition_ids = ("1", "2")
    mock_coordinator.data = {
        "status": "Disarmed", "zones_bypassed": False,
        "battery_volt": 13.7, "ac_power": True, "zones_available": True,
        "zones": [{"zone": "1", "description": "Front Door", "status": "Closed", "bypass": False}],
        "partition_2": {
            "status": "Disarmed", "zones_bypassed": False, "zones_available": True,
            "zones": [{"zone": "21", "description": "Office", "status": "Closed", "bypass": False}],
        },
    }
    mock_hass.data = {DOMAIN: {mock_config_entry.entry_id: {"coordinator": mock_coordinator}}}
    add_entities = MagicMock()
    await async_setup_entry(mock_hass, mock_config_entry, add_entities)
    entities = {entity._attr_name: entity for entity in add_entities.call_args.args[0]}
    zone_names = (
        "Sigma Zone 1 - Front Door Status", "Sigma Zone 1 - Front Door Bypass",
        "Sigma Partition 2 Zone 21 - Office Status", "Sigma Partition 2 Zone 21 - Office Bypass",
    )
    prefix = f"{DOMAIN}_{mock_config_entry.entry_id}_"
    expected_ids = (
        prefix + "zone_1_-_front_door_status", prefix + "zone_1_-_front_door_bypass",
        prefix + "partition_2_zone_21_-_office_status", prefix + "partition_2_zone_21_-_office_bypass",
    )
    assert [entities[name]._attr_unique_id for name in zone_names] == list(expected_ids)
    assert [entities[name].native_value for name in zone_names] == ["Closed", False, "Closed", False]

    # A missing table on partition 2 must not hide partition 1 or shared power.
    second = mock_coordinator.data["partition_2"]
    second["zones_available"] = False
    for name in zone_names[:2]:
        assert entities[name].available is True
    for name in zone_names[2:]:
        assert entities[name].available is False
        assert entities[name].native_value is None
    for name, value in (
        ("Sigma Alarm Status", "Disarmed"), ("Sigma Partition 2 Alarm Status", "Disarmed"),
        ("Sigma Battery Voltage", 13.7), ("Sigma AC Power", True),
        ("Sigma Zones Bypassed", False), ("Sigma Partition 2 Zones Bypassed", False),
    ):
        assert entities[name].available is True
        assert entities[name].native_value == value

    mock_coordinator.data["zones_available"] = False
    for name in zone_names:
        assert entities[name].available is False
        assert entities[name].native_value is None
    assert [entities[name]._attr_unique_id for name in zone_names] == list(expected_ids)


@pytest.mark.parametrize("partition", ["1", "2"])
def test_unavailable_partition_still_hides_all_its_entities(
    mock_coordinator, mock_config_entry, partition
):
    mock_coordinator.last_update_success = True
    mock_coordinator.data = {
        "available": partition != "1", "status": "Disarmed", "zones_available": True,
        "zones": [{"zone": "1", "status": "Closed"}],
        "partition_2": {
            "available": partition != "2", "status": "Disarmed", "zones_available": True,
            "zones": [{"zone": "21", "status": "Closed"}],
        },
    }
    sensor = PartitionSensor(
        mock_coordinator, mock_config_entry, "Alarm Status",
        lambda data: partition_data(data, partition).get("status"), partition=partition,
    )
    alarm = SigmaAlarmPanel(mock_coordinator, mock_config_entry, partition)
    assert sensor.available is False
    assert alarm.available is False
