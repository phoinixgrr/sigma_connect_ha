import logging
import random
import re
import time
from typing import List, Dict, Tuple, Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Constants
RETRY_TOTAL = 5
RETRY_BACKOFF_FACTOR = 0.5
RETRY_STATUS_FORCELIST = [500, 502, 503, 504]
RETRY_ATTEMPTS_FOR_HTML = 5
MAX_ACTION_ATTEMPTS = 5
ACTION_BASE_DELAY = 2
POST_ACTION_EXTRA_DELAY = 3
SESSION_TTL = 180  # seconds

class SigmaClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = self._create_session()
        self._last_login_time = 0

    def _create_session(self) -> requests.Session:
        s = requests.Session()
        retry = Retry(
            total=RETRY_TOTAL,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_FORCELIST,
            allowed_methods=["GET", "POST"]
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

    def _encrypt(self, secret: str, token: str) -> Tuple[str, str]:
        S = list(range(256))
        j = 0
        for i in range(256):
            j = (j + S[i] + ord(token[i % len(token)])) % 256
            S[i], S[j] = S[j], S[i]
        i = j = 0
        num = random.randint(1, 7)
        prefix = token[1:1+num]
        suffix_len = 14 - num - len(secret)
        suffix = token[num:num+suffix_len]
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

    def _login_flow(self) -> None:
        soup = self._raw_get("login.html")
        token = soup.find("input", {"name": "gen_input"})["value"]
        encrypted, gen_val = self._encrypt(self.password, token)
        self._raw_post("login.html", data={
            "username": self.username,
            "password": encrypted,
            "gen_input": gen_val,
            "Submit": "Apply",
        })
        soup = self._raw_get("user.html")
        token = soup.find("input", {"name": "gen_input"})["value"]
        encrypted, gen_val = self._encrypt(self.password, token)
        self._raw_post("ucode", data={
            "password": encrypted,
            "gen_input": gen_val,
            "Submit": "code",
        })
        self._last_login_time = time.time()

    def _ensure_session(func):
        def wrapper(self, *args, **kwargs):
            if time.time() - self._last_login_time > SESSION_TTL:
                logger.info("Session expired, re-logging in.")
                self.logout()
                self._login_flow()
            try:
                return func(self, *args, **kwargs)
            except (requests.HTTPError, AttributeError, IndexError, TypeError) as e:
                logger.warning("Session invalid, retrying login: %s", e)
                self.logout()
                self._login_flow()
                return func(self, *args, **kwargs)
        return wrapper

    def _raw_get(self, path: str) -> BeautifulSoup:
        resp = self.session.get(f"{self.base_url}/{path.lstrip('/')}", timeout=5)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    def _raw_post(self, path: str, data: dict) -> BeautifulSoup:
        resp = self.session.post(f"{self.base_url}/{path.lstrip('/')}", data=data, timeout=5)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    @_ensure_session
    def refresh_zones_page(self) -> Dict[str, Optional[object]]:
        """Fetch /zones.html and extract all needed information."""
        soup = self._raw_get("zones.html")
        header_p = soup.find("p")
        text = header_p.get_text(" ", strip=True) if header_p else soup.get_text(" ", strip=True)
        battery = re.search(r"(\d+\.?\d*)\s*Volt", text)
        ac_match = re.search(r"Παροχή\s*230V:\s*(ΝΑΙ|NAI|OXI|Yes|No)", text, re.IGNORECASE)
        alarm_status = header_p.find_all("span")[1].get_text(strip=True) if header_p else None

        parsed_status, bypass = self.parse_alarm_status(alarm_status)

        table = soup.find("table", class_="normaltable")
        zones = []
        if table:
            for row in table.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) >= 4:
                    zones.append({
                        "zone": cols[0].get_text(strip=True),
                        "description": cols[1].get_text(strip=True),
                        "status": self._to_openclosed(cols[2].get_text(strip=True)),
                        "bypass": self._to_bool(cols[3].get_text(strip=True)),
                    })

        return {
            "status": parsed_status,
            "zones_bypassed": bypass,
            "battery_volt": float(battery.group(1)) if battery else None,
            "ac_power": self._to_bool(ac_match.group(1)) if ac_match else None,
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
        v = str(val).strip().upper()
        if v in ("ΝΑΙ", "NAI", "YES", "TRUE"):
            return True
        if v in ("OXI", "NO", "FALSE"):
            return False
        return None

    @staticmethod
    def _to_openclosed(val) -> Optional[str]:
        v = str(val).strip().lower()
        if v == "κλειστή":
            return "Closed"
        if v == "ανοικτή":
            return "Open"
        return val

    @_ensure_session
    def perform_action(self, action: str) -> bool:
        action_map = {"arm": "arm.html", "disarm": "disarm.html", "stay": "stay.html"}
        desired_map = {"arm": "Armed", "disarm": "Disarmed", "stay": "Armed Perimeter"}

        if action not in action_map:
            logger.error("Invalid action: %s", action)
            return False

        for attempt in range(1, MAX_ACTION_ATTEMPTS + 1):
            try:
                current = self.refresh_zones_page()["status"]
                if current == desired_map[action]:
                    logger.info("Already in desired state: %s", current)
                    return True

                self._raw_get(action_map[action])
                time.sleep(POST_ACTION_EXTRA_DELAY + attempt)
                new_state = self.refresh_zones_page()["status"]

                if new_state == desired_map[action]:
                    logger.info("Action '%s' successful", action)
                    return True

                logger.warning("Mismatch after action '%s', retrying", action)
            except Exception as e:
                logger.warning("Attempt %d failed for action '%s': %s", attempt, action, e)

            time.sleep(ACTION_BASE_DELAY * attempt)

        logger.error("Failed to perform action '%s' after retries", action)
        return False