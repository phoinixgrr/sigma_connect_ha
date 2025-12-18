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
import asyncio

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    CONF_RETRY_TOTAL,
    DEFAULT_RETRY_TOTAL,
    CONF_RETRY_BACKOFF_FACTOR,
    DEFAULT_RETRY_BACKOFF_FACTOR,
    CONF_RETRY_ATTEMPTS_FOR_HTML,
    DEFAULT_RETRY_ATTEMPTS_FOR_HTML,
    CONF_MAX_TOTAL_ATTEMPTS,
    DEFAULT_MAX_TOTAL_ATTEMPTS,
    CONF_MAX_ACTION_ATTEMPTS,
    DEFAULT_MAX_ACTION_ATTEMPTS,
    CONF_ACTION_BASE_DELAY,
    DEFAULT_ACTION_BASE_DELAY,
    CONF_POST_ACTION_EXTRA_DELAY,
    DEFAULT_POST_ACTION_EXTRA_DELAY,
    CONF_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    CONF_ENABLE_ANALYTICS,
    DEFAULT_ENABLE_ANALYTICS,
)
# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RETRY_TOTAL = 5
RETRY_BACKOFF_FACTOR = 0.5
RETRY_STATUS_FORCELIST = [500, 502, 503, 504]
RETRY_ATTEMPTS_FOR_HTML = 5

# Super-retry parameters for arm / disarm / stay
MAX_ACTION_ATTEMPTS    = 5   # full-flow retries
ACTION_BASE_DELAY      = 2   # sec – exponential back-off multiplier
POST_ACTION_EXTRA_DELAY = 3  # sec – wait before verifying state

ANALYTICS_ENDPOINT = "https://hastats.qivocio.com/internal-api/analytics"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Generic HTML-parse retry decorator
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

def post_installation_analytics(
    base_url: str,
    config: Optional[Dict[str, object]] = None,
    version: str = "unknown"
) -> None:
    try:
        uid = str(uuid.getnode())
        raw_id = f"{base_url}:{uid}"
        unique_hash = hashlib.sha256(raw_id.encode()).hexdigest()

        try:
            import homeassistant.const as hass_const
            ha_version = hass_const.__version__
        except ImportError:
            ha_version = None

        # start with every default
        full_config: Dict[str, object] = {
            CONF_UPDATE_INTERVAL:       DEFAULT_UPDATE_INTERVAL,
            CONF_RETRY_TOTAL:           DEFAULT_RETRY_TOTAL,
            CONF_RETRY_BACKOFF_FACTOR:  DEFAULT_RETRY_BACKOFF_FACTOR,
            CONF_RETRY_ATTEMPTS_FOR_HTML: DEFAULT_RETRY_ATTEMPTS_FOR_HTML,
            CONF_MAX_TOTAL_ATTEMPTS:    DEFAULT_MAX_TOTAL_ATTEMPTS,
            CONF_MAX_ACTION_ATTEMPTS:   DEFAULT_MAX_ACTION_ATTEMPTS,
            CONF_ACTION_BASE_DELAY:     DEFAULT_ACTION_BASE_DELAY,
            CONF_POST_ACTION_EXTRA_DELAY: DEFAULT_POST_ACTION_EXTRA_DELAY,
            CONF_MAX_CONSECUTIVE_FAILURES: DEFAULT_MAX_CONSECUTIVE_FAILURES,
            CONF_ENABLE_ANALYTICS:      DEFAULT_ENABLE_ANALYTICS,
        }
        # overlay with whatever actually got saved
        if config:
            full_config.update(config)

        payload = {
            "id": unique_hash,
            "panel": base_url,
            "version": version,
            "ha_version": ha_version,
            "python": platform.python_version(),
            "os": platform.system(),
            "os_version": platform.platform(),
            "arch": platform.machine(),
            "time": datetime.datetime.utcnow().isoformat() + "Z",
            "locale": locale.getdefaultlocale()[0] if locale.getdefaultlocale() else None,
            "tz": time.tzname[0] if time.tzname else None,
            "zones": config.get("zones") if config else None,
            "config": full_config,
        }

        requests.post(ANALYTICS_ENDPOINT, json=payload, timeout=3)
        logger.info("Sigma analytics posted successfully.")
    except Exception as e:
        logger.debug("Failed to post analytics: %s", e)


# ---------------------------------------------------------------------------
# Sigma alarm client
# ---------------------------------------------------------------------------

