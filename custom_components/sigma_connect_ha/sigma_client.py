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


class SigmaClient:
    """Resilient HTTP client for Sigma alarm panels."""

    # --------------------------------------------------------------------- #
    # Session / init
    # --------------------------------------------------------------------- #

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session: requests.Session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Return a requests.Session with automatic HTTP retries."""
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
        """Best‑effort logout & fresh session (used at every full‑flow retry)."""
        try:
            self.session.get(f"{self.base_url}/logout.html", timeout=5)
        except Exception:
            pass
        finally:
            self.session.close()
            self.session = self._create_session()

    def _is_login_page(self, html: str) -> bool:
        """Detect if session expired and login page is shown."""
        return "login.html" in html.lower() or "gen_input" in html.lower()

    def _smart_get(self, path: str, headers: dict = None) -> str:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url, timeout=5, headers=headers)
        if self._is_login_page(resp.text):
            logger.warning("Session expired. Re‑logging in...")
            self.logout()
            self.login()
            resp = self.session.get(url, timeout=5, headers=headers)
            if self._is_login_page(resp.text):
                logger.error("Login failed after retry")
                raise RuntimeError("Login failed after retry")  # 🔥 Bubble up!
        resp.raise_for_status()
        return resp.text

    def _smart_post(self, path: str, data: dict = None, headers: dict = None) -> str:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.post(url, data=data, headers=headers, timeout=5)
        if self._is_login_page(resp.text):
            logger.warning("Session expired. Re‑logging in...")
            self.logout()
            self.login()
            resp = self.session.post(url, data=data, headers=headers, timeout=5)
            if self._is_login_page(resp.text):
                logger.error("Login failed after retry")
                raise RuntimeError("Login failed after retry")  # 🔥 Bubble up!
        resp.raise_for_status()
        return resp.text

    # --------------------------------------------------------------------- #
    # Low‑level helpers
    # --------------------------------------------------------------------- #

    @retry_html_request
    def _get_soup(self, path: str) -> BeautifulSoup:
        html = self._smart_get(path)
        return BeautifulSoup(html, "html.parser")

    # --- RC4‑style password obfuscation ----------------------------------

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

    # --------------------------------------------------------------------- #
    # Login flow
    # --------------------------------------------------------------------- #

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
        self._smart_post("login.html", data=data)

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
        self._smart_post("ucode", data=data)

    def login(self) -> None:
        """Full login (HTML form + PIN)."""
        self._submit_login()
        self._submit_pin()

    # --------------------------------------------------------------------- #
    # Partition / status helpers
    # --------------------------------------------------------------------- #

    @retry_html_request
    def select_partition(self, part_id: str = "1") -> BeautifulSoup:
        """Navigate to /panel and select a partition; returns its soup."""
        self._smart_get("panel.html")
        data = {"part": f"part{part_id}", "Submit": "code"}
        html = self._smart_post("part.cgi", data=data, headers={"Referer": f"{self.base_url}/panel.html"})
        return BeautifulSoup(html, "html.parser")

    def get_part_status(self, soup: BeautifulSoup) -> Dict[str, Optional[object]]:
        """Extract alarm status, battery voltage, AC power from partition page."""
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

    def get_zones(self, soup: BeautifulSoup) -> List[Dict[str, str]]:
        """Fetch the zones table and parse zone statuses."""
        link = soup.find("a", string=re.compile("ζωνών", re.I))
        url = link["href"] if link else "zones.html"
        html = self._smart_get(url, headers={"Referer": f"{self.base_url}/part.cgi"})
        table = BeautifulSoup(html, "html.parser").find("table", class_="normaltable")
        zones = []
        if table:
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

    @retry_html_request
    def _fetch_partition_status(self, part_id: str = "1") -> Tuple[Optional[str], Optional[bool]]:
        soup = self.select_partition(part_id)
        raw = self.get_part_status(soup)["alarm_status"]
        return self.parse_alarm_status(raw)

    # --------------------------------------------------------------------- #
    # Utility conversions
    # --------------------------------------------------------------------- #

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
        """
        Arm / Disarm / Stay with full‑flow retry.
        Returns True once the desired end‑state is confirmed.
        """
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

                soup = self.select_partition()
                current, _ = self.parse_alarm_status(self.get_part_status(soup)["alarm_status"])
                desired = desired_map[action]

                if current == desired:
                    logger.info("Alarm already in desired state (%s)", desired)
                    return True

                self._smart_get(action_map[action])

                time.sleep(POST_ACTION_EXTRA_DELAY + attempt)
                new_soup = self.select_partition()
                new_state, _ = self.parse_alarm_status(self.get_part_status(new_soup)["alarm_status"])

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