![HACS badge](https://img.shields.io/badge/HACS-Default-orange.svg)
![GitHub release](https://img.shields.io/github/v/release/phoinixgrr/sigma_connect_ha)
![GitHub stars](https://img.shields.io/github/stars/phoinixgrr/sigma_connect_ha?style=social)
[![Validate with HACS Action](https://github.com/phoinixgrr/sigma_connect_ha/actions/workflows/hacs-validation.yml/badge.svg)](https://github.com/phoinixgrr/sigma_connect_ha/actions/workflows/hacs-validation.yml)
[![Validate with hassfest Action](https://github.com/phoinixgrr/sigma_connect_ha/actions/workflows/hassfest-validation.yaml/badge.svg)](https://github.com/phoinixgrr/sigma_connect_ha/actions/workflows/hassfest-validation.yaml)
[![Tests](https://github.com/phoinixgrr/sigma_connect_ha/actions/workflows/tests.yml/badge.svg)](https://github.com/phoinixgrr/sigma_connect_ha/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/phoinixgrr/sigma_connect_ha/branch/main/graph/badge.svg)](https://codecov.io/gh/phoinixgrr/sigma_connect_ha)

# Sigma Alarm Integration for Home Assistant

![Logo with Red Eggs](./images/logo-small.png "Easter Logo")

This custom integration adds support for **Sigma alarm systems**, with the Ixion addon(IP) in Home Assistant.

It communicates with the alarm panel through HTTP requests and HTML parsing (web scraping), providing real-time insight into the panel's status and zones, as well as the ability to arm/disarm the system.

![Alarm System](./images/alarm.jpg "Sigma Alarm Panel")

## Features

- **Alarm status**: Armed, Disarmed, or Perimeter Armed
- **Arming/Disarming**: Full support for Away and Stay modes
- **Bypassed zones**: Clearly indicates which zones are bypassed
- **Zone sensors**: Open/Closed state per zone + bypass state
- **Battery voltage** and **AC power** monitoring
- **Two partitions (optional)**: a second alarm entity and zone sensors for panels with a PIN per partition

-![Demo HA Integration](./images/demo.png "Sigma Alarm Demo HA Integration")

## Installation

### Option 1: HACS (Recommended)

1. Go to **HACS > Integrations > ⋯ > Custom Repositories**
2. Add this repository: `https://github.com/phoinixgrr/sigma_connect_ha`
3. Category: **Integration**
4. Click **Add**
5. Then search for **Sigma Alarm** and install it

### Option 2: Manual

1. Copy the folder `custom_components/sigma_connect_ha/` to your Home Assistant `custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings > Devices & Services**
2. Click **Add Integration**
3. Search for **Sigma Alarm**
4. Enter your:
   - Alarm Panel IP address
   - Username
   - Password
   - Alarm PIN (keypad code), if different from the password
   - Partition 2 PIN, only for a second partition (see [Two Partitions](#two-partitions))

## Settings

After adding the integration, you can go to **Settings → Devices & Services → Sigma Alarm → Configure** to adjust:

<img src="./images/settings.png" alt="Sigma Alarm settings dialog" width="450">

| Option                   | Default | Description                                                 |
|--------------------------|:-------:|-------------------------------------------------------------|
| **Alarm PIN**            | —       | Keypad code (if different from web password)                |
| **Partition 2 PIN**      | —       | Keypad code of a second partition. Leave empty for one partition |
| **Polling Interval**     | 10 s    | How often Home Assistant checks the panel status (at least 60 s with a Partition 2 PIN) |
| **Send Analytics**       | On      | Send anonymous usage statistics to help improve the integration |

> **Note:** A restart of Home Assistant is required for changes to take effect.

## Two Partitions

For panels where each partition has its own PIN, such as AEOLUS with the Ixion module. Enter the second partition's PIN as **Partition 2 PIN**, during setup or under **Configure**, and restart Home Assistant.

This adds:

- **Sigma Alarm Partition 2**: a second alarm entity, armed and disarmed independently of partition 1
- Partition 2 alarm status, bypassed zones, and zone status and bypass sensors

Battery voltage and AC power belong to the whole panel, so they stay single sensors. Partition 1 keeps its existing entities and entity IDs. With the field empty, nothing changes.

Good to know:

- The polling interval is at least 60 seconds, because each check reads both partitions.
- Commands queue, so one automation can arm or disarm both partitions at once.
- A fully armed partition can't switch straight to perimeter mode. Disarm it first.
- Right after a command, zone sensors can show as unavailable for a few seconds until the next full read.


## How It Works

Sigma does not offer a public or documented API. This integration operates via **HTML scraping**, similar to how you would inspect your alarm panel via your browser.

We reverse-engineered the web interface used by the alarm's IP module:
- Captured the requests and tokens during login from the browser’s developer tools
- Reconstructed the flow in Python
- Submit credentials through encrypted payloads
- Select partitions and extract real-time data from plain HTML (status, zones, battery, etc.)

## Notes

- Your Home Assistant instance must be able to communicate with the Alarm IP.
- Sigma systems support IP connectivity via an additional network module: [https://sigmasec.gr/ixion-ip](https://sigmasec.gr/ixion-ip)
- Supports **full read/write** functionality:
  - Arm (Away)
  - Arm (Stay/Perimeter)
  - Disarm
- Tested with **Ixion v1.3.9** and:
  - **AEOLUS v12.0**
  - **S-PRO 32**
- Tested with **Axion v1.4.0** and:
  - **S-PRO 64**
- Two partitions tested with **Ixion v1.4.0** and:
  - **AEOLUS v11.7**
- ⚠️ No guarantees for other firmware versions.
- This is still in beta form. Your mileage will vary! 

## Security & Token Handling (Deep Dive)

Sigma alarm systems implement a custom **token-based challenge-response mechanism** during login, along with a **session-based authorization model** for subsequent interactions. Although no public API exists, we reverse-engineered the system by inspecting its web interface and mimicking browser behavior in Python.

### Discovery & Reverse Engineering

Sigma does not document any API. To build this integration, we reverse-engineered its IP module web interface:

- Using browser developer tools, we captured traffic to:
  - Identify the `gen_input` **login token**
  - Observe the JavaScript-based **encryption algorithm**
  - Track HTML-based workflows for zone status, PIN entry, and partitions

- The JavaScript-based encryption was reverse-engineered and replicated in Python. This allowed us to authenticate programmatically, just like the browser does.

### How Login & Token-Based Encryption Works

1. **GET /login.html** returns a one-time token in a hidden input field (`gen_input`).
2. A JavaScript function encrypts the user’s password using that token.
3. The browser submits:
   - `username`
   - `encrypted password` (hex)
   - `gen_input` (length of encrypted string)
4. The server verifies the password by reproducing the exact same encryption steps.

We ported this exact logic to Python to simulate browser behavior headlessly.

### Post-Login Session Behavior

Once logged in:

- The panel issues a **session cookie** (e.g. `SID=...`)
- This cookie is used for all subsequent interactions (e.g. viewing panel status, arming/disarming)
- The session remains valid for a period of time, or until logout

⚠️ All control actions depend entirely on this cookie being present and valid.

### Brute-Forcing Hidden Control Endpoints

Sigma's frontend **does not expose** any official control buttons (e.g., arm/disarm). However, after logging in, we began **probing common paths** and discovered undocumented endpoints like:

- `/arm.html` – Arms the system (Away)
- `/stay.html` – Arms perimeter/stay
- `/disarm.html` – Disarms the system
- `/done.html` – Shows a Hidden Menu 

-![Hidden Menu](./images/hiddenmenu.png "Hidden Menu")

These endpoints are not visible in the UI, but respond with a `200 OK` and successfully execute the command when accessed by a logged-in session.
( More may exist, feel free to reachout 😎 ) 

## Security Considerations

| Feature / Mechanism         | Status                            |
|-----------------------------|------------------------------------|
| Token replay protection     | ✅ One-time use per login           |
| Password protection         | ⚠️ Obfuscated, not securely encrypted |
| HTTPS/TLS                   | ❌ Not supported                    |
| Session hijack protection   | ❌ Not protected if cookie leaked   |
| Authenticated API access    | ❌ None — only via UI scraping      |

### Recommendations

- **DO NOT expose your alarm system to the public internet**
- Keep it on a secure VLAN or local-only network
- Never trust Sigma’s web interface for critical security boundaries — treat it as legacy
- Avoid shared/public Wi-Fi unless you’ve isolated communication via VPN or encrypted tunneling

##  Note on Web Interface Conflicts

While the Sigma integration is active and connected in Home Assistant, the alarm panel's web interface (login.html) may continuously log you out or prevent login. This is most likely due to the panel allowing only one active session per set of credentials.

To access the alarm’s web interface manually, **you will need to temporarily disable the Sigma integration in Home Assistant**. Once you're done using the web interface, you can re-enable the integration. 

## Feedback

Found a bug or need a feature? Open an issue or PR in the [GitHub repository](https://github.com/phoinixgrr/sigma_connect_ha)

## 🐣 Easter Egg

This project was developed as a hobby project during **Greek Easter 2025** — which is why you might notice a few symbolic red Easter eggs baked into the logo 🥚🇬🇷.

It’s a little nod to the timing of the project and a reminder that tech and tradition can happily coexist ✨

## Anonymous Analytics

This integration can optionally send a one-time anonymous report with basic system and config info (no personal data) to help improve the integration.
Disable it anytime from the "Enable analytics reporting" option in settings.

## Development & Testing

### Running the tests

Requires Python 3.11 or newer.

```bash
pip install -r requirements_test.txt
pytest tests/
```

### Simulated panel

`tests/simulator` contains a fake Sigma panel that runs on your machine over HTTP. It mimics what the integration relies on: the one-time login token with the encrypted password and PIN, the `SID` session cookie, one active session at a time, the status and zone pages, and the arm, disarm and stay endpoints. It has two styles:

- `classic`: one PIN, partitions selected through `part.cgi`.
- `pin_scoped`: one PIN per partition (Aeolus style).

The end-to-end tests run the real coordinator, client, sensors and alarm entities against it:

- `tests/test_simulated_single_pin.py` records the exact requests and entities of a single-partition setup. If a change makes one of these tests fail, it changes behaviour for single-partition users, so make sure that's intended.
- `tests/test_simulated_two_pin.py` covers two-partition (two PIN) setups.

```bash
pytest tests/test_simulated_single_pin.py tests/test_simulated_two_pin.py -v
```

The simulator's pages are synthetic: they follow what the integration's parsers expect, not pages captured from real firmware. Passing tests mean the integration's logic is right, not that a specific panel or firmware version works.

### Trying the integration without a panel

```bash
python -m tests.simulator                    # one partition, PIN 1234
python -m tests.simulator --mode pin_scoped  # two partitions, PINs 1234 and 5678
```

Add the integration in a development Home Assistant using the IP address of the machine running the simulator, username `admin`, password `secret` and PIN `1234` (and Partition 2 PIN `5678` for two partitions). The integration always connects to port 5053, so keep the default port. Anonymous analytics are on by default; turn them off in the integration options if you don't want test runs counted.

At the simulator's prompt you can change things as if from the keypad (`arm 1`, `open 1 2`), end the session (`expire`), or cause faults (`empty 2` for an empty zone table, `ignore 1` to ignore the next command, `fail zones.html 3` for HTTP 500s). `status` shows the current state.

### Reproducing a bug

`FakePanel` has switches for common failures: `expire_sessions()`, `empty_zones`, `ignore_commands`, `fail_requests` and `holding_commands()` (a slow panel). Use them to turn a reported problem into a test; the existing simulator tests show how.

## ☕ Support My Work

If you find this project helpful and want to support my work, feel free to donate via PayPal:

[https://paypal.me/amaziotis](https://paypal.me/amaziotis)
