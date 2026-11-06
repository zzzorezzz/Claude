"""Using the app from your phone or tablet (an iPhone, an iPad, any browser) on your own network.

Off unless you switch it on (Settings → Use on your phone). Then the app also listens on this
computer's own network addresses - your home Wi-Fi / Ethernet, and Tailscale when it's running -
on one port you choose (8765 to start with). It never listens anywhere else and opens nothing in
the router or the firewall.

Everything there needs a paired device. A phone asks to connect; this computer shows a
6-digit code and you press Allow (the phone shows the same code, so you know it's yours). The
phone then keeps a long random key in a cookie (HttpOnly, SameSite=Strict, for a year); this
computer keeps only a hash of it, with the device's name and when it was last seen. Asking to
connect is rate-limited, and keys are never logged or put in addresses.

Requests from this computer itself (127.0.0.1) don't come through here at all: they go to the
app's own server as they always have.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

COOKIE = "wa_device"
COOKIE_AGE = 365 * 86400
PAIR_WAIT = 120                 # seconds a request to connect waits for Allow
PAIR_LIMIT = (5, 600)           # at most 5 requests from one address in 10 minutes
AWAKE_FOR = 600                 # the PC stays awake this long after the phone last did something
DEFAULT_PORT = 8765
TAILNET = ipaddress.ip_network("100.64.0.0/10")

# What a phone may load before it's paired: the app's own pages and pictures (no data in them).
SHELL = re.compile(r"/(?:|index\.html|app\.js|phone\.js|player\.js|style\.css|manifest\.webmanifest|sw\.js|"
                   r"favicon\.png|logo\.png|icon\.svg|(?:icons|vendor)/[\w.-]+)")
OPEN_API = {"/api/ping", "/api/pair/request", "/api/pair/status"}


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now():
    return time.time()


class Devices:
    """The phones and tablets allowed in, and the ones asking to be."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.requests = {}          # id -> {"code", "name", "ip", "at", "state", "token"}
        self._asked = {}            # ip -> [when it asked]
        self._saved_at = 0.0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.devices = [d for d in data.get("devices") or [] if isinstance(d, dict) and d.get("hash")]

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": self.devices}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        self._saved_at = _now()

    # ---- asking to connect

    def request(self, name, ip):
        """A phone asks to connect: a code to show on both screens. Raises PermissionError
        when that address asked too often."""
        with self.lock:
            now = _now()
            for rid in [r for r, v in self.requests.items() if now - v["at"] > PAIR_WAIT + 60]:
                del self.requests[rid]
            recent = [t for t in self._asked.get(ip, []) if now - t < PAIR_LIMIT[1]]
            if len(recent) >= PAIR_LIMIT[0]:
                raise PermissionError("too many tries - wait a few minutes and try again")
            if sum(1 for v in self.requests.values() if v["state"] == "waiting") >= 3:
                raise PermissionError("other devices are asking to connect right now - try again in a minute")
            self._asked[ip] = recent + [now]
            for r in self.requests.values():
                if r["ip"] == ip and r["state"] == "waiting":
                    r["state"] = "replaced"          # (that device asked again: only its newest code counts)
            rid = secrets.token_urlsafe(12)
            name = re.sub(r"\s+", " ", str(name or "")).strip()[:40] or "A phone"
            self.requests[rid] = {"code": f"{secrets.randbelow(1_000_000):06d}", "name": name, "ip": ip,
                                  "at": now, "state": "waiting", "token": None}
            return rid, self.requests[rid]["code"]

    def waiting(self):
        with self.lock:
            now = _now()
            return [{"id": rid, "name": r["name"], "code": r["code"], "ip": r["ip"]}
                    for rid, r in self.requests.items() if r["state"] == "waiting" and now - r["at"] < PAIR_WAIT]

    def answer(self, rid, allow):
        """You pressed Allow (the device is added, its key made) or Deny."""
        with self.lock:
            r = self.requests.get(rid)
            if not r or r["state"] != "waiting" or _now() - r["at"] > PAIR_WAIT:
                raise KeyError("that request isn't waiting any more")
            if not allow:
                r["state"] = "denied"
                return None
            token = secrets.token_urlsafe(32)
            device = {"id": secrets.token_hex(6), "name": r["name"], "hash": _hash(token), "created": _now(),
                      "seen": _now(), "ip": r["ip"], "play_on": "phone"}
            self.devices.append(device)
            self._save()
            r["state"], r["token"], r["device"] = "allowed", token, device["id"]
            return self.public(device)

    def poll(self, rid, ip):
        """Where a request stands: "waiting", "denied", "expired", or ("allowed", key) - the key
        handed over once, to the device that asked."""
        with self.lock:
            r = self.requests.get(rid)
            if not r or r["ip"] != ip:
                return "expired", None
            if r["state"] == "waiting" and _now() - r["at"] > PAIR_WAIT:
                r["state"] = "expired"
            if r["state"] == "allowed" and r["token"]:
                token, r["token"] = r["token"], None
                return "allowed", token
            return r["state"], None

    # ---- paired devices

    def check(self, token):
        """The device this key belongs to, or None."""
        if not token or len(token) > 200:
            return None
        h = _hash(token)
        with self.lock:
            for d in self.devices:
                if hmac.compare_digest(d["hash"], h):
                    return d
        return None

    def check_id(self, device_id):
        """Whether that device is still let in."""
        with self.lock:
            return any(d["id"] == device_id for d in self.devices)

    def seen(self, device, ip):
        with self.lock:
            device["seen"], device["ip"] = _now(), ip
            if _now() - self._saved_at > 60:
                self._save()

    def public(self, d):
        return {k: d.get(k) for k in ("id", "name", "created", "seen", "ip", "play_on")}

    def list(self):
        with self.lock:
            return [self.public(d) for d in self.devices]

    def update(self, device_id, name=None, play_on=None):
        with self.lock:
            d = next((x for x in self.devices if x["id"] == device_id), None)
            if not d:
                raise KeyError(device_id)
            if name is not None:
                d["name"] = re.sub(r"\s+", " ", str(name)).strip()[:40] or d["name"]
            if play_on in ("phone", "pc"):
                d["play_on"] = play_on
            self._save()
            return self.public(d)

    def remove(self, device_id=None):
        """Take one device out (it has to ask again), or every one (None)."""
        with self.lock:
            before = len(self.devices)
            self.devices = [d for d in self.devices if device_id is not None and d["id"] != device_id]
            self._save()
            return before - len(self.devices)


