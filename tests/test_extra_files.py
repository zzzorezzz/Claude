"""Settings → Downloads → Extra files: only what no title plays from."""
from tests.test_scanner import touch
from tests.test_server import ServerBase


class ExtraFilesApiTest(ServerBase):
    def test_a_file_in_a_titles_own_folder_isnt_extra(self):
        dl = self.media / "Downloads"
        mine = touch(dl, "Breaking Bad/Breaking.Bad.S01E01.mkv")
        stray = touch(dl, "Old stuff/clip.mkv")
        self.app.settings.update({"downloads_folder": str(dl)})
        self.app.library.scan(force=True)
        for f in (mine, stray):
            self.app.library.route(f.relative_to(dl).as_posix(), "ignore")
        show = self.add("tv", "tmdb", "tv/1396")
        self.call("PUT", f"/api/items/{show['id']}", {"local_folder": str(mine.parent)})
        status, r = self.call("GET", "/api/library/extra")
        self.assertEqual(status, 200, r)
        self.assertEqual([f["rel"] for f in r["files"]], ["Old stuff/clip.mkv"])
