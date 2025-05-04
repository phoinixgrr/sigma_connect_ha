import logging
import random
import re
import time
import uuid
import hashlib
import platform
import datetime
from typing import List, Dict, Tuple, Optional
import locale

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RETRY_TOTAL = 5
RETRY_BACKOFF_FACTOR = 0.5
RETRY_STATUS_FORCELIST = [500, 502, 503, 504]
RETRY_ATTEMPTS_FOR_HTML = 5

# Super‑retry parameters for arm / disarm / stay
MAX_ACTION_ATTEMPTS    = 5   # full‑flow retries
ACTION_BASE_DELAY      = 2   # sec – exponential back‑off multiplier
POST_ACTION_EXTRA_DELAY = 3  # sec – wait before verifying state

ANALYTICS_ENDPOINT = "https://hastats.qivocio.com/internal-api/analytics"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Generic HTML‑parse retry decorator
# ---------------------------------------------------------------------------

def retry_html_request(func):
    def wrapper(*args, **kwargs):
        for attempt in range(1, RETRY_ATTEMPTS_FOR_HTML + 1):
            try:
                return func(*args, **kwargs)
            except (AttributeError, IndexError, TypeError) as exc:
                logger.warning("HTML parse failed (%d/%d): %s", attempt, RETRY_ATTEMPTS_FOR_HTML, exc)
                time.sleep(RETRY_BACKOFF_FACTOR * (2 ** (attempt - 1)))
        raise RuntimeError("HTML parsing failed after max attempts")
    return wrapper

# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def post_installation_analytics(base_url: str, config: Optional[Dict[str, object]] = None) -> None:
    try:
        uid = str(uuid.getnode())
        raw_id = f"{base_url}:{uid}"
        unique_hash = hashlib.sha256(raw_id.encode()).hexdigest()

        try:
            import homeassistant.const as hass_const
            ha_version = hass_const.__version__
        except ImportError:
            ha_version = None

        config = config or {}

        payload = {
            "id": unique_hash,
            "panel": base_url,
            "version": "1.0.0",
            "ha_version": ha_version,
            "python": platform.python_version(),
            "os": platform.system(),
            "os_version": platform.platform(),
            "arch": platform.machine(),
            "time": datetime.datetime.utcnow().isoformat() + "Z",
            "locale": locale.getdefaultlocale()[0] if locale.getdefaultlocale() else None,
            "tz": time.tzname[0] if time.tzname else None,
            "zones": config.get("zones"),
            "config": {k: v for k, v in config.items() if k != "zones"},
        }

        requests.post(ANALYTICS_ENDPOINT, json=payload, timeout=3)
        logger.info("Sigma analytics posted successfully.")
    except Exception as e:
        logger.debug("Failed to post analytics: %s", e)

# ---------------------------------------------------------------------------
# Sigma alarm client
# ---------------------------------------------------------------------------

