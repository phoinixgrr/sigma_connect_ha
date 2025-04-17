import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import re
import random
import time
import logging

RETRY_TOTAL = 8
RETRY_BACKOFF_FACTOR = 0.5
RETRY_STATUS_FORCELIST = [500, 502, 503, 504]
RETRY_ATTEMPTS_FOR_HTML = 8

logger = logging.getLogger(__name__)

def retry_html_request(func):
    def wrapper(*args, **kwargs):
        for attempt in range(RETRY_ATTEMPTS_FOR_HTML):
            try:
                return func(*args, **kwargs)
            except (AttributeError, TypeError, IndexError) as e:
                logger.warning(f"HTML parsing failed on attempt {attempt + 1}/{RETRY_ATTEMPTS_FOR_HTML}: {e}")
                time.sleep(RETRY_BACKOFF_FACTOR * (2 ** attempt))  # exponential backoff
        raise RuntimeError(f"HTML parsing failed after {RETRY_ATTEMPTS_FOR_HTML} attempts.")
    return wrapper

class SigmaClient:
    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip('/')
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
    def _get_soup(self, path):
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, 'html.parser')

    def _encrypt(self, secret, token):
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
        out_chars = []
        for ch in newpass:
            i = (i + 1) % 256
            j = (j + S[i]) % 256
            S[i], S[j] = S[j], S[i]
            K = S[(S[i] + S[j]) % 256]
            out_chars.append(chr(ord(ch) ^ K))
        cipher = ''.join(out_chars)
        hexstr = ''.join(f"{ord(c):02x}" for c in cipher)
        gen_val = str(len(cipher))
        return hexstr, gen_val

    @retry_html_request
    def _submit_login(self):
        soup = self._get_soup('login.html')
        token = soup.find('input', {'name': 'gen_input'})['value']
        encrypted, gen_val = self._encrypt(self.password, token)
        data = {
            'username': self.username,
            'password': encrypted,
            'gen_input': gen_val,
            'Submit': 'Apply'
        }
        self.session.post(f"{self.base_url}/login.html", data=data).raise_for_status()

    @retry_html_request
    def _submit_pin(self):
        soup = self._get_soup('user.html')
        token = soup.find('input', {'name': 'gen_input'})['value']
        encrypted, gen_val = self._encrypt(self.password, token)
        data = {'password': encrypted, 'gen_input': gen_val, 'Submit': 'code'}
        self.session.post(f"{self.base_url}/ucode", data=data).raise_for_status()

    def login(self):
        self._submit_login()
        self._submit_pin()

    @retry_html_request
    def select_partition(self, part_id='1'):
        self.session.get(f"{self.base_url}/panel.html").raise_for_status()
        data = {'part': f'part{part_id}', 'Submit': 'code'}
        headers = {'Referer': f"{self.base_url}/panel.html"}
        resp = self.session.post(f"{self.base_url}/part.cgi", data=data, headers=headers)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, 'html.parser')

    @retry_html_request
    def get_part_status(self, soup):
        p_tag = soup.find('p')
        alarm_status = None
        if p_tag:
            spans = p_tag.find_all('span')
            if len(spans) >= 2:
                alarm_status = spans[1].get_text(strip=True)

        text = soup.get_text("\n", strip=True)
        battery = re.search(r"(\d+\.?\d*)\s*Volt", text)
        ac_match = re.search(r"Παροχή\s*230V:\s*(ΝΑΙ|NAI|OXI|Yes|No)", text, re.IGNORECASE)

        return {
            'alarm_status': alarm_status,
            'battery_volt': float(battery.group(1)) if battery else None,
            'ac_power': self._to_bool(ac_match.group(1)) if ac_match else None
        }

    @retry_html_request
    def get_zones(self, soup):
        link = soup.find('a', string=re.compile('ζωνών', re.I))
        url = link['href'] if link and link.get('href') else 'zones.html'
        full_url = f"{self.base_url}/{url.lstrip('/')}"
        resp = self.session.get(full_url, headers={'Referer': f"{self.base_url}/part.cgi"})
        resp.raise_for_status()

        table = BeautifulSoup(resp.text, 'html.parser').find('table', class_='normaltable')
        zones = []
        if table:
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')
                if len(cols) >= 4:
                    zones.append({
                        'zone': cols[0].get_text(strip=True),
                        'description': cols[1].get_text(strip=True),
                        'status': cols[2].get_text(strip=True),
                        'bypass': cols[3].get_text(strip=True),
                    })
        return zones

    def parse_alarm_status(self, raw_status):
        mapping = {
            "AΦOΠΛIΣMENO": ("Disarmed", None),
            "OΠΛIΣMENO ME ZΩNEΣ BYPASS": ("Armed", True),
            "OΠΛIΣMENO": ("Armed", False),
            "ΠEPIMETPIKH OΠΛIΣH ME ZΩNEΣ BYPASS": ("Perimeter Armed", True),
            "ΠEPIMETPIKH OΠΛIΣH": ("Armed", False)
        }
        return mapping.get(raw_status, (None, None))

    def _to_bool(self, val):
        if val is None:
            return None
        v = str(val).strip().upper()
        if v in ("ΝΑΙ", "NAI", "YES", "TRUE"):
            return True
        if v in ("OXI", "NO", "FALSE"):
            return False
        return None

    def _to_openclosed(self, val):
        if val is None:
            return None
        v = str(val).strip().lower()
        if v == "κλειστή":
            return "Closed"
        if v == "ανοικτή":
            return "Open"
        return val
