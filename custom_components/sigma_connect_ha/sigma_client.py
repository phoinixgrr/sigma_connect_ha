import logging
import random
import re
import time
from typing import List, Dict, Tuple, Optional

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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Generic HTML‑parse retry decorator
# ---------------------------------------------------------------------------

def retry_html_request(func):
    """Retry any HTML‑dependent call on Attribute / Index / Type errors."""

    def wrapper(*args, **kwargs):
        for attempt in range(1, RETRY_ATTEMPTS_FOR_HTML + 1):
            try:
                return func(*args, **kwargs)
            except (AttributeError, IndexError, TypeError) as exc:
                logger.warning(
                    "HTML parse failed (%d/%d): %s",
                    attempt,
                    RETRY_ATTEMPTS_FOR_HTML,
                    exc,
                )
                time.sleep(RETRY_BACKOFF_FACTOR * (2 ** (attempt - 1)))
        raise RuntimeError("HTML parsing failed after max attempts")

    return wrapper

# ---------------------------------------------------------------------------
# Sigma alarm client
# ---------------------------------------------------------------------------

# --------------------------------------------------------------------- #
# Session / init
# --------------------------------------------------------------------- #

class SigmaClient:
    """Resilient HTTP client for Sigma alarm panels."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session: requests.Session = self._create_session()
        # --------------------------------------------------------------------- #
# Low‑level helpers
# --------------------------------------------------------------------- #
        self.logged_in = False

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
            self.logged_in = False

    @retry_html_request
    def _get_soup(self, path: str) -> BeautifulSoup:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url, timeout=5)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    # --------------------------------------------------------------------- #
    # RC4‑style password obfuscation
    # --------------------------------------------------------------------- #
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

    # --------------------------------------------------------------------- #
    # Login flow
    # --------------------------------------------------------------------- #
    def login(self) -> None:
        if self.logged_in:
            return
        self._submit_login()
        self._submit_pin()
        self.logged_in = True

    @retry_html_request
    def select_partition(self, part_id: str = "1") -> BeautifulSoup:
        self.session.get(f"{self.base_url}/panel.html", timeout=5).raise_for_status()
        data = {"part": f"part{part_id}", "Submit": "code"}
        headers = {"Referer": f"{self.base_url}/panel.html"}
        resp = self.session.post(
            f"{self.base_url}/part.cgi", data=data, headers=headers, timeout=5
        )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    def get_part_status(self, soup: BeautifulSoup) -> Dict[str, Optional[object]]:
        p = soup.find("p")
        spans = p.find_all("span") if p else []
        alarm_status = spans[1].get_text(strip=True) if len(spans) >= 2 else None

        text = soup.get_text("\n", strip=True)
        battery = re.search(r"(\d+\.?\d*)\s*Volt", text)
        ac_match = re.search(r"Παροχή\s*230V:\s*(ΝΑΙ|NAI|OXI|Yes|No)", text, re.IGNORECASE)

        return {
            "alarm_status": alarm_status,
            "battery_volt": float(battery.group(1)) if battery else None,
            "ac_power": self._to_bool(ac_match.group(1)) if ac_match else None,
        }

    def get_zones(self, soup: BeautifulSoup) -> List[Dict[str, str]]:
        zones = []
        table = soup.find("table", class_=lambda x: x and "normaltable" in x)
        if not table:
            logger.warning("No zone table found in zones.html")
            return zones

        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) >= 4:
                zones.append(
                    {
                        "zone": cols[0].get_text(strip=True),
                        "description": cols[1].get_text(strip=True),
                        "status": cols[2].get_text(strip=True),
                        "bypass": cols[3].get_text(strip=True),
                    }
                )
        return zones


    def get_all_from_zones(self) -> Tuple[List[Dict[str, str]], Dict[str, Optional[object]]]:
        for attempt in range(1, RETRY_ATTEMPTS_FOR_HTML + 1):
            try:
                url = f"{self.base_url}/zones.html"
                headers = {"Referer": f"{self.base_url}/part.cgi"}
                resp = self.session.get(url, timeout=5, headers=headers)

                # ✅ Ensure correct character decoding (Greek)
                resp.encoding = "ISO-8859-7"
                resp.raise_for_status()

                # ✅ Save raw HTML for inspection
                with open("/config/zones_debug.html", "w", encoding="utf-8") as f:
                    f.write(resp.text)

                soup = BeautifulSoup(resp.text, "html.parser")

                # 💬 DEBUG
                logger.debug("zones.html (attempt %d) raw response snippet:\n%s", attempt, soup.get_text(strip=True)[:200])

                # ✅ moved after soup is ready
                zones = self.get_zones(soup)

                full_text = soup.get_text(" ", strip=True)
                alarm_match = re.search(r"Τμήμα\s*\d+\s*:\s*([^\n\r]+)", full_text, re.IGNORECASE)
                battery_match = re.search(r"Μπαταρία:\s*([\d.,]+)\s*Volt", full_text, re.IGNORECASE)
                ac_match = re.search(r"Παροχή\s*230V:\s*(ΝΑΙ|NAI|OXI|Yes|No)", full_text, re.IGNORECASE)

                logger.debug("alarm_match=%r battery_match=%r ac_match=%r", alarm_match, battery_match, ac_match)

                alarm_status = alarm_match.group(1).strip() if alarm_match else None
                battery_volt = float(battery_match.group(1).replace(",", ".")) if battery_match else None
                ac_power = self._to_bool(ac_match.group(1)) if ac_match else None

                logger.debug("Parsed alarm_status=%s, battery_volt=%s, ac_power=%s", alarm_status, battery_volt, ac_power)

                if not alarm_status or battery_volt is None or not zones:
                    logger.warning("Incomplete data after parsing attempt %d", attempt)
                    logger.debug(
                        "Debug dump: alarm_status=%r, battery_volt=%r, zones=%d",
                        alarm_status, battery_volt, len(zones)
                    )
                    raise ValueError("Incomplete data")

                return zones, {
                    "alarm_status": alarm_status,
                    "battery_volt": battery_volt,
                    "ac_power": ac_power,
                }

            except Exception as ex:
                logger.warning("Attempt %d failed to parse zones.html: %s", attempt, ex)
                time.sleep(RETRY_BACKOFF_FACTOR * (2 ** (attempt - 1)))

        raise RuntimeError("Failed to fetch valid data from zones.html after retries")


    # --------------------------------------------------------------------- #
    # Fast zone refresh skipping login if already authenticated
    # --------------------------------------------------------------------- #
    @retry_html_request
    def refresh_zones_only(self) -> List[Dict[str, str]]:
        if not self.logged_in:
            raise RuntimeError("Cannot refresh zones: not logged in.")
        resp = self.session.get(f"{self.base_url}/zones.html", timeout=5)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return self.get_zones(soup)

    @retry_html_request
    def _fetch_partition_status(self, part_id: str = "1") -> Tuple[Optional[str], Optional[bool]]:
        soup = self.select_partition(part_id)
        raw = self.get_part_status(soup)["alarm_status"]
        return self.parse_alarm_status(raw)

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
                self.logout()
                self.login()
                current, _ = self._fetch_partition_status()
                desired = desired_map[action]

                if current == desired:
                    logger.info("Alarm already in desired state (%s)", desired)
                    return True

                self.session.get(f"{self.base_url}/{action_map[action]}", timeout=5).raise_for_status()
                time.sleep(POST_ACTION_EXTRA_DELAY + attempt)

                # ✅ Re-login again to reinitialize session (action invalidates it)
                self.logout()
                self.login()
                new_state, _ = self._fetch_partition_status()

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
