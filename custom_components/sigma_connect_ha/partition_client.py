"""PIN-scoped partitions for Aeolus/Ixion; callers serialize all access."""
import re
import time
from urllib.parse import urljoin, urlsplit

from .sigma_client import SigmaClient


class PartitionClient(SigmaClient):
    def __init__(self, *args, second_pin="", **kwargs):
        super().__init__(*args, **kwargs)
        self.partition_pins = {"1": self.pin}
        if second_pin:
            self.partition_pins["2"] = second_pin.strip()
        self._active_partition = None

    def logout(self):
        self._active_partition = None
        super().logout()

    @staticmethod
    def partition_number(soup):
        match = re.search(r"Τμήμα\s*(\d+)\s*(?:\([^)]*\))?\s*:", soup.get_text(" ", strip=True))
        return match.group(1) if match else None

    def require_partition(self, soup, partition):
        if self.partition_number(soup) != partition:
            raise ValueError(f"Ixion did not confirm partition {partition}; command blocked")

    def select_partition(self, part_id="1"):
        partition = str(part_id)
        if partition not in self.partition_pins:
            raise ValueError("Partition is not configured")
        if self._active_partition != partition:
            self.logout()
            self.pin = self.partition_pins[partition]
            self.login()
            self._active_partition = partition
        soup = self._get_soup("panel.html")
        # A partition-specific PIN goes directly to the panel on this firmware.
        # Other firmware presents an explicit selection form instead.
        if self.partition_number(soup) is None:
            form = soup.find("form", action=re.compile(r"(?:^|/)part\.cgi$"))
            if form is not None:
                soup = super().select_partition(partition)
        self.require_partition(soup, partition)
        return soup

    def read_partition(self, partition):
        try:
            self.select_partition(partition)
            soup = self._get_soup("zones.html")
            self.require_partition(soup, partition)
            raw = self.parse_zones_html(soup)
            status, bypass = self.parse_alarm_status(raw.get("alarm_status"))
            if status is None or raw.get("battery_volt") is None or not raw.get("zones"):
                raise ValueError(f"Incomplete data for partition {partition}")
            return {
                "status": status, "zones_bypassed": bypass,
                "battery_volt": raw["battery_volt"], "ac_power": raw["ac_power"],
                "zones": [{**z, "status": self._to_openclosed(z["status"]),
                           "bypass": self._to_bool(z["bypass"])} for z in raw["zones"]],
            }
        except Exception:
            self.logout()
            raise

    def action_partition(self, action, partition):
        desired = {"arm": "Armed", "disarm": "Disarmed", "stay": "Armed Perimeter"}
        if action not in desired:
            raise ValueError("Unsupported action")
        if self.read_partition(partition)["status"] == desired[action]:
            return
        self.select_partition(partition)
        soup = self._get_soup("access.html")
        self.require_partition(soup, partition)
        # Follow only the exact control link advertised by this panel.
        candidates = []
        for link in soup.find_all("a", href=True):
            url = urljoin(self.base_url + "/", link["href"])
            parsed = urlsplit(url)
            if (parsed.netloc == urlsplit(self.base_url).netloc
                    and parsed.scheme == urlsplit(self.base_url).scheme
                    and parsed.path in (f"/{action}", f"/{action}.html")
                    and not parsed.query and not parsed.fragment):
                candidates.append(url)
        if not candidates:
            raise ValueError("Panel does not advertise the requested control link")
        # Never retry a control request automatically: its outcome may be uncertain.
        import requests
        with requests.Session() as command_session:
            command_session.cookies.update(self.session.cookies)
            response = command_session.get(candidates[0], timeout=5, allow_redirects=False)
            response.raise_for_status()
            self.session.cookies.update(command_session.cookies)
        for _ in range(5):
            time.sleep(1)
            if self.read_partition(partition)["status"] == desired[action]:
                return
        raise ValueError(f"Partition {partition}: requested state was not confirmed")
