"""Home → Currently watching: playing puts a title there; you can add it, move it on, take it off."""
from tests.test_server import ServerBase


class CurrentlyWatchingTest(ServerBase):
    def test_new_title_isnt_there(self):
        show = self.add("tv", "tmdb", "tv/1396")
        self.assertIsNone(show["watching"])

    def test_playing_puts_it_there(self):
        show = self.add("tv", "tmdb", "tv/1396")
        item = self.app.store.update(show["id"], {"last_played": 123.0})
        self.assertIs(item["watching"], True)

    def test_add_where_and_take_off(self):
        show = self.add("tv", "tmdb", "tv/1396")
        status, item = self.call("PUT", f"/api/items/{show['id']}",
                                 {"watching": True, "progress": {"season": 2, "episode": 5}})
        self.assertEqual(status, 200, item)
        self.assertIs(item["watching"], True)
        self.assertEqual(item["progress"], {"season": 2, "episode": 5})
        status, item = self.call("PUT", f"/api/items/{show['id']}", {"watching": False})
        self.assertIs(item["watching"], False)
        # Playing it again brings it back.
        self.assertIs(self.app.store.update(show["id"], {"last_played": 200.0})["watching"], True)

    def test_watched_it_all_takes_it_off(self):
        show = self.add("tv", "tmdb", "tv/1396")
        self.call("PUT", f"/api/items/{show['id']}", {"watching": True})
        status, item = self.call("PUT", f"/api/items/{show['id']}", {"watched": True})
        self.assertIs(item["watching"], False)
