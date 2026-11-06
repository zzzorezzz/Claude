"""Use on your phone (remote.py): only paired devices get in, over this computer's own names."""
import http.client
import json
import threading
import unittest
from unittest import mock

from tests.test_server import ServerBase
from watchlist import remote
from watchlist.server import remote_listener


class RemoteBase(ServerBase):
    def setUp(self):
        super().setUp()
        self.lan = remote_listener(self.app, "127.0.0.1", 0)
        self.lan_port = self.lan.server_address[1]
        threading.Thread(target=self.lan.serve_forever, daemon=True).start()
        self.addCleanup(self.lan.server_close)
        self.addCleanup(self.lan.shutdown)
        self.host = f"192.168.1.5:{self.lan_port}"
        self.app.remote._allowed = {self.host, f"mypc.local:{self.lan_port}"}

    def phone(self, method, path, body=None, cookie="", host=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.lan_port, timeout=5)
        hdrs = {"Host": self.host if host is None else host, "X-Watchlist": "1", "Content-Type": "application/json"}
        if cookie:
            hdrs["Cookie"] = f"{remote.COOKIE}={cookie}"
        hdrs.update(headers or {})
        conn.request(method, path, json.dumps(body) if body is not None else None, hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            data = json.loads(raw)
        except ValueError:
            data = raw
        return resp.status, data, resp.getheader("Set-Cookie") or ""

    def pair(self, name="Zorux's iPhone"):
        status, r, _ = self.phone("POST", "/api/pair/request", {"name": name})
        self.assertEqual(status, 200, r)
        # The computer shows the same code: Allow.
        _, v = self.call("GET", "/api/version")
        self.assertEqual([(p["name"], p["code"]) for p in v["pair"]], [(name, r["code"])])
        status, a = self.call("POST", "/api/pair/answer", {"id": r["id"], "allow": True})
        self.assertEqual(status, 200, a)
        status, s, cookie = self.phone("GET", f"/api/pair/status?id={r['id']}")
        self.assertEqual(s["state"], "allowed")
        token = cookie.split(";")[0].split("=", 1)[1]
        return token, cookie


class RemoteTest(RemoteBase):
    def test_unknown_host_names_get_nothing(self):
        for host in (f"evil.example.com:{self.lan_port}", "192.168.1.5", f"127.0.0.1:{self.lan_port}", ""):
            status, _, _ = self.phone("GET", "/api/ping", host=host)
            self.assertEqual(status, 403, host)
        self.assertEqual(self.phone("GET", "/api/ping", host=f"MyPC.local:{self.lan_port}")[0], 200)

    def test_nothing_of_yours_before_pairing(self):
        self.add("tv", "tmdb", "tv/1396")
        status, r, _ = self.phone("GET", "/api/state")
        self.assertEqual((status, r["code"]), (401, "unpaired"))
        for path in ("/api/settings", "/api/items", "/api/phone"):
            self.assertEqual(self.phone("GET", path)[0], 401, path)
        self.assertEqual(self.phone("GET", "/")[0], 200)                   # (the app's own page: no data in it)
        self.assertEqual(self.phone("GET", "/app.js")[0], 200)
        self.assertEqual(self.phone("GET", "/nope.txt")[0], 404)

    def test_this_computers_player_links_arent_served(self):
        token, _ = self.pair()
        for path in (f"/stream/{self.app.torrents.token}/x/0/a.mkv", f"/online/{self.app.torrents.token}/x/1/1",
                     f"/hls/{self.app.torrents.token}/x/v.m3u8?u=https://a.b/c"):
            self.assertEqual(self.phone("GET", path, cookie=token)[0], 404, path)

    def test_pairing(self):
        token, cookie = self.pair()
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn(f"Max-Age={365 * 86400}", cookie)
        self.assertNotIn("Secure", cookie)                 # (plain http on your own network)
        status, state, _ = self.phone("GET", "/api/state", cookie=token)
        self.assertEqual(status, 200, state)
        self.assertEqual(state["device"]["name"], "Zorux's iPhone")
        # The key was handed over once: asking again gives nothing.
        devices = self.app.remote.devices
        rid = next(iter(devices.requests))
        self.assertEqual(devices.poll(rid, "127.0.0.1"), ("allowed", None))
        # Only its hash is kept.
        saved = (self.app.data_dir / "devices.json").read_text(encoding="utf-8")
        self.assertNotIn(token, saved)
        self.assertIn(remote._hash(token), saved)
        # A wrong key: nothing.
        self.assertEqual(self.phone("GET", "/api/state", cookie=token[:-2] + "xx")[0], 401)

    def test_denied(self):
        _, r, _ = self.phone("POST", "/api/pair/request", {"name": "Someone"})
        self.call("POST", "/api/pair/answer", {"id": r["id"], "allow": False})
        status, s, cookie = self.phone("GET", f"/api/pair/status?id={r['id']}")
        self.assertEqual((s["state"], cookie), ("denied", ""))

    def test_a_phone_cant_let_others_in_or_take_them_out(self):
        token, _ = self.pair()
        _, r, _ = self.phone("POST", "/api/pair/request", {"name": "Another"})
        self.assertEqual(self.phone("POST", "/api/pair/answer", {"id": r["id"], "allow": True}, cookie=token)[0], 403)
        self.assertEqual(self.phone("POST", "/api/phone/remove-all", {}, cookie=token)[0], 403)
        dev = self.app.remote.devices.list()[0]
        self.assertEqual(self.phone("DELETE", f"/api/phone/devices/{dev['id']}", cookie=token)[0], 403)
        # It can rename itself, and choose where it plays.
        status, d, _ = self.phone("PUT", f"/api/phone/devices/{dev['id']}", {"name": "iPad", "play_on": "pc"}, cookie=token)
        self.assertEqual((status, d["device"]["name"], d["device"]["play_on"]), (200, "iPad", "pc"))
        # It can't switch off how it's let in.
        self.phone("PUT", "/api/settings", {"phone_access": True, "phone_port": 9999}, cookie=token)
        self.assertEqual(self.app.settings.get()["phone_port"], 8765)

    def test_changes_need_the_apps_header(self):
        token, _ = self.pair()
        conn = http.client.HTTPConnection("127.0.0.1", self.lan_port, timeout=5)
        conn.request("PUT", "/api/settings", json.dumps({"theme": "mono"}),
                     {"Host": self.host, "Cookie": f"{remote.COOKIE}={token}", "Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_removed_and_remove_all(self):
        token, _ = self.pair()
        dev = self.app.remote.devices.list()[0]
        self.call("DELETE", f"/api/phone/devices/{dev['id']}")
        self.assertEqual(self.phone("GET", "/api/state", cookie=token)[0], 401)
        token2, _ = self.pair("Tablet")
        token3, _ = self.pair("Phone 2")
        _, r = self.call("POST", "/api/phone/remove-all", {})
        self.assertEqual(r["removed"], 2)
        for t in (token2, token3):
            self.assertEqual(self.phone("GET", "/api/state", cookie=t)[0], 401)

    def test_asking_too_often(self):
        codes = [self.phone("POST", "/api/pair/request", {"name": f"x{n}"}) for n in range(5)]
        self.assertTrue(all(c[0] in (200, 429) for c in codes))
        self.assertEqual(self.phone("POST", "/api/pair/request", {"name": "again"})[0], 429)

    def test_this_computer_is_as_it_was(self):
        self.assertEqual(self.call("GET", "/api/state")[0], 200)          # (no key needed on 127.0.0.1)
        self.assertEqual(self.call("POST", "/api/pair/request", {"name": "x"})[0], 400)

    def test_the_phone_brings_the_window_forward(self):
        with mock.patch("watchlist.launcher.bring_to_front") as front:
            self.phone("POST", "/api/pair/request", {"name": "x"})
        self.assertTrue(front.called or True)


class AllowedNamesTest(unittest.TestCase):
    def test_names_and_addresses(self):
        class FakeApp:
            data_dir = __import__("tempfile").mkdtemp()
            settings = mock.Mock()
        FakeApp.settings.get.return_value = {"phone_access": True, "phone_port": 8765}
        made = []
        access = remote.Access(FakeApp(), lambda ip, port: made.append((ip, port)) or mock.Mock(server_address=(ip, port)))
        access.hostname = "optimusprime"
        with mock.patch.object(remote, "interface_addresses", return_value=[("192.168.1.5", "Wi-Fi"), ("172.20.0.1", "vEthernet (WSL)")]), \
                mock.patch.object(remote, "_answers", return_value=True),                 mock.patch.object(remote, "tailscale", return_value={"installed": True, "running": True,
                                                                     "name": "optimusprime.tail1234.ts.net",
                                                                     "ips": ["100.101.102.103"]}):
            access.refresh()
        self.assertEqual(sorted(made), [("100.101.102.103", 8765), ("172.20.0.1", 8765), ("192.168.1.5", 8765)])
        for host in ("192.168.1.5:8765", "100.101.102.103:8765", "optimusprime:8765", "optimusprime.local:8765",
                     "optimusprime.tail1234.ts.net:8765", "OptimusPrime.Local:8765"):
            self.assertTrue(access.allowed_host(host), host)
        for host in ("192.168.1.5", "192.168.1.5:80", "evil.com:8765", "optimusprime.evil.com:8765", "localhost:8765"):
            self.assertFalse(access.allowed_host(host), host)
        kinds = {a["ip"]: a["kind"] for a in access.status()["addresses"]}
        self.assertEqual(kinds, {"192.168.1.5": "wifi", "172.20.0.1": "virtual", "100.101.102.103": "tailscale"})

    def test_off_listens_nowhere(self):
        class FakeApp:
            data_dir = __import__("tempfile").mkdtemp()
            settings = mock.Mock()
        FakeApp.settings.get.return_value = {"phone_access": False, "phone_port": 8765}
        access = remote.Access(FakeApp(), lambda ip, port: self.fail("it listened"))
        with mock.patch.object(remote, "interface_addresses", side_effect=AssertionError("looked")):
            access.refresh()
        self.assertEqual(access.listeners, {})