class SigmaClient:
    def __init__(self, base_url: str, username: str, password: str, pin: str | None, coordinator, send_analytics: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.pin = (pin or password).strip()
        self.session: requests.Session = self._create_session()
        self._session_authenticated = False
        self._send_analytics = send_analytics
        self._analytics_sent = False
        self._config: Dict[str, object] = {}
        self.coordinator = coordinator

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
        prefix = token[1:1 + num]
        suffix_len = 14 - num - len(secret)
        suffix = token[num:num + suffix_len]
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
        encrypted, gen_val = self._encrypt(self.pin, token)
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
            zones_resp = self.session.get(
                f"{self.base_url}/{zones_url}",
                headers={"Referer": f"{self.base_url}/part.cgi"},
                timeout=5,
            )
            zones_resp.raise_for_status()
            zones_soup = BeautifulSoup(zones_resp.text, "html.parser")
            result = self.parse_zones_html(zones_soup)

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
            zones_resp = self.session.get(
                f"{self.base_url}/{zones_url}",
                headers={"Referer": f"{self.base_url}/part.cgi"},
                timeout=5,
            )
            zones_resp.raise_for_status()
            zones_soup = BeautifulSoup(zones_resp.text, "html.parser")
            data = self.parse_zones_html(zones_soup)

        # Only fire analytics once we actually have a non-zero zone count (or give up after 3 tries)
        if self._send_analytics and not self._analytics_sent:
            attempts = 0
            while attempts < 3 and len(data.get("zones", [])) == 0:
                logger.warning(
                    "Zones still empty—retrying analytics in 1s (%d/3)", attempts + 1
                )
                time.sleep(1)
                retry = self.try_zones_directly()
                if retry:
                    data = retry
                attempts += 1

            if len(data.get("zones", [])) == 0:
                logger.warning("Giving up after 3 retries; sending analytics with zones=0")

            # stash and send
            self._config["zones"] = len(data.get("zones", []))
            post_installation_analytics(
                self.base_url,
                config=self._config,
                version=self._config.get("version", "unknown"),
            )
            self._analytics_sent = True

        return data
        
    def _extract_zones_url(self, soup: BeautifulSoup) -> str:
        link = soup.find("a", string=re.compile("ζωνών", re.I))
        return link["href"] if link and link.get("href") else "zones.html"

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

    @retry_html_request
    def _fetch_partition_status(
        self, part_id: str = "1"
    ) -> Tuple[Optional[str], Optional[bool]]:
        soup = self.select_partition(part_id)
        raw = self.get_part_status(soup)["alarm_status"]
        return self.parse_alarm_status(raw)

    def parse_zones_html(self, soup: BeautifulSoup) -> Dict[str, object]:
        text = soup.get_text("\n", strip=True)
        alarm_match = re.search(r"Τμήμα\s*\d+\s*:\s*(.+)", text)
        alarm_status = alarm_match.group(1).strip() if alarm_match else None
        battery_match = re.search(r"Μπαταρία:\s*([\d.]+)\s*Volt", text)
        battery_volt = float(battery_match.group(1)) if battery_match else None
        ac_match = re.search(r"Παροχή\s*230V:\s*(ΝΑΙ|NAI|OXI|Yes|No)", text, re.IGNORECASE)
        ac_power = self._to_bool(ac_match.group(1)) if ac_match else None

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
    # HIGH-LEVEL ACTION with full-flow retry
    # --------------------------------------------------------------------- #

    def perform_action(self, action: str) -> bool:
        action_map = {"arm": "arm.html", "disarm": "disarm.html", "stay": "stay.html"}
        desired_map = {"arm": "Armed", "disarm": "Disarmed", "stay": "Armed Perimeter"}

        if action not in action_map:
            logger.error("Invalid action %r", action)
            return False

        logger.debug(f"[LOCK] Waiting to perform '{action}'")
        asyncio.run_coroutine_threadsafe(
            self.coordinator.lock.acquire(),
            self.coordinator.hass.loop
        ).result()
        logger.debug(f"[LOCK] Acquired for '{action}'")

        try:
            desired = desired_map[action]

            # 1) If already desired, exit fast
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
            logger.debug(f"[ACTION] Current status before '{action}': {current_status}")
            if current_status == desired:
                logger.info(f"[ACTION] Alarm already in desired state ({desired})")
                return True

            # 2) Trigger ONCE
            logger.debug(f"[ACTION] Triggering '{action}' on panel.")
            self.session.get(f"{self.base_url}/{action_map[action]}", timeout=5).raise_for_status()

            # 3) Wait/poll until the panel reports the new state (fast polling allowed)
            deadline = time.time() + 60  # seconds (tune if you want)
            while time.time() < deadline:
                try:
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
                    logger.debug(f"[ACTION] Status after '{action}': {new_state}")

                    if new_state == desired:
                        logger.info(f"[ACTION SUCCESS] '{action}' reached {desired}")
                        # Optional: request one refresh, but don't block on multiple refreshes
                        asyncio.run_coroutine_threadsafe(
                            self.coordinator.async_request_refresh(),
                            self.coordinator.hass.loop
                        ).result()
                        return True
                except Exception as e:
                    logger.debug("[ACTION] Verify loop error (will retry): %s", e)

                time.sleep(0.5)

            logger.error(f"[ACTION FAILED] Timeout waiting for '{action}' to become {desired}")
            return False

        finally:
            self.coordinator.hass.loop.call_soon_threadsafe(self.coordinator.lock.release)
            logger.debug(f"[LOCK] Released for '{action}'")
