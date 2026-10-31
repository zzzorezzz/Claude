"""The watch-party server inside the host's app (when Syncplay's servers are down)."""
import queue
import time
import unittest

from watchlist import party, partyserver


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


def wait_for(cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class PartyServerTest(unittest.TestCase):
    def setUp(self):
        self.srv = partyserver.PartyServer()
        self.addCleanup(self.srv.stop)

    def join(self, name, room="r"):
        pipe = _Pipe()
        self.srv.serve(pipe)
        pipe.say({"Hello": {"username": name, "room": {"name": room}}})
        self.assertTrue(wait_for(lambda: any("Hello" in m for m in pipe.got)))
        return pipe

    def test_people_files_and_chat_reach_the_others(self):
        a, b = self.join("Ann"), self.join("Ben")
        self.assertTrue(wait_for(lambda: any("Set" in m and "Ben" in m["Set"]["user"] for m in a.got)), "Ann sees Ben join")
        a.say({"Set": {"file": {"name": "Ep 1", "duration": 1400}}})
        self.assertTrue(wait_for(lambda: any((m.get("Set") or {}).get("user", {}).get("Ann", {}).get("file") for m in b.got)))
        b.say({"List": None})
        self.assertTrue(wait_for(lambda: any("List" in m for m in b.got)))
        listing = next(m["List"]["r"] for m in b.got if "List" in m)
        self.assertEqual(sorted(listing), ["Ann", "Ben"])
        a.say({"Chat": "hi"})
        self.assertTrue(wait_for(lambda: any((m.get("Chat") or {}).get("message") == "hi" for m in b.got)))

    def test_a_pause_by_one_goes_to_the_others(self):
        a, b = self.join("Ann"), self.join("Ben")
        a.say({"State": {"playstate": {"position": 42.0, "paused": True}, "ignoringOnTheFly": {"client": 1},
                         "ping": {"clientLatencyCalculation": time.time(), "clientRtt": 0}}})

        def forced():
            return [m["State"] for m in b.got if "State" in m and (m["State"].get("ignoringOnTheFly") or {}).get("server")]
        self.assertTrue(wait_for(lambda: forced()))
        state = forced()[0]
        self.assertTrue(state["playstate"]["paused"])
        self.assertAlmostEqual(state["playstate"]["position"], 42.0, delta=1)
        self.assertEqual(state["playstate"]["setBy"], "Ann")
        # Ann's own change is acknowledged to her (she stops ignoring the room).
        self.assertTrue(wait_for(lambda: any((m.get("State") or {}).get("ignoringOnTheFly", {}).get("client") == 1 for m in a.got)))

    def test_rooms_are_kept_apart_and_a_leaver_is_told(self):
        a, b, c = self.join("Ann", "r1"), self.join("Ben", "r1"), self.join("Cat", "r2")
        self.assertFalse(any("Cat" in str(m.get("Set")) for m in a.got))
        b.close()
        self.assertTrue(wait_for(lambda: any((m.get("Set") or {}).get("user", {}).get("Ben", {}).get("event", {}).get("left")
                                             for m in a.got)))

    def test_the_apps_own_party_code_joins_it(self):
        port = self.srv.listen(0, "127.0.0.1")
        host = party.Party(f"127.0.0.1:{port}", "room", "Host", "host").connect()
        self.addCleanup(host.leave)
        guest = party.Party(f"127.0.0.1:{port}", "room", "Guest", "guest").connect()
        self.addCleanup(guest.leave)
        self.assertTrue(wait_for(lambda: sorted(guest.people) == ["Guest", "Host"]))
        self.assertEqual(guest.leader_name(), "Host")


if __name__ == "__main__":
    unittest.main()
