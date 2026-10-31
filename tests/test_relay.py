"""The backup relay's encryption and the invites for the backup ways of watching together."""
import unittest

from watchlist import relay, syncplay


class RelayTest(unittest.TestCase):
    def test_only_the_party_can_read_its_messages(self):
        box = relay.Box(b"0123456789")
        sealed = box.seal(b'{"Hello": {"username": "Ann"}}')
        self.assertNotIn(b"Ann", sealed)
        self.assertEqual(box.open(sealed), b'{"Hello": {"username": "Ann"}}')
        changed = sealed[:-1] + bytes([sealed[-1] ^ 1])
        self.assertIsNone(box.open(changed), "a changed message is turned away")
        self.assertIsNone(relay.Box(b"another-key").open(sealed), "another party's key can't read it")
        self.assertNotEqual(box.topic, relay.Box(b"another-key").topic)

    def test_a_relay_invite_carries_its_key_and_relay(self):
        secret = bytes(range(10))
        code = syncplay.relay_invite(secret, 1)
        self.assertRegex(code, r"^WA-[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}-R1$")
        got = syncplay.read_invite(f"join me: {code.lower()} !")
        self.assertEqual(relay.parse(got["server"]), (secret, 1))
        self.assertEqual(got["room"], relay.ROOM)

    def test_short_invites_for_each_kind_of_server(self):
        room = syncplay.new_room()
        for server in ("syncplay.pl:8999", "syncplay.pl:8995", "192.168.1.20:8999"):
            got = syncplay.read_invite(syncplay.short_invite(server, room))
            self.assertEqual((got["server"], got["room"]), (server, room))


if __name__ == "__main__":
    unittest.main()
