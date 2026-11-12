"""Watch parties standing up to bad messages, slow or vanished guests, replays through the
relay, and the router's port being closed again. No real network: the party's own server on
this computer, connections in memory, a stand-in relay and a stand-in router."""
import json
import queue
import socket
import threading
import time
import unittest
from unittest import mock

from watchlist import party, partyserver, relay, syncplay


class _Pipe:
    """A client connection in memory: what the server sends lands in `got`."""

    def __init__(self):
        self.inbox, self.got = queue.Queue(), []

    def send(self, message):
        self.got.append(message)

    def read(self):
        return self.inbox.get()

    def close(self):
        self.inbox.put(None)

    def say(self, message):
        self.inbox.put(message)


class _Stuck(_Pipe):
    """A guest whose connection has stalled: a send to it never returns."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def send(self, message):
        self.release.wait(30)


def wait_for(cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def fast(test):
    """The server's clock sped up (states every 0.1 s) for one test."""
    p = mock.patch.object(partyserver, "STATE_EVERY", 0.1)
    p.start()
    test.addCleanup(p.stop)


class _Server(unittest.TestCase):
    def setUp(self):
        fast(self)
        self.srv = partyserver.PartyServer()
        self.addCleanup(self.srv.stop)

    def join(self, name, room="r", pipe=None):
        pipe = pipe or _Pipe()
        self.srv.serve(pipe)
        pipe.say({"Hello": {"username": name, "room": {"name": room}}})
        if not isinstance(pipe, _Stuck):
            self.assertTrue(wait_for(lambda: any("Hello" in m for m in pipe.got)))
        return pipe

    def states_after(self, pipe, since):
        return [m for m in pipe.got[since:] if "State" in m]


# ---- 1. one bad message doesn't freeze the party

class BadMessageTest(_Server):
    def test_a_bad_ping_doesnt_stop_the_room(self):
        a, b = self.join("Ann"), self.join("Ben")
        a.say({"State": {"ping": {"clientLatencyCalculation": "soon"}}})
        a.say({"State": {"ping": {"latencyCalculation": [1]}, "ignoringOnTheFly": [1]}})
        time.sleep(0.3)
        since = len(b.got)
        self.assertTrue(wait_for(lambda: len(self.states_after(b, since)) >= 3), "the room keeps going for Ben")

    def test_wrong_typed_messages_are_dropped_and_the_rest_carry_on(self):
        a, b = self.join("Ann"), self.join("Ben")
        for bad in ([1, 2], "hi", {"Hello": "again"}, {"Set": "x"}, {"Set": {"file": "not a file"}},
                    {"Chat": {"message": {"long": "x" * 10}}}, {"List": 5},
                    {"State": {"playstate": {"position": float("nan"), "paused": False},
                               "ignoringOnTheFly": {"client": 1}}},
                    {"State": {"playstate": {"position": "end", "paused": "yes"}, "ignoringOnTheFly": {"client": 2}}},
                    {"State": {"playstate": {"position": 1e300, "paused": False}, "ignoringOnTheFly": {"client": 3}}}):
            a.say(bad)
        a.say({"Chat": "still here"})
        self.assertTrue(wait_for(lambda: any((m.get("Chat") or {}).get("message") == "still here" for m in b.got)))
        for m in b.got:
            self.assertNotEqual((((m.get("Set") or {}).get("user") or {}).get("Ann") or {}).get("file"), "not a file")
            pos = ((m.get("State") or {}).get("playstate") or {}).get("position")
            if pos is not None:
                self.assertTrue(0 <= pos < 1e7, f"a strange place isn't passed on ({pos})")
        self.assertIn("Ann", [c.name for c in self.srv.clients], "a guest's bad message is dropped (not the guest)")

    def test_a_stranger_sending_junk_straight_to_the_host_is_cut_off(self):
        port = self.srv.listen(0, "127.0.0.1")
        for junk in (b"GET / HTTP/1.1\r\n\r\n", b"[1, 2, 3]\r\n", b'{"Hello": "x"}\r\n', b"x" * (partyserver.MAX_LINE + 10)):
            with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
                s.sendall(junk)
                s.settimeout(5)
                try:
                    while s.recv(4096):
                        pass
                    closed = True
                except socket.timeout:
                    closed = False
                except OSError:
                    closed = True
                self.assertTrue(closed, f"cut off after {junk[:20]!r}")
        self.assertTrue(wait_for(lambda: not self.srv.clients))

    def test_too_many_strangers_are_turned_away(self):
        with mock.patch.object(partyserver, "MAX_CLIENTS", 3):
            port = self.srv.listen(0, "127.0.0.1")
            socks = [socket.create_connection(("127.0.0.1", port), timeout=5) for _ in range(6)]
            self.addCleanup(lambda: [s.close() for s in socks])
            time.sleep(0.5)
            self.assertLessEqual(len(self.srv.clients), 3)


