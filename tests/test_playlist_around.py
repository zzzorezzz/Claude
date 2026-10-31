"""Your own files: the player gets the whole season around the episode you play (the
ones before it too, so Previous plays), starting at that one."""

from tests.test_scanner import touch
from tests.test_server import ServerBase


class PlaylistAroundTest(ServerBase):
    def test_the_episodes_before_it_are_in_the_playlist_too(self):
        files = [touch(self.media, f"show/Breaking.Bad.S01E0{n}.mkv") for n in range(1, 5)]
        show = self.add("tv", "tmdb", "tv/1396")
        self.call("PUT", f"/api/items/{show['id']}", {"local_folder": str(files[0].parent)})
        item = self.app.store.get(show["id"])
        entries, patch = self.app._entries(item, str(files[2]), None, 1, 3, True, around=True)
        self.assertEqual(patch["first"], 2)
        self.assertEqual([e["key"] for e in entries], ["1:1", "1:2", "1:3", "1:4"])
        self.assertEqual(entries[patch["first"]]["path"], str(files[2]))

    def test_without_around_it_starts_with_the_episode(self):
        files = [touch(self.media, f"show/Breaking.Bad.S01E0{n}.mkv") for n in range(1, 4)]
        show = self.add("tv", "tmdb", "tv/1396")
        self.call("PUT", f"/api/items/{show['id']}", {"local_folder": str(files[0].parent)})
        item = self.app.store.get(show["id"])
        entries, patch = self.app._entries(item, str(files[1]), None, 1, 2, True)
        self.assertNotIn("first", patch)
        self.assertEqual([e["key"] for e in entries], ["1:2", "1:3"])