class SigmaClient:
    def __init__(self, base_url: str, username: str, password: str, send_analytics: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session: requests.Session = self._create_session()
        self._session_authenticated = False
        self._send_analytics = send_analytics
        self._analytics_sent = False
        self._config: Dict[str, object] = {}

    def _create_session(self) -> requests.Session:
        s = requests.Session()
        retry = Retry(
            total=RETRY_TOTAL,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_FORCELIST,
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        return s

    def logout(self) -> None:
        try:
            self.session.get(f"{self.base_url}/logout.html", timeout=5)
        except Exception:
            pass
        finally:
            self.session.close()
            self.session = self._create_session()

    @retry_html_request
    def _get_soup(self, path: str) -> BeautifulSoup:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url, timeout=5)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    def _encrypt(self, secret: str, token: str) -> Tuple[str, str]:
        S = list(range(256))
        j = 0
        for i in range(256):
            j = (j + S[i] + ord(token[i % len(token)])) % 256
            S[i], S[j] = S[j], S[i]
        i = j = 0
        num = random.randint(1, 7)
        prefix = token[1 : 1 + num]
        suffix_len = 14 - num - len(secret)
        suffix = token[num : num + suffix_len]
        newpass = prefix + secret + suffix + str(num) + str(len(secret))
        out = []
        for ch in newpass:
            i = (i + 1) % 256
            j = (j + S[i]) % 256
            S[i], S[j] = S[j], S[i]
            K = S[(S[i] + S[j]) % 256]
            out.append(chr(ord(ch) ^ K))
        cipher = "".join(out)
        return "".join(f"{ord(c):02x}" for c in cipher), str(len(cipher))

    @retry_html_request
    def _submit_login(self) -> None:
        soup = self._get_soup("login.html")
        token = soup.find("input", {"name": "gen_input"})["value"]
        encrypted, gen_val = self._encrypt(self.password, token)
        data = {
            "username": self.username,
            "password": encrypted,
            "gen_input": gen_val,
            "Submit": "Apply",
        }
        self.session.post(f"{self.base_url}/login.html", data=data, timeout=5).raise_for_status()

    @retry_html_request
    def _submit_pin(self) -> None:
        soup = self._get_soup("user.html")
        token = soup.find("input", {"name": "gen_input"})["value"]
        encrypted, gen_val = self._encrypt(self.password, token)
        data = {
            "password": encrypted,
            "gen_input": gen_val,
            "Submit": "code",
        }
        self.session.post(f"{self.base_url}/ucode", data=data, timeout=5).raise_for_status()

    def login(self) -> None:
        self._submit_login()
        self._submit_pin()
        self._session_authenticated = True

    def try_zones_directly(self):
        if not self._session_authenticated:
            logger.debug("Skipping session reuse: not authenticated.")
            return None
        try:
            soup = self.select_partition()
            zones_url = self._extract_zones_url(soup)
            zones_resp = self.session.get(f"{self.base_url}/{zones_url}", headers={"Referer": f"{self.base_url}/part.cgi"}, timeout=5)
            zones_resp.raise_for_status()
            zones_soup = BeautifulSoup(zones_resp.text, "html.parser")
            result = self.parse_zones_html(zones_soup)

            # Check if required data is present
            if not result.get("alarm_status") or result.get("battery_volt") is None or not result.get("zones"):
                logger.warning("Session reuse failed: zones.html content incomplete.")
                return None

            return result

        except Exception as e:
            logger.warning("Session reuse failed: %s", e)
            return None


    def safe_get_status(self):
        data = self.try_zones_directly()
        if not data:
            logger.info("Session expired or invalid — performing full login.")
            self.logout()
            self.login()
            soup = self.select_partition()
            zones_url = self._extract_zones_url(soup)
            zones_resp = self.session.get(f"{self.base_url}/{zones_url}", headers={"Referer": f"{self.base_url}/part.cgi"}, timeout=5)
            zones_resp.raise_for_status()
            zones_soup = BeautifulSoup(zones_resp.text, "html.parser")
            data = self.parse_zones_html(zones_soup)

        logger.info("Session reused or login successful.")
        
        # 👉 Send analytics only once and only if not yet sent
        if self._send_analytics and not getattr(self, "_analytics_sent", False):
            self._config["zones"] = len(data.get("zones", []))
            post_installation_analytics(self.base_url, config=self._config)
            self._analytics_sent = True

        return data


    def _extract_zones_url(self, soup: BeautifulSoup) -> str:
        link = soup.find("a", string=re.compile("ζωνών", re.I))
        return link["href"] if link and link.get("href") else "zones.html"\


    # --------------------------------------------------------------------- #
    # Partition / status helpers
    # --------------------------------------------------------------------- #

    @retry_html_request
    def select_partition(self, part_id: str = "1") -> BeautifulSoup:
        """Navigate to /panel and select a partition; returns its soup."""
        self.session.get(f"{self.base_url}/panel.html", timeout=5).raise_for_status()
        data = {"part": f"part{part_id}", "Submit": "code"}
        headers = {"Referer": f"{self.base_url}/panel.html"}
        resp = self.session.post(
            f"{self.base_url}/part.cgi", data=data, headers=headers, timeout=5
        )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")


    # One‑stop helper that **fetches + parses** under retry
    @retry_html_request
    def _fetch_partition_status(
        self, part_id: str = "1"
    ) -> Tuple[Optional[str], Optional[bool]]:
        soup = self.select_partition(part_id)
        raw = self.get_part_status(soup)["alarm_status"]
        return self.parse_alarm_status(raw)

    # --------------------------------------------------------------------- #
    # Utility conversions
    # --------------------------------------------------------------------- #

    def parse_zones_html(self, soup: BeautifulSoup) -> Dict[str, object]:
        """Extract alarm status, battery voltage, AC power, and zones from zones.html content."""
        text = soup.get_text("\n", strip=True)

        # 1. Alarm status from: "Τμήμα 1 : ΑΦΟΠΛΙΣΜΕΝΟ"
        alarm_match = re.search(r"Τμήμα\s*\d+\s*:\s*(.+)", text)
        alarm_status = alarm_match.group(1).strip() if alarm_match else None

        # 2. Battery: "Μπαταρία: 13.5 Volt"
        battery_match = re.search(r"Μπαταρία:\s*([\d.]+)\s*Volt", text)
        battery_volt = float(battery_match.group(1)) if battery_match else None

        # 3. AC Power: "Παροχή 230V: NAI"
        ac_match = re.search(r"Παροχή\s*230V:\s*(ΝΑΙ|NAI|OXI|Yes|No)", text, re.IGNORECASE)
        ac_power = self._to_bool(ac_match.group(1)) if ac_match else None

        # 4. Zones
        table = soup.find("table", class_="normaltable")
        zones = []
        if table:
            for row in table.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) >= 4:
                    zones.append({
                        "zone": cols[0].get_text(strip=True),
                        "description": cols[1].get_text(strip=True),
                        "status": cols[2].get_text(strip=True),
                        "bypass": cols[3].get_text(strip=True),
                    })

        return {
            "alarm_status": alarm_status,
            "battery_volt": battery_volt,
            "ac_power": ac_power,
            "zones": zones,
        }


    def parse_alarm_status(self, raw_status: str) -> Tuple[Optional[str], Optional[bool]]:
        mapping = {
            "AΦOΠΛIΣMENO": ("Disarmed", None),
            "OΠΛIΣMENO ME ZΩNEΣ BYPASS": ("Armed", True),
            "OΠΛIΣMENO": ("Armed", False),
            "ΠEPIMETPIKH OΠΛIΣH ME ZΩNEΣ BYPASS": ("Armed Perimeter", True),
            "ΠEPIMETPIKH OΠΛIΣH": ("Armed Perimeter", False),
        }
        return mapping.get(raw_status, (None, None))

    @staticmethod
    def _to_bool(val) -> Optional[bool]:
        if not val:
            return None
        v = str(val).strip().upper()
        if v in ("ΝΑΙ", "NAI", "YES", "TRUE"):
            return True
        if v in ("OXI", "NO", "FALSE"):
            return False
        return None

    @staticmethod
    def _to_openclosed(val) -> Optional[str]:
        if not val:
            return None
        v = str(val).strip().lower()
        if v == "κλειστή":
            return "Closed"
        if v == "ανοικτή":
            return "Open"
        return val

    # --------------------------------------------------------------------- #
    # HIGH‑LEVEL ACTION with full‑flow retry
    # --------------------------------------------------------------------- #

    def perform_action(self, action: str) -> bool:
        action_map = {"arm": "arm.html", "disarm": "disarm.html", "stay": "stay.html"}
        desired_map = {
            "arm": "Armed",
            "disarm": "Disarmed",
            "stay": "Armed Perimeter",
        }

        if action not in action_map:
            logger.error("Invalid action %r", action)
            return False

        for attempt in range(1, MAX_ACTION_ATTEMPTS + 1):
            try:
                logger.debug("Attempt %d/%d for %s", attempt, MAX_ACTION_ATTEMPTS, action)

                # Current state
                zones_url = self._extract_zones_url(self.select_partition())
                zones_resp = self.session.get(
                    f"{self.base_url}/{zones_url}",
                    headers={"Referer": f"{self.base_url}/part.cgi"},
                    timeout=5,
                )
                zones_resp.raise_for_status()
                zones_soup = BeautifulSoup(zones_resp.text, "html.parser")
                current_status, _ = self.parse_alarm_status(
                    self.parse_zones_html(zones_soup).get("alarm_status")
                )

                desired = desired_map[action]

                if current_status == desired:
                    logger.info("Alarm already in desired state (%s)", desired)
                    return True

                # Trigger action
                self.session.get(f"{self.base_url}/{action_map[action]}", timeout=5).raise_for_status()

                # Wait and re-check
                time.sleep(POST_ACTION_EXTRA_DELAY + attempt)
                zones_url = self._extract_zones_url(self.select_partition())
                zones_resp = self.session.get(
                    f"{self.base_url}/{zones_url}",
                    headers={"Referer": f"{self.base_url}/part.cgi"},
                    timeout=5,
                )
                zones_resp.raise_for_status()
                zones_soup = BeautifulSoup(zones_resp.text, "html.parser")
                new_state, _ = self.parse_alarm_status(
                    self.parse_zones_html(zones_soup).get("alarm_status")
                )

                if new_state == desired:
                    logger.info("Action '%s' successful on attempt %d", action, attempt)
                    return True

                logger.warning(
                    "Mismatch after '%s' (attempt %d): expected %s, got %s",
                    action,
                    attempt,
                    desired,
                    new_state,
                )

            except Exception as exc:
                logger.warning(
                    "Attempt %d/%d failed for '%s': %s",
                    attempt,
                    MAX_ACTION_ATTEMPTS,
                    action,
                    exc,
                )

            time.sleep(ACTION_BASE_DELAY * attempt)

        logger.error("Failed to perform action '%s' after %d attempts", action, MAX_ACTION_ATTEMPTS)
        return False
