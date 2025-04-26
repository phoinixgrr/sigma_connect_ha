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

class SigmaClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = self._create_session()

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

    def _ensure_logged_in(func):
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except (AttributeError, IndexError, TypeError, requests.HTTPError) as e:
                logger.warning("Session expired or invalid, retrying login: %s", e)
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

    @_ensure_logged_in
    def select_partition(self, part_id: str = "1") -> BeautifulSoup:
        self._raw_get("panel.html")
        return self._raw_post("part.cgi", data={"part": f"part{part_id}", "Submit": "code"})

    @_ensure_logged_in
    def get_part_status(self, soup: BeautifulSoup) -> Dict[str, Optional[object]]:
        p = soup.find("p")
        alarm_status = p.find_all("span")[1].get_text(strip=True) if p else None
        text = soup.get_text("\n", strip=True)
        battery = re.search(r"(\d+\.?\d*)\s*Volt", text)
        ac_match = re.search(r"Παροχή\s*230V:\s*(ΝΑΙ|NAI|OXI|Yes|No)", text, re.IGNORECASE)
        return {
            "alarm_status": alarm_status,
            "battery_volt": float(battery.group(1)) if battery else None,
            "ac_power": self._to_bool(ac_match.group(1)) if ac_match else None,
        }

    @_ensure_logged_in
    def get_zones(self, soup: BeautifulSoup) -> List[Dict[str, str]]:
        link = soup.find("a", string=re.compile("ζωνών", re.I))
        url = link["href"] if link else "zones.html"
        table = self._raw_get(url).find("table", class_="normaltable")
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
        return zones

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

    def perform_action(self, action: str) -> bool:
        action_map = {"arm": "arm.html", "disarm": "disarm.html", "stay": "stay.html"}
        desired_map = {"arm": "Armed", "disarm": "Disarmed", "stay": "Armed Perimeter"}

        if action not in action_map:
            logger.error("Invalid action %r", action)
            return False

        for attempt in range(1, MAX_ACTION_ATTEMPTS + 1):
            try:
                soup = self.select_partition()
                current, _ = self.parse_alarm_status(self.get_part_status(soup)["alarm_status"])
                if current == desired_map[action]:
                    logger.info("Already in desired state: %s", current)
                    return True

                self._raw_get(action_map[action])
                time.sleep(POST_ACTION_EXTRA_DELAY + attempt)
                soup = self.select_partition()
                new_state, _ = self.parse_alarm_status(self.get_part_status(soup)["alarm_status"])

                if new_state == desired_map[action]:
                    logger.info("Action '%s' successful", action)
                    return True

                logger.warning("Mismatch after action '%s', retrying", action)
            except Exception as e:
                logger.warning("Attempt %d failed: %s", attempt, e)

            time.sleep(ACTION_BASE_DELAY * attempt)

        logger.error("Failed to perform action '%s' after retries", action)
        return False
