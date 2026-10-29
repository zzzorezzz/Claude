"""HTTPS requests to online services, with plain-English errors.

If a connection fails in a way that points at a broken IPv6 route (common on
home connections: the browser copes, other programs hang), it's tried once
more over IPv4.
"""

import http.client
import json
import socket
import ssl
import urllib.error
import urllib.request

UA = "WatchAnything/1.0 (personal desktop app)"


class NetError(Exception):
    """A request that didn't get an answer. `kind` is "timeout", "dns",
    "blocked", "secure", "dropped" or "offline"; `status` is set (with the
    service's own message in `detail`) when the service answered with an error."""

    def __init__(self, message, kind="offline", status=None, detail=""):
        super().__init__(message)
        self.kind, self.status, self.detail = kind, status, detail


def _ssl_context():
    from .metadata import ssl_context
    return ssl_context()


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        infos = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)
        if not infos:
            raise OSError(f"no IPv4 address for {self.host}")
        addr = infos[0][4][0]
        original = self._create_connection
        self._create_connection = lambda address, *a, **kw: original((addr, address[1]), *a, **kw)
        try:
            super().connect()
        finally:
            self._create_connection = original


class _IPv4Handler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_IPv4HTTPSConnection, req, context=self._context)


def _open(req, timeout, ipv4=False):
    if ipv4:
        opener = urllib.request.build_opener(_IPv4Handler(context=_ssl_context()))
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def _describe(e, service):
    """(message, kind) for a failed connection."""
    reason = getattr(e, "reason", e)
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason):
        return f"{service} took too long to answer", "timeout"
    if isinstance(reason, socket.gaierror):
        return (f"couldn't find {service}'s server - check your internet connection "
                "(a VPN in your browser doesn't cover this app)"), "dns"
    if isinstance(reason, ssl.SSLError):
        return f"couldn't make a secure connection to {service} ({reason})", "secure"
    if isinstance(reason, (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError)):
        return (f"the connection to {service} was blocked or cut off - a firewall or "
                "antivirus may be stopping Watch Anything"), "blocked"
    if isinstance(e, http.client.HTTPException):
        return f"{service} closed the connection before answering", "dropped"
    return f"couldn't reach {service} ({reason}) - are you online?", "offline"


def request_json(url, service, headers=None, body=None, timeout=20, method=None):
    """The JSON answer from a service, or NetError."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method or ("POST" if data else "GET"))
    last = None
    for ipv4 in (False, True):
        try:
            with _open(req, timeout, ipv4) as resp:
                raw = resp.read()
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError as e:
                raise NetError(f"{service} sent an unreadable reply", "dropped") from e
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", "replace")
            except (OSError, http.client.HTTPException):
                text = ""
            detail = text
            try:
                err = json.loads(text)
                err = err[0] if isinstance(err, list) and err else err
                if isinstance(err, dict):
                    inner = err.get("error")
                    detail = (inner.get("message") if isinstance(inner, dict) else inner) or err.get("message") or text
            except ValueError:
                pass
            raise NetError(f"{service} returned an error (HTTP {e.code})", "http", e.code,
                           str(detail or "")[:300]) from e
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            message, kind = _describe(e, service)
            last = NetError(message, kind)
            last.__cause__ = e
            # Worth one more go over IPv4 unless the name itself couldn't be found.
            if ipv4 or kind in ("dns", "secure"):
                break
    raise last
