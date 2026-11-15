"""A stateful fake of the Sigma Ixion web interface, served over real HTTP.

It reproduces what the integration relies on (see "Security & Token Handling" in the README):

* ``GET /login.html`` hands out a one-time ``gen_input`` token, and the password and PIN
  arrive encrypted with it. The fake decrypts them, so the client's encryption is exercised.
* A successful login sets a ``SID`` cookie. Only one session is active at a time: a new
  login ends the previous one, as on the real panel.
* Protected pages answer with the login form when the session is missing or expired.
* ``/arm.html``, ``/disarm.html`` and ``/stay.html`` change the partition state.

Two panel styles are supported:

* ``classic``: one PIN, partitions are selected with ``POST /part.cgi`` (what ``SigmaClient``
  does today).
* ``pin_scoped``: every PIN unlocks one partition and the pages show ``Τμήμα N:`` for it
  (Aeolus style, as reported for two-partition installations). Commands also work without
  the ``.html`` extension.

The HTML is synthetic. It matches what the integration's parsers expect, not pages captured
from real firmware, so the simulator tests the integration's logic, not firmware
compatibility.

This module only uses the standard library, so it can also run on its own:
``python -m tests.simulator --help``.
"""
from __future__ import annotations

import json
import secrets
import string
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

DISARMED, ARMED, PERIMETER = "disarmed", "armed", "perimeter"

# Exactly as the panel writes them: a mix of Latin and Greek capitals.
STATUS_TEXT = {
    DISARMED: "AΦOΠΛIΣMENO",
    ARMED: "OΠΛIΣMENO",
    PERIMETER: "ΠEPIMETPIKH OΠΛIΣH",
}
BYPASS_SUFFIX = " ME ZΩNEΣ BYPASS"
COMMANDS = {"arm": ARMED, "disarm": DISARMED, "stay": PERIMETER}


@dataclass
class Zone:
    zone: str
    description: str
    open: bool = False
    bypassed: bool = False


@dataclass
class Partition:
    number: str
    zones: list[Zone]
    state: str = DISARMED


@dataclass
class _Session:
    created: float
    partition: str | None = None  # set once the PIN is accepted
    pin_token: str | None = None


def default_zones(partition: str) -> list[Zone]:
    if partition == "1":
        return [
            Zone("1", "Front Door"),
            Zone("2", "Back Door", open=True),
            Zone("3", "Window", bypassed=True),
        ]
    return [Zone("5", "Garage"), Zone("6", "Shed", bypassed=True)]


def _rc4_decrypt(cipher_hex: str, token: str) -> str:
    """Undo SigmaClient._encrypt's RC4 step (RC4 is symmetric)."""
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + ord(token[i % len(token)])) % 256
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = []
    for pos in range(0, len(cipher_hex), 2):
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(chr(int(cipher_hex[pos:pos + 2], 16) ^ s[(s[i] + s[j]) % 256]))
    return "".join(out)


def _contains_secret(plain: str, secret: str) -> bool:
    """The plaintext is prefix(num) + secret + padding + str(num) + str(len(secret))."""
    length = str(len(secret))
    if not plain.endswith(length) or len(plain) <= len(length):
        return False
    num = plain[-len(length) - 1]
    return num.isdigit() and plain[int(num):int(num) + len(secret)] == secret


