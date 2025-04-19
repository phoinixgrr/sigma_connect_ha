import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import re
import random
import time
import logging

RETRY_TOTAL = 5
RETRY_BACKOFF_FACTOR = 0.5
RETRY_STATUS_FORCELIST = [500, 502, 503, 504]
RETRY_ATTEMPTS_FOR_HTML = 5

logger = logging.getLogger(__name__)


def retry_html_request(func):
    """Decorator to retry HTML‐parsing functions on parse failures."""
    def wrapper(*args, **kwargs):
        for attempt in range(1, RETRY_ATTEMPTS_FOR_HTML + 1):
            try:
                return func(*args, **kwargs)
            except (AttributeError, IndexError, TypeError) as e:
                logger.warning("HTML parse failed (attempt %d/%d): %s", attempt, RETRY_ATTEMPTS_FOR_HTML, e)
                time.sleep(RETRY_BACKOFF_FACTOR * (2 ** (attempt - 1)))
        raise RuntimeError("HTML parsing failed after max attempts")
    return wrapper


class SigmaClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()

        retry = Retry(
            total=RETRY_TOTAL,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_FORCELIST,
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    @retry_html_request
    def _get_soup(self, path: str) -> BeautifulSoup:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url, timeout=5)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    def _encrypt(self, secret: str, token: str):
        # Same RC4‐style obfuscation...
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
    def _submit_login(self):
        soup = self._get_soup("login.html")
        token = soup.find("input", {"name": "gen_input"})["value"]
        encrypted, gen_val = self._encrypt(self.password, token)
        data = {
            "username":    self.username,
            "password":    encrypted,
            "gen_input":   gen_val,
            "Submit":      "Apply",
        }
        self.session.post(f"{self.base_url}/login.html", data=data, timeout=5).raise_for_status()

    @retry_html_request
    def _submit_pin(self):
        soup = self._get_soup("user.html")
        token = soup.find("input", {"name": "gen_input"})["value"]
        encrypted, gen_val = self._encrypt(self.password, token)
        data = {
            "password":    encrypted,
            "gen_input":   gen_val,
            "Submit":      "code",
        }
        self.session.post(f"{self.base_url}/ucode", data=data, timeout=5).raise_for_status()

    def login(self):
        self._submit_login()
        self._submit_pin()

    @retry_html_request
    def select_partition(self, part_id: str = "1") -> BeautifulSoup:
        # Navigate to panel and select partition:
        self.session.get(f"{self.base_url}/panel.html", timeout=5).raise_for_status()
        data = {"part": f"part{part_id}", "Submit": "code"}
        headers = {"Referer": f"{self.base_url}/panel.html"}
        resp = self.session.post(f"{self.base_url}/part.cgi", data=data, headers=headers, timeout=5)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    @retry_html_request
    def get_part_status(self, soup: BeautifulSoup) -> dict:
        p = soup.find("p")
        alarm_status = p.find_all("span")[1].get_text(strip=True) if p else None

        text = soup.get_text("\n", strip=True)
        battery = re.search(r"(\d+\.?\d*)\s*Volt", text)
        ac_match = re.search(r"Παροχή\s*230V:\s*(ΝΑΙ|NAI|OXI|Yes|No)", text, re.IGNORECASE)

        return {
            "alarm_status": alarm_status,
            "battery_volt": float(battery.group(1)) if battery else None,
            "ac_power":     self._to_bool(ac_match.group(1)) if ac_match else None,
        }

    @retry_html_request
    def get_zones(self, soup: BeautifulSoup) -> list:
        link = soup.find("a", string=re.compile("ζωνών", re.I))
        url = link["href"] if link else "zones.html"
        resp = self.session.get(f"{self.base_url}/{url.lstrip('/')}", timeout=5, headers={"Referer": f"{self.base_url}/part.cgi"})
        resp.raise_for_status()
        table = BeautifulSoup(resp.text, "html.parser").find("table", class_="normaltable")
        zones = []
        if table:
            for row in table.find_all("tr")[1:]:
                cols = row.find_all("td")
                if len(cols) >= 4:
                    zones.append({
                        "zone":        cols[0].get_text(strip=True),
                        "description": cols[1].get_text(strip=True),
                        "status":      cols[2].get_text(strip=True),
                        "bypass":      cols[3].get_text(strip=True),
                    })
        return zones

    def parse_alarm_status(self, raw_status: str):
        mapping = {
            "AΦOΠΛIΣMENO":                        ("Disarmed",        None),
            "OΠΛIΣMENO ME ZΩNEΣ BYPASS":          ("Armed",           True),
            "OΠΛIΣMENO":                          ("Armed",           False),
            "ΠEPIMETPIKH OΠΛIΣH ME ZΩNEΣ BYPASS": ("Armed Perimeter", True),
            "ΠEPIMETPIKH OΠΛIΣH":                 ("Armed Perimeter", False),
        }
        return mapping.get(raw_status, (None, None))

    def _to_bool(self, val):
        if not val:
            return None
        v = str(val).strip().upper()
        if v in ("ΝΑΙ", "NAI", "YES", "TRUE"):
            return True
        if v in ("OXI", "NO", "FALSE"):
            return False
        return None

    def _to_openclosed(self, val):
        if not val:
            return None
        v = str(val).strip().lower()
        if v == "κλειστή":
            return "Closed"
        if v == "ανοικτή":
            return "Open"
        return val

    def perform_action(self, action: str):
        action_map = {"arm": "arm.html", "disarm": "disarm.html", "stay": "stay.html"}
        if action not in action_map:
            logger.warning("Invalid action %r", action)
            return None

        try:
            self.login()
            soup = self.select_partition()
            current, _ = self.parse_alarm_status(self.get_part_status(soup)["alarm_status"])

            # Skip redundant
            expected = {"arm": "Armed", "stay": "Armed Perimeter", "disarm": "Disarmed"}[action]
            if current == expected:
                logger.info("Already in state %s, skipping %s", expected, action)
                return None

            resp = self.session.get(f"{self.base_url}/{action_map[action]}", timeout=5)
            resp.raise_for_status()

            # verify
            time.sleep(1)
            soup2 = self.select_partition()
            new, _ = self.parse_alarm_status(self.get_part_status(soup2)["alarm_status"])
            if new != expected:
                raise RuntimeError(f"Expected {expected} after {action}, got {new}")

            return resp
        except Exception as e:
            logger.exception("Failed to perform action %r: %s", action, e)
            return None