class _Conn:
    """What a party reads from (a server's messages, given here) and sends to."""

    def __init__(self):
        self.inbox, self.sent, self.tls = queue.Queue(), [], False
        self.sock = relay._NoSocket()

    def send(self, message):
        self.sent.append(message)

    def read(self):
        return self.inbox.get()

    def close(self):
        self.inbox.put(None)


class PartyBadMessageTest(unittest.TestCase):
    def test_someone_elses_strange_file_doesnt_stop_your_party(self):
        p = party.Party("127.0.0.1:1", "r", "Ann", "host")
        p.conn, p.active = _Conn(), True
        p.people = {"Ann": None}
        self.addCleanup(p.leave)
        threading.Thread(target=p._read_loop, daemon=True).start()
        for bad in ([1], "x", {"Set": {"user": {"Bob": {"file": "not a file"}}}},
                    {"Set": {"user": {"Bob": {"file": {"name": ["x"], "duration": "long", "watchlist": "oops"}}}}},
                    {"Set": {"user": ["Bob"]}}, {"Set": {"user": {"Bob": "joined"}}},
                    {"State": {"ping": {"clientLatencyCalculation": "x", "serverRtt": "y"},
                               "playstate": {"position": "y", "paused": False}, "ignoringOnTheFly": {"server": "z"}}},
                    {"Chat": 5}, {"Chat": {"message": {"x": 1}, "username": 3}}, {"List": [1]},
                    {"List": {"r": {"Bob": "here"}}}, {"Hello": "x"}):
            p.conn.inbox.put(bad)
        p.conn.inbox.put({"Set": {"user": {"Cat": {"room": {"name": "r"}, "event": {"joined": True}}}}})
        self.assertTrue(wait_for(lambda: "Cat" in p.people), "the next good message still counts")
        self.assertTrue(p.active)
        status = p.status()                  # (what the app shows works)
        json.dumps(status)
        bob = p.people.get("Bob")
        self.assertTrue(bob is None or isinstance(bob.get("watchlist", {}), dict))
        self.assertIsInstance(p._server_ignoring, int)
        p._release_hold(type("S", (), {"connected": True, "dur": 10, "pos": 0, "paused": True,
                                       "send": lambda *a: None})())

    def test_a_server_sending_an_endless_line_is_dropped_not_kept(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        conn = party.Connection.__new__(party.Connection)
        conn.sock, conn._buf, conn._lock, conn.tls, conn._skipping = a, b"", threading.Lock(), False, False
        a.settimeout(5)

        def feed():
            b.sendall(b"y" * (party.MAX_LINE + 100))
            b.sendall(b'yyy\r\n{"Chat": {"message": "ok"}}\r\n')
        threading.Thread(target=feed, daemon=True).start()
        self.assertEqual(conn.read(), {"Chat": {"message": "ok"}})
        self.assertLess(len(conn._buf), party.MAX_LINE)


# ---- 2. one stalled guest doesn't hold up everyone

class StalledGuestTest(_Server):
    def test_a_guest_who_stops_taking_messages_doesnt_hold_up_the_others(self):
        stuck = self.join("Sid", pipe=_Stuck())
        self.addCleanup(stuck.release.set)
        a, b = self.join("Ann"), self.join("Ben")
        a.say({"Chat": "hi"})
        self.assertTrue(wait_for(lambda: any((m.get("Chat") or {}).get("message") == "hi" for m in b.got), 2),
                        "Ben hears Ann while Sid's connection is stuck")
        since = len(b.got)
        self.assertTrue(wait_for(lambda: len(self.states_after(b, since)) >= 3, 2), "the room keeps going")

    def test_the_stalled_guest_is_dropped_once_too_much_waits_for_it(self):
        with mock.patch.object(partyserver, "MAX_QUEUED", 5):
            stuck = self.join("Sid", pipe=_Stuck())
            self.addCleanup(stuck.release.set)
            a = self.join("Ann")
            self.assertTrue(wait_for(lambda: any((((m.get("Set") or {}).get("user") or {}).get("Sid") or {})
                                                 .get("event", {}).get("left") for m in a.got), 5),
                            "Ann is told Sid left")
            self.assertNotIn("Sid", [c.name for c in self.srv.clients])

    def test_a_direct_connection_has_a_send_timeout(self):
        port = self.srv.listen(0, "127.0.0.1")
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            self.assertTrue(wait_for(lambda: self.srv.clients))
            conn = self.srv.clients[0].conn
            self.assertEqual(conn.sock.gettimeout(), partyserver.SEND_TIMEOUT)


# ---- 3. the router's port is closed again

class _Router:
    """A stand-in UPnP router: what it was asked to do."""

    def __init__(self):
        self.calls, self.open = [], set()

    def gateways(self, timeout):
        return [("http://192.168.1.1:5000/ctl/IPConn", "urn:schemas-upnp-org:service:WANIPConnection:1")]

    def soap(self, control, kind, action, args, timeout):
        args = dict(args)
        self.calls.append((action, args))
        port = int(args["NewExternalPort"])
        (self.open.add if action == "AddPortMapping" else self.open.discard)(port)
        return True


class RouterPortTest(unittest.TestCase):
    def setUp(self):
        self.router = _Router()
        for name in ("gateways", "soap"):
            p = mock.patch.object(partyserver, "_" + name, getattr(self.router, name))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(partyserver, "local_address", return_value="192.168.1.20")
        p.start()
        self.addCleanup(p.stop)

    def test_ending_the_party_closes_the_port_it_opened(self):
        srv = partyserver.PartyServer()
        port = srv.listen(0, "127.0.0.1")
        self.assertTrue(partyserver.open_port(port))
        self.assertEqual(self.router.open, {port})
        add = self.router.calls[0][1]
        self.assertEqual((add["NewInternalClient"], add["NewProtocol"]), ("192.168.1.20", "TCP"))
        srv.stop()
        self.assertEqual(self.router.open, set(), "the port is closed when the party ends")
        self.assertEqual(self.router.calls[-1][0], "DeletePortMapping")
        srv.stop()                                       # (twice: asked once)
        self.assertEqual(len(self.router.calls), 2)

    def test_closing_the_app_closes_it_too(self):
        srv = partyserver.PartyServer()
        self.addCleanup(srv.stop)
        port = srv.listen(0, "127.0.0.1")
        partyserver.open_port(port)
        partyserver.close_ports()                         # (what runs as the app quits)
        self.assertEqual(self.router.open, set())

    def test_a_port_it_didnt_open_isnt_touched(self):
        srv = partyserver.PartyServer()
        srv.listen(0, "127.0.0.1")
        srv.stop()
        self.assertEqual(self.router.calls, [])

    def test_a_router_that_says_no(self):
        with mock.patch.object(partyserver, "_soap", side_effect=OSError("500")):
            self.assertFalse(partyserver.open_port(45678))
        self.assertFalse(partyserver.close_port(45678))


# ---- 4. messages through the relay can't be sent again

class _Broker:
    """A stand-in MQTT relay: delivers what's published to who listens on that topic, and
    keeps a copy of everything that went by (as anyone watching the relay could)."""

    def __init__(self):
        self.subs, self.seen = [], []

    def mqtt(self, broker, topics, on_message, on_lost=None, timeout=10):
        broker_self = self

        class _M:
            def __init__(self):
                self.topics, self.alive = list(topics), True
                broker_self.subs.append(self)

            def publish(self, topic, payload, retain=False):
                broker_self.publish(topic, payload)

            def close(self):
                self.alive = False

            def deliver(self, topic, payload):
                on_message(topic, payload)
        return _M()

    def publish(self, topic, payload):
        self.seen.append((topic, payload))
        for sub in list(self.subs):
            if sub.alive and topic in sub.topics:
                sub.deliver(topic, payload)


class RelayReplayTest(unittest.TestCase):
    def setUp(self):
        fast(self)
        self.broker = _Broker()
        p = mock.patch.object(relay, "Mqtt", self.broker.mqtt)
        p.start()
        self.addCleanup(p.stop)
        self.secret = b"0123456789"
        self.srv = partyserver.PartyServer()
        self.addCleanup(self.srv.stop)
        self.hub = relay.Hub(self.secret, self.srv, 0)
        self.addCleanup(self.hub.close)
        self.ann = _Pipe()
        self.srv.serve(self.ann)
        self.ann.say({"Hello": {"username": "Ann", "room": {"name": relay.ROOM}}})

    def guest(self, name="Ben"):
        p = party.Party(relay.server_name(self.secret, 0), relay.ROOM, name, "guest").connect(timeout=5)
        self.addCleanup(p.leave)
        return p

    def to_host(self):
        return [blob for topic, blob in self.broker.seen if topic.endswith("/h")]

    def chats(self, text):
        return sum(1 for m in self.ann.got if (m.get("Chat") or {}).get("message") == text)

    def test_a_message_seen_going_by_cant_be_sent_again(self):
        ben = self.guest()
        ben.conn.send({"Chat": "hi"})
        self.assertTrue(wait_for(lambda: self.chats("hi") == 1))
        for blob in self.to_host():
            self.broker.publish(self.hub.box.topic + "/h", blob)
        time.sleep(0.3)
        self.assertEqual(self.chats("hi"), 1, "a replayed chat isn't said twice")

    def test_a_guest_who_left_cant_be_brought_back_by_replaying_them(self):
        ben = self.guest()
        before = self.to_host()
        ben.leave()
        self.assertTrue(wait_for(lambda: "Ben" not in [c.name for c in self.srv.clients]))
        for blob in before:
            self.broker.publish(self.hub.box.topic + "/h", blob)
        time.sleep(0.3)
        self.assertNotIn("Ben", [c.name for c in self.srv.clients])

    def test_the_hosts_messages_to_one_guest_dont_reach_another(self):
        ben = self.guest("Ben")
        cat = self.guest("Cat")
        for_ben = [blob for topic, blob in self.broker.seen if topic.endswith("/g/" + ben.conn.sid)]
        heard = []
        cat.conn.inbox = queue.Queue()
        for blob in for_ben:
            cat.conn._arrived(f"{self.hub.box.topic}/g/{cat.conn.sid}", blob)
        while not cat.conn.inbox.empty():
            heard.append(cat.conn.inbox.get())
        self.assertEqual(heard, [], "one guest's messages are turned away by another")
        for blob in for_ben:                             # (and by Ben, a second time)
            ben.conn._arrived(f"{self.hub.box.topic}/g/{ben.conn.sid}", blob)
        time.sleep(0.1)
        replays = [m for m in list(ben.conn.inbox.queue) if m and "Hello" in m]
        self.assertEqual(replays, [])

    def test_an_old_kind_of_message_is_dropped(self):
        old = self.hub.box.seal(json.dumps({"s": "abc", "m": {"Hello": {"username": "Old", "room": {"name": relay.ROOM}}}}).encode())
        self.broker.publish(self.hub.box.topic + "/h", old)
        stale = self.hub.box.seal(json.dumps({"s": "abd", "n": 1, "t": time.time() - 2 * relay.REPLAY_WINDOW,
                                              "m": {"Hello": {"username": "Stale", "room": {"name": relay.ROOM}}}}).encode())
        self.broker.publish(self.hub.box.topic + "/h", stale)
        for junk in (self.hub.box.seal(b"[1]"), self.hub.box.seal(b"\xff"), self.hub.box.seal(b"x" * (relay.MAX_MESSAGE + 1))):
            self.broker.publish(self.hub.box.topic + "/h", junk)
        time.sleep(0.3)
        self.assertEqual(self.hub.guests, {})
        self.assertNotIn("Old", [c.name for c in self.srv.clients])

    def test_the_party_still_works_through_the_relay(self):
        ben = self.guest()
        self.assertTrue(wait_for(lambda: "Ann" in ben.people))
        self.assertTrue(wait_for(lambda: any((((m.get("Set") or {}).get("user") or {}).get("Ben") or {}).get("file")
                                             for m in self.ann.got)), "Ann sees what Ben does")
        sent = len(self.to_host())
        self.assertTrue(wait_for(lambda: len(self.to_host()) > sent + 2), "Ben keeps answering the room")
        self.assertTrue(ben.active)


# ---- 5. a guest who vanishes is taken out of the room

class VanishedGuestTest(_Server):
    def test_a_guest_not_heard_from_is_removed_and_the_host_doesnt_wait_for_them(self):
        with mock.patch.object(partyserver, "DROP_AFTER", 1.0):
            port = self.srv.listen(0, "127.0.0.1")
            host = party.Party(f"127.0.0.1:{port}", "r", "Ann", "host").connect(timeout=5)
            self.addCleanup(host.leave)
            ben = self.join("Ben")
            ben.say({"Set": {"file": {"name": "Ep 2", "duration": 0, "size": 0,
                                      "watchlist": {"status": "preparing", "key": "k2"}}}})
            self.assertTrue(wait_for(lambda: host.person_state(host.people.get("Ben"))[0] == "preparing"))
            sent = []
            session = type("S", (), {"connected": True, "dur": 100.0, "pos": 0.0, "paused": True,
                                     "send": lambda self, *c: sent.extend(c)})()
            host._announced, host._hold_until = True, time.time() + 180
            host._release_hold(session)
            self.assertEqual(sent, [], "the host waits for Ben while he's there")
            # Ben's app vanishes (no goodbye): he's taken out, and the host plays.
            self.assertTrue(wait_for(lambda: "Ben" not in host.people, 4), "Ben is taken out of the room")
            self.assertTrue(host.active, "the host (who answers) stays")
            host._release_hold(session)
            self.assertIn(["set_property", "pause", False], sent)

    def test_a_connection_that_never_says_hello_is_dropped(self):
        with mock.patch.object(partyserver, "DROP_AFTER", 0.5):
            pipe = _Pipe()
            self.srv.serve(pipe)
            self.assertTrue(wait_for(lambda: not self.srv.clients, 3))

    def test_a_guest_hears_when_the_relay_goes_quiet(self):
        broker = _Broker()
        with mock.patch.object(relay, "Mqtt", broker.mqtt), mock.patch.object(relay, "IDLE", 0.3):
            conn = relay.Connection(b"0123456789", 0)
            with self.assertRaises(OSError):
                conn.read()


class SyncplayCheckTest(unittest.TestCase):
    def test_a_strange_invite_is_a_valueerror(self):
        import base64
        for data in ({"r": "room", "ep": {"x": 1}}, {"r": "room", "ep": 2, "se": [1]}):
            raw = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
            with self.assertRaises(ValueError):
                syncplay.read_invite("WLP1." + raw)


if __name__ == "__main__":
    unittest.main()