# ---- this computer's addresses

def _run(cmd, timeout=4):
    kwargs = {"capture_output": True, "text": True, "timeout": timeout}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.run(cmd, **kwargs).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _usable(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and not (a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified)


def interface_addresses():
    """[(ip, adapter name)] for this computer's IPv4 addresses (not 127.x or 169.254.x)."""
    found = {}
    if sys.platform == "win32":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { $_.IPAddress + '|' + $_.InterfaceAlias }"])
        for line in out.splitlines():
            ip, _, name = line.strip().partition("|")
            if _usable(ip):
                found[ip] = name.strip()
    else:
        out = _run(["ifconfig"])
        name = ""
        for line in out.splitlines():
            if line and not line[0].isspace():
                name = line.split(":")[0]
            m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)", line)
            if m and _usable(m.group(1)):
                found[m.group(1)] = name
    if not found:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                if _usable(info[4][0]):
                    found.setdefault(info[4][0], "")
        except OSError:
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))          # (no packet is sent)
                if _usable(s.getsockname()[0]):
                    found.setdefault(s.getsockname()[0], "")
        except OSError:
            pass
    return sorted(found.items(), key=lambda x: (ipaddress.ip_address(x[0]) in TAILNET, x[0]))


def tailscale_binary():
    for p in (shutil.which("tailscale"), r"C:\Program Files\Tailscale\tailscale.exe",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if p and os.path.isfile(p):
            return p
    return ""


def tailscale():
    """{"installed", "running", "name" (its MagicDNS name), "ips"}."""
    exe = tailscale_binary()
    info = {"installed": bool(exe), "running": False, "name": "", "ips": []}
    if not exe:
        return info
    try:
        status = json.loads(_run([exe, "status", "--json"], timeout=5) or "{}")
    except ValueError:
        return info
    me = status.get("Self") or {}
    info["running"] = status.get("BackendState") == "Running"
    info["name"] = str(me.get("DNSName") or "").rstrip(".")
    info["ips"] = [ip for ip in me.get("TailscaleIPs") or [] if _usable(ip)]
    return info


def kind_of(ip, adapter=""):
    if ipaddress.ip_address(ip) in TAILNET or "tailscale" in adapter.lower():
        return "tailscale"
    a = adapter.lower()
    if any(w in a for w in ("vethernet", "wsl", "hyper-v", "virtualbox", "vmware", "docker", "bridge", "utun", "awdl")):
        return "virtual"
    if any(w in a for w in ("wi-fi", "wifi", "wlan", "wireless")):
        return "wifi"
    if "ethernet" in a or a.startswith(("en", "eth")):
        return "ethernet"
    return "network"


LABELS = {"wifi": "Wi-Fi", "ethernet": "Ethernet", "network": "Your network", "tailscale": "Tailscale",
          "virtual": "A virtual network (not your phone's)"}


def _answers(ip, port):
    """Whether the app answers on that address."""
    try:
        with socket.create_connection((ip, port), timeout=2) as s:
            s.sendall(f"GET /api/ping HTTP/1.0\r\nHost: {ip}:{port}\r\n\r\n".encode())
            s.settimeout(3)
            return s.recv(12).startswith(b"HTTP/1.")
    except OSError:
        return False


# ---- keeping the PC awake while a phone uses it

class Awake:
    def __init__(self):
        self.last, self.held, self.on = 0.0, False, True
        self._mac = None
        threading.Thread(target=self._loop, daemon=True).start()

    def poke(self):
        self.last = _now()

    def _loop(self):
        while True:
            time.sleep(30)
            want = self.on and _now() - self.last < AWAKE_FOR
            try:
                if want:
                    self._hold()
                elif self.held:
                    self._release()
            except Exception:  # noqa: BLE001 - staying awake is a nicety
                pass

    def _hold(self):
        self.held = True
        if sys.platform == "win32":
            import ctypes
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_SYSTEM_REQUIRED)     # (the idle clock starts again)
        elif sys.platform == "darwin":
            if not self._mac or self._mac.poll() is not None:
                self._mac = subprocess.Popen(["caffeinate", "-i", "-t", "120"], stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)

    def _release(self):
        self.held = False
        if self._mac and self._mac.poll() is None:
            self._mac.terminate()
        self._mac = None


