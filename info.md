# Sigma Alarm Integration for Home Assistant

This custom integration adds support for **Sigma Ixion alarm systems** in Home Assistant.

It communicates with the alarm panel through HTTP requests and HTML parsing (web scraping), providing real-time insight into the panel's status and zones.

![Alarm System](./images/alarm.jpg "Sigma Alarm Panel")

---

## Features

- **Alarm status**: Armed, Disarmed, or Perimeter Armed
- **Bypassed zones**: Clearly indicates which zones are bypassed
- **Zone sensors**: Open/Closed state per zone + bypass state
- **Battery voltage** and **AC power** monitoring

![Demo HA Integration](./images/demo.png "Sigma Alarm Demo HA Integration")

---

## Installation

### Option 1: HACS (Recommended)

1. Go to **HACS > Integrations > ⋯ > Custom Repositories**
2. Add this repository: `https://github.com/phoinixgrr/sigma_connect_ha`
3. Category: **Integration**
4. Click **Add**
5. Then search for **Sigma Alarm** and install it

### Option 2: Manual

1. Copy the folder `custom_components/sigma_alarm/` to your Home Assistant `custom_components/` directory
2. Restart Home Assistant

---

## Configuration

1. Go to **Settings > Devices & Services**
2. Click **Add Integration**
3. Search for **Sigma Alarm**
4. Enter your:
   - Alarm Panel IP address
   - Username
   - Password

---

## Notes

- This integration relies on **HTML scraping** due to the lack of an official API.
- Your Home Assistant instance must be able to communicate with the Alarm IP. Sigma systems support IP connectivity via an additional network module: [link](https://sigmasec.gr/ixion-ip)
- Supports **read-only** functionality for now — arming/disarming will be supported in the future.
- Tested with:
  - **AEOLUS v12.0**
  - **Ixion Ver: 1.3.9**
- ⚠️ No guarantees for other firmware versions.
- It is still in alpha form. Chances of working in your system are slim. 

---

## Directory Structure

```
custom_components/sigma_alarm/
├── __init__.py
├── alarm_control_panel.py
├── config_flow.py
├── const.py
├── coordinator.py
├── manifest.json
├── sensor.py
├── sigma_client.py
├── strings.json
└── translations/
    └── en.json
```

---

## To-Do / Planned

- [ ] Add arming/disarming support via alarm control panel entity
- [ ] More robust error handling & retries
- [ ] Better multi-partition(system 1/system2) support (if available)

---

## Feedback

Found a bug or need a feature? Open an issue or PR in the [GitHub repository](https://github.com/phoinixgrr/sigma_connect_ha)!


## ☕ Support My Work

If you find this project helpful and want to support my work, feel free to donate via PayPal:

[![Donate via PayPal](https://img.shields.io/badge/Donate-via%20PayPal-blue.svg)](https://paypal.me/amaziotis)