class FakePanel:
    """A fake panel listening on ``url``. Use as a context manager or call start()/stop()."""

    def __init__(
        self,
        *,
        mode: str = "classic",
        partitions: int | None = None,
        username: str = "admin",
        password: str = "secret",
        pins: dict[str, str] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        session_ttl: float | None = None,
    ) -> None:
        if mode not in ("classic", "pin_scoped"):
            raise ValueError("mode must be 'classic' or 'pin_scoped'")
        self.mode = mode
        if partitions is None:
            partitions = 2 if mode == "pin_scoped" else 1
        self.username = username
        self.password = password
        if pins is None:
            pins = {"1234": "1", "5678": "2"} if mode == "pin_scoped" else {"1234": "1"}
        self.pins = dict(pins)
        self.session_ttl = session_ttl
        self.partitions = {
            str(n): Partition(str(n), default_zones(str(n))) for n in range(1, partitions + 1)
        }
        self.battery_volt = 13.5
        self.ac_power = True

        # What happened, for assertions.
        self.requests: list[tuple[str, str]] = []
        self.commands: list[str] = []  # e.g. "arm@1"
        self.analytics: list[dict] = []

        # Faults.
        self.empty_zones: set[str] = set()  # partitions whose zone table comes back empty
        self.ignore_commands = 0  # the next N commands get a 200 but change nothing
        self.fail_requests: dict[str, int] = {}  # path -> number of HTTP 500 answers
        self.command_started = threading.Event()
        self._hold: threading.Event | None = None

        self._lock = threading.RLock()
        self._tokens: set[str] = set()
        self._sessions: dict[str, _Session] = {}
        self._server = ThreadingHTTPServer((host, port), _Handler)
        self._server.daemon_threads = True
        self._server.panel = self
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> FakePanel:
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._hold is not None:
            self._hold.set()
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> FakePanel:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------------ test controls

    def state(self, partition: str = "1") -> str:
        return self.partitions[partition].state

    def set_state(self, partition: str, state: str) -> None:
        with self._lock:
            self.partitions[partition].state = state

    def remove_zone(self, partition: str, zone: str) -> None:
        with self._lock:
            part = self.partitions[partition]
            part.zones = [z for z in part.zones if z.zone != zone]

    def expire_sessions(self) -> None:
        """End every session, as when the panel times it out or someone logs in elsewhere."""
        with self._lock:
            self._sessions.clear()

    def clear_log(self) -> None:
        with self._lock:
            self.requests.clear()
            self.commands.clear()

    @contextmanager
    def holding_commands(self):
        """Commands block inside the panel until the block exits (a slow panel)."""
        self.command_started.clear()
        self._hold = threading.Event()
        try:
            yield
        finally:
            self._hold.set()
            self._hold = None

    # ------------------------------------------------------------------ pages

    def _page(self, body: str) -> str:
        return f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{body}</body></html>"

    def _token_page(self) -> str:
        token = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
        self._tokens.add(token)
        return self._page(
            f"<form method='post'><input type='hidden' name='gen_input' value='{token}'>"
            "<input name='username'><input name='password' type='password'></form>"
        )

    def _status_text(self, partition: Partition) -> str:
        text = STATUS_TEXT[partition.state]
        if partition.state != DISARMED and any(z.bypassed for z in partition.zones):
            text += BYPASS_SUFFIX
        return text

    def _header(self, partition: Partition) -> str:
        return (
            f"<div>Τμήμα {partition.number}: {self._status_text(partition)}</div>"
            f"<div>Μπαταρία: {self.battery_volt} Volt</div>"
            f"<div>Παροχή 230V: {'ΝΑΙ' if self.ac_power else 'OXI'}</div>"
        )

    def _partition_page(self, partition: Partition) -> str:
        return self._page(
            self._header(partition) + "<a href='zones.html'>Κατάσταση ζωνών</a>"
        )

    def _zones_page(self, partition: Partition) -> str:
        rows = ""
        if partition.number not in self.empty_zones:
            rows = "".join(
                f"<tr><td>{z.zone}</td><td>{z.description}</td>"
                f"<td>{'ανοικτή' if z.open else 'κλειστή'}</td>"
                f"<td>{'ΝΑΙ' if z.bypassed else 'OXI'}</td></tr>"
                for z in partition.zones
            )
        return self._page(
            self._header(partition)
            + "<table class='normaltable'><tr><th>Ζώνη</th><th>Περιγραφή</th>"
            f"<th>Κατάσταση</th><th>Παράκαμψη</th></tr>{rows}</table>"
        )

    def _access_page(self, partition: Partition) -> str:
        ext = "" if self.mode == "pin_scoped" else ".html"
        links = "".join(f"<a href='{c}{ext}'>{c}</a>" for c in COMMANDS)
        return self._page(self._header(partition) + links)

    # ------------------------------------------------------------------ request handling

    def _verify(self, form: dict[str, str], secrets_to_try: list[str]) -> str | None:
        """Return the secret the encrypted password field holds, consuming its token."""
        cipher = form.get("password", "")
        if form.get("gen_input") != str(len(cipher) // 2):
            return None
        for token in list(self._tokens):
            plain = _rc4_decrypt(cipher, token)
            for secret in secrets_to_try:
                if _contains_secret(plain, secret):
                    self._tokens.discard(token)
                    return secret
        return None

    def _session(self, cookie_header: str | None) -> tuple[str | None, _Session | None]:
        if not cookie_header:
            return None, None
        morsel = SimpleCookie(cookie_header).get("SID")
        sid = morsel.value if morsel else None
        session = self._sessions.get(sid)
        if session and self.session_ttl and time.monotonic() - session.created > self.session_ttl:
            self._sessions.pop(sid, None)
            return sid, None
        return sid, session

    def handle(self, method: str, raw_path: str, body: bytes, cookie_header: str | None):
        """Return (status, html, set_cookie)."""
        path = urlsplit(raw_path).path.lstrip("/")
        with self._lock:
            if path == "analytics" and method == "POST":  # stands in for the analytics server
                self.analytics.append(json.loads(body or b"{}"))
                return 200, "ok", None
            self.requests.append((method, path))
            if self.fail_requests.get(path):
                self.fail_requests[path] -= 1
                return 500, self._page("Internal error"), None

            form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
            sid, session = self._session(cookie_header)

            if path == "login.html":
                if method == "GET":
                    return 200, self._token_page(), None
                if form.get("username") == self.username and self._verify(form, [self.password]):
                    # One session at a time: a new login ends the previous one.
                    self._sessions.clear()
                    sid = secrets.token_hex(8)
                    self._sessions[sid] = _Session(created=time.monotonic())
                    return 200, self._page("Welcome"), f"SID={sid}; Path=/"
                return 200, self._token_page(), None

            if path == "logout.html":
                self._sessions.pop(sid, None)
                return 200, self._page("Bye"), None

            if session is None:
                return 200, self._token_page(), None

            if path == "user.html":
                return 200, self._token_page(), None

            if path == "ucode" and method == "POST":
                pin = self._verify(form, list(self.pins))
                if pin is not None:
                    session.partition = self.pins[pin]
                return 200, self._page("OK"), None

            if session.partition is None:  # logged in, but no valid PIN yet
                return 200, self._token_page(), None

            if path == "part.cgi" and method == "POST" and self.mode == "classic":
                requested = form.get("part", "").replace("part", "")
                if requested in self.partitions:
                    session.partition = requested
            partition = self.partitions[session.partition]

            if path == "panel.html":
                if self.mode == "pin_scoped":
                    return 200, self._partition_page(partition), None
                items = "".join(f"<li>Τμήμα {n}</li>" for n in self.partitions)
                return 200, self._page(f"<ul>{items}</ul>"), None
            if path == "part.cgi":
                return 200, self._partition_page(partition), None
            if path == "zones.html":
                return 200, self._zones_page(partition), None
            if path == "access.html":
                return 200, self._access_page(partition), None

            command = path[:-5] if path.endswith(".html") else path
            if command not in COMMANDS:
                return 404, self._page("Not found"), None
            self.commands.append(f"{command}@{partition.number}")
            ignored = self.ignore_commands > 0
            if ignored:
                self.ignore_commands -= 1
            hold = self._hold

        # Outside the lock, so a held command doesn't block other requests.
        if hold is not None:
            self.command_started.set()
            hold.wait(30)
        if not ignored:
            with self._lock:
                partition.state = COMMANDS[command]
        return 200, self._page("Done"), None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _serve(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        status, html, cookie = self.server.panel.handle(
            method, self.path, body, self.headers.get("Cookie")
        )
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")

    def log_message(self, *args) -> None:
        pass