# ---- the listeners

class Access:
    """The app on your network: on, off, its addresses, and who may come in."""

    def __init__(self, app, make_listener):
        self.app = app
        self.make_listener = make_listener      # (ip, port) -> a server (serve_forever / shutdown / server_close)
        self.devices = Devices(Path(app.data_dir) / "devices.json")
        self.awake = Awake()
        self.lock = threading.RLock()
        self.listeners = {}                     # ip -> server
        self.error = ""
        self.addresses = []                     # [{"ip", "kind", "label", "adapter"}]
        self.ts = {"installed": False, "running": False, "name": "", "ips": []}
        self.hostname = socket.gethostname().lower()
        self._allowed = set()
        self._started = False
        self.reachable = {}                     # ip -> whether the app answered there just now

    def start(self):
        if not self._started:
            self._started = True
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 - tried again in a moment
                pass
            time.sleep(30)

    def settings(self):
        s = self.app.settings.get()
        return bool(s.get("phone_access")), int(s.get("phone_port") or DEFAULT_PORT), s.get("phone_keep_awake", True) is not False

    def refresh(self):
        """Listen on the addresses this computer has now (they change: Wi-Fi, Tailscale coming up)."""
        on, port, awake = self.settings()
        self.awake.on = awake
        with self.lock:
            if not on:
                self._close_all()
                self.error = ""
                return
            found = interface_addresses()
            ts = tailscale()
            known = {ip for ip, _ in found}
            for ip in ts["ips"]:
                if ip not in known:
                    found.append((ip, "Tailscale"))
            self.ts = ts
            self.addresses = [{"ip": ip, "adapter": name, "kind": kind_of(ip, name), "label": LABELS[kind_of(ip, name)]}
                              for ip, name in found]
            wanted = {a["ip"] for a in self.addresses}
            for ip in [ip for ip in self.listeners if ip not in wanted or self.listeners[ip].server_address[1] != port]:
                self._close(ip)
            errors = []
            for ip in wanted - set(self.listeners):
                try:
                    srv = self.make_listener(ip, port)
                except OSError as e:
                    errors.append(f"{ip}:{port} ({e.strerror or e})")
                    continue
                self.listeners[ip] = srv
                threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.error = ("The app couldn't listen on " + ", ".join(errors) + " - another program may be using that "
                          "port: choose another one.") if errors else ""
            names = {self.hostname, f"{self.hostname}.local", f"{self.hostname}.lan", f"{self.hostname}.home"}
            if ts["name"]:
                names.add(ts["name"].lower())
                names.add(ts["name"].split(".")[0].lower())
            self._allowed = {f"{h}:{port}" for h in names | wanted}
            listening = dict(self.listeners)
        # Can it be reached there? (A firewall that lets nothing in blocks the app reaching itself too.)
        reach = {ip: _answers(ip, port) for ip in listening}
        with self.lock:
            self.reachable = reach

    def _close(self, ip):
        srv = self.listeners.pop(ip, None)
        if srv:
            threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()

    def _close_all(self):
        for ip in list(self.listeners):
            self._close(ip)

    def allowed_host(self, host):
        """Only this computer's own names and addresses (with the port): a website that points
        a name of its own at this computer (DNS rebinding) gets nothing."""
        return (host or "").strip().lower() in self._allowed

    def status(self):
        on, port, awake = self.settings()
        with self.lock:
            listening = sorted(self.listeners)
            return {"on": on, "port": port, "keep_awake": awake, "error": self.error,
                    "addresses": [dict(a, url=f"http://{a['ip']}:{port}/", listening=a["ip"] in listening,
                                       reachable=self.reachable.get(a["ip"]))
                                  for a in self.addresses],
                    "platform": sys.platform,
                    "names": [f"http://{self.ts['name']}:{port}/"] if self.ts.get("name") else [],
                    "tailscale": dict(self.ts), "devices": self.devices.list(),
                    "waiting": self.devices.waiting()}

    def close(self):
        with self.lock:
            self._close_all()
