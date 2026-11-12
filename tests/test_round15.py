"""Round 15 (1.3.9): the whole-app check's problems fixed - episode names read wrongly, torrents
that lost files or stopped (a pack other titles use, "Keep" while files are checked, "Download
whole" while playing, a full disk, resume data)."""
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from watchlist import scanner, torrents, transcode


def video(rel, season, episodes, **extra):
    return dict({"rel": rel, "path": "/dl/" + rel, "name": rel.split("/")[-1], "season": season,
                 "episodes": list(episodes), "kind": "episode"}, **extra)


class EpisodeNamesTest(unittest.TestCase):
    def check(self, rel, season, episodes):
        self.assertEqual(scanner.parse_episode(tuple(rel.split("/"))), (season, tuple(episodes)), rel)

    def test_a_season_then_ep(self):
        self.check("Show S02 EP05 1080p.mkv", 2, [5])
        self.check("Show.S2.Episode.5.mkv", 2, [5])
        self.check("Show S02E05.mkv", 2, [5])                     # (as before)

    def test_a_recap_between_two_episodes_takes_neither_place(self):
        self.check("[Group] Show - 05.5 [1080p].mkv", 0, [])
        self.check("Show E12.5.mkv", 0, [])
        self.check("[Group] Show - 05 [1080p].mkv", None, [5])
        self.check("Show 5.1 Audio - 05.mkv", None, [5])          # (5.1 sound isn't a recap)

    def test_a_range_of_episodes(self):
        self.check("Show.S01E01-03.mkv", 1, [1, 2, 3])
        self.check("Show.S01E01-E03.mkv", 1, [1, 2, 3])
        self.check("Show.S01E01E02.mkv", 1, [1, 2])              # (two, as before)
        self.check("Show.S01E01-1080p.mkv", 1, [1])

    def test_an_episode_past_the_seasons_known_size_stays_in_its_season(self):
        show = {"seasons": [{"number": 1, "episodes": 12}, {"number": 2, "episodes": 12}]}
        files = [video(f"S2/Show.S02E{n:02d}.mkv", 2, [n]) for n in (1, 12, 13)] + [video("S1/Show.S01E13.mkv", 1, [13])]
        found = scanner.episode_map(show, files)
        self.assertEqual(found[(2, 13)]["name"], "Show.S02E13.mkv")
        self.assertEqual(found[(2, 1)]["name"], "Show.S02E01.mkv")
        self.assertEqual(found[(1, 13)]["name"], "Show.S01E13.mkv")

    def test_a_season_counted_from_the_start_still_is(self):
        tmdb = {"seasons": [{"number": n, "episodes": 20} for n in range(1, 21)]}
        found = scanner.episode_map(tmdb, [video("S12/y S12E250.mkv", 12, [250]), video("S12/y S12E251.mkv", 12, [251])])
        self.assertEqual(sorted(found), [(13, 10), (13, 11)])


class FakeHandle:
    def __init__(self, errc=0):
        self.code = errc
        self.calls = []

    def status(self):
        code = self.code
        return mock.Mock(errc=mock.Mock(value=lambda: code), state="downloading")

    def clear_error(self):
        self.calls.append("clear")
        self.code = 0

    def resume(self):
        self.calls.append("resume")

    def is_valid(self):
        return True


@unittest.skipUnless(torrents.lt, "libtorrent isn't installed")
class FullDiskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        app = mock.Mock()
        app.settings.get.return_value = {"downloads_folder": ""}
        self.t = torrents.Torrents(app, self.tmp.name)
        self.rec = {"hash": "a" * 40, "item_id": "x", "save_path": self.tmp.name, "files": [], "priorities": {},
                    "overrides": {}, "finished": {}, "error": "No space left on device"}
        self.t.records[self.rec["hash"]] = self.rec

    def test_a_torrent_stopped_by_a_full_disk_goes_again_once_theres_room(self):
        handle = FakeHandle(errc=112)
        dict.__setitem__(self.t.handles, self.rec["hash"], handle)
        full = mock.Mock(free=1024)
        with mock.patch.object(torrents.shutil, "disk_usage", return_value=full):
            self.t._retry_errors()
        self.assertEqual(handle.calls, [], "still full: left as it is")
        self.rec["retried"] -= torrents.RETRY_AFTER + 1
        with mock.patch.object(torrents.shutil, "disk_usage", return_value=mock.Mock(free=10 ** 10)):
            self.t._retry_errors()
        self.assertEqual(handle.calls, ["clear", "resume"])
        self.assertNotIn("error", self.rec)

    def test_a_torrent_paused_by_hand_isnt_started(self):
        handle = FakeHandle(errc=112)
        dict.__setitem__(self.t.handles, self.rec["hash"], handle)
        self.rec["paused"] = True
        with mock.patch.object(torrents.shutil, "disk_usage", return_value=mock.Mock(free=10 ** 10)):
            self.t._retry_errors()
        self.assertEqual(handle.calls, [])


class SlowSwitchTest(unittest.TestCase):
    """mpv tells which entry a new file is late (after its length and first positions): those are
    the new entry's, not the one before's - and the new one is still marked watched at its end."""

    def follow(self, messages):
        from watchlist import player

        class Conn:
            def __init__(self, *a, **k):
                pass

            def send(self, *a):
                pass

            def messages(self):
                yield from messages

            def close(self):
                pass

        saved, finished = [], []
        entries = [{"path": f"/v/{n}.mkv", "key": f"1:{n}", "label": f"Episode {n}"} for n in (1, 2)]
        s = player.Session("mpv", "x", entries, on_progress=lambda i, e, pos, dur: saved.append((e["key"], pos, dur)),
                           on_finished=lambda i, e: finished.append(e["key"]))
        with mock.patch.object(player, "_Connection", Conn), mock.patch.object(player, "SAVE_EVERY", 0):
            s._follow()
        return saved, finished

    def change(self, name, data):
        return {"event": "property-change", "name": name, "data": data}

    def test_the_next_files_length_and_place_arent_the_last_ones(self):
        saved, finished = self.follow([
            self.change("duration", 1400.0), self.change("time-pos", 1390.0),
            {"event": "end-file", "reason": "eof"}, {"event": "start-file"},
            self.change("duration", 1500.0), self.change("time-pos", 1.0), self.change("time-pos", 2.0),
            {"event": "file-loaded"},
            self.change("path", "/v/2.mkv"), self.change("playlist-pos", 1),
            self.change("time-pos", 1450.0), {"event": "end-file", "reason": "eof"}])
        self.assertEqual(finished, ["1:1", "1:2"])
        self.assertFalse([x for x in saved if x[0] == "1:1" and x[1] < 1000], "episode 1's place stays at its end")
        self.assertIn(("1:2", 1450.0, 1500.0), saved)


class SharingAndPhoneSafetyTest(unittest.TestCase):
    def test_a_share_code_built_to_unpack_to_gigabytes_is_turned_down(self):
        import base64
        import zlib
        from watchlist import share
        bomb = zlib.compress(b'["x","",[' + b" " * (64 * 1024 * 1024) + b"]]", 9)
        code = share.SHORT + base64.urlsafe_b64encode(bomb).decode().rstrip("=")
        start = time.time()
        with self.assertRaises(share.ShareError):
            share.read(code)
        self.assertLess(time.time() - start, 2)
        # (a real one still reads)
        data = share.pack("Mine", [{"title": "Dune", "type": "movie", "source": "tmdb", "source_id": "movie/1"}])
        self.assertEqual(share.read(share.to_code(data))["items"][0]["title"], "Dune")

    def test_only_web_addresses_are_passed_through_to_a_phone(self):
        from watchlist import webplay
        for url in ("file:///C:/Windows/win.ini", "FILE:///etc/passwd", "ftp://x/y", "C:/secret.srt"):
            with self.assertRaises(ValueError, msg=url):
                webplay.open_remote(url, {})


from tests.test_remote import RemoteBase  # noqa: E402


class PhoneSubtitleTest(RemoteBase):
    def test_a_phone_cant_use_a_subtitle_file_from_anywhere(self):
        token, _ = self.pair()
        item = self.add("tv", "tmdb", "tv/1396")
        show = self.media / "Breaking Bad"
        (show / "Season 1").mkdir(parents=True)
        (show / "Season 1" / "Breaking.Bad.S01E01.mkv").write_bytes(b"x" * 10)
        (show / "Season 1" / "Breaking.Bad.S01E01.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        secret = self.media / "elsewhere" / "private.srt"
        secret.parent.mkdir(parents=True)
        secret.write_text("1\n00:00:01,000 --> 00:00:02,000\nsecret\n")
        self.call("PUT", f"/api/items/{item['id']}", {"local_folder": str(show)})
        video = str(show / "Season 1" / "Breaking.Bad.S01E01.mkv")
        status, r, _ = self.phone("POST", f"/api/items/{item['id']}/play", {"path": video, "subtitle": str(secret),
                                                                          "season": 1, "episode": 1}, cookie=token)
        self.assertEqual(status, 400, r)
        self.assertIn("subtitle", r["error"])
        status, r, _ = self.phone("POST", f"/api/items/{item['id']}/subs/folder", {"path": str(secret.parent)}, cookie=token)
        self.assertEqual(status, 400, r)
        # (the computer itself may use any)
        status, r = self.call("POST", f"/api/items/{item['id']}/subs/folder", {"path": str(secret.parent)})
        self.assertEqual(status, 200, r)


class OddAIAnswersTest(unittest.TestCase):
    """An AI answering in another shape than asked, or naming a film "1917" as a number."""

    def answer(self, reply):
        from watchlist import ai
        return mock.patch.object(ai, "ask", return_value=(reply, "gemini"))

    def test_mood_picks(self):
        from watchlist import ai
        with self.answer({"intro": 5, "from_list": [{"id": {"x": 1}}, {"id": 12, "why": None}, "junk"],
                          "new": [{"title": 1917, "year": "2019", "type": "Movie", "why": ["war"]},
                                  {"title": None, "type": "movie"}, {"title": "Dunkirk", "type": "film"}]}):
            got = ai.mood_picks({}, "war", ["films"], None, [], [])
        self.assertEqual(got["new"], [{"title": "1917", "year": 2019, "type": "movie", "why": ""}])
        self.assertEqual(got["from_list"], [{"id": "12", "why": ""}])
        with self.answer([{"title": "Heat", "year": 1995, "type": "movie", "why": "crime"}]):
            self.assertEqual(ai.mood_picks({}, "crime", ["films"], None, [], [])["new"][0]["title"], "Heat")

    def test_recaps(self):
        from watchlist import ai
        item = {"title": "Dark", "type": "tv"}
        with self.answer(["They meet.", "The cave."]):
            self.assertEqual(ai.recap({}, item, 1, 3)["points"], ["They meet.", "The cave."])
        with self.answer({"known": "false", "points": []}):
            self.assertFalse(ai.recap({}, item, 1, 3)["known"])
        with self.answer({"known": True, "points": "- One\n- Two"}):
            self.assertEqual(ai.recap({}, item, 1, 3)["points"], ["One", "Two"])
        with self.answer({"known": True, "points": [{"text": "One"}, 7]}):
            self.assertEqual(ai.recap({}, item, 1, 3)["points"], ["One", "7"])
        with self.answer({"known": True, "points": None}), self.assertRaises(ai.AIError):
            ai.recap({}, item, 1, 3)


class TorrentRankTest(unittest.TestCase):
    def test_a_part_of_the_series_isnt_above_all_of_it(self):
        from watchlist import sites
        rows = []
        for name, seeds in (("[Grp] Mystery Show 25-37 [1080p]", 500), ("[Grp] Mystery Show (Complete Series) [1080p]", 50),
                            ("[Grp] Mystery Show 01-37 [1080p]", 40)):
            r = {"name": name, "about": 100, "release": sites.describe_release(name), "seeders": seeds}
            rows.append((sites.Finder._name_score(r, 37), name))
        rows.sort(reverse=True)
        self.assertNotIn("25-37", rows[0][1])
        self.assertIn("25-37", rows[-1][1])


class CommandLineQuitTest(unittest.TestCase):
    def test_ctrl_c_ends_the_players_too(self):
        from watchlist import __main__ as cli
        app, server = mock.Mock(), mock.Mock()
        app.settings.get.return_value = {"browser_path": ""}
        server.serve_forever.side_effect = KeyboardInterrupt
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(cli, "App", return_value=app), mock.patch.object(cli, "make_server", return_value=server), \
                mock.patch.object(cli, "_already_running", return_value=False), \
                mock.patch.object(cli, "start_background_refresh"), mock.patch.object(cli, "_register_on_mac"):
            self.assertEqual(cli.main(["--no-open", "--data", tmp, "--port", "1"]), 0)
        app.shutdown.assert_called_once()


class SyncTest(unittest.TestCase):
    """Two computers on one sync folder (the cloud app copying at once)."""

    def setUp(self):
        from watchlist.store import Store, new_item
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "cloud").mkdir()
        self.Store, self.new_item = Store, new_item
        self.a = Store(self.root / "cloud" / "watchlist.json", self.root / "a" / "local.json")
        self.b = Store(self.root / "cloud" / "watchlist.json", self.root / "b" / "local.json")
        self.heat = self.a.add(new_item("movie", {"source": "tmdb", "source_id": "1", "title": "Heat"}))
        self.b.get(self.heat["id"])

    def test_a_change_just_made_on_the_other_computer_isnt_written_over(self):
        self.a.update(self.heat["id"], {"review": {"stars": 5, "text": "great", "at": time.time()}})
        self.b._checked_at = time.monotonic()                 # (B looked a moment ago)
        self.b.update(self.heat["id"], {"positions": {"movie": {"pos": 100, "dur": 6000, "at": time.time()}}})
        fresh = self.Store(self.root / "cloud" / "watchlist.json", self.root / "c" / "local.json").get(self.heat["id"])
        self.assertEqual((fresh["review"]["text"], fresh["positions"]["movie"]["pos"]), ("great", 100))

    def test_a_title_deleted_on_the_other_computer_doesnt_come_back(self):
        self.a.delete(self.heat["id"])
        self.b._checked_at = time.monotonic()
        with self.assertRaises(KeyError):
            self.b.update(self.heat["id"], {"positions": {"movie": {"pos": 100, "dur": 6000, "at": time.time()}}})
        self.assertEqual(self.b.list(), [])

    def test_a_list_that_cant_be_opened_at_the_start_isnt_written_over(self):
        from watchlist import store
        real, calls = store._read_quiet, []

        def busy_once(path):
            calls.append(path)
            if len(calls) == 1:
                raise store.Busy("held by the cloud app")
            return real(path)
        with mock.patch.object(store, "_read_quiet", side_effect=busy_once):
            c = self.Store(self.root / "cloud" / "watchlist.json", self.root / "c" / "local.json")
        self.assertTrue(c.blocked)
        c._checked_at = 0                                     # (looked at again: it opens now)
        self.assertEqual([i["title"] for i in c.list()], ["Heat"])
        c.add(self.new_item("movie", {"source": "tmdb", "source_id": "2", "title": "Alien"}))
        self.a._checked_at = 0
        self.assertEqual(sorted(i["title"] for i in self.a.list()), ["Alien", "Heat"])

    def test_a_list_still_held_isnt_saved_over(self):
        from watchlist import store
        with mock.patch.object(store, "_read_quiet", side_effect=store.Busy("held")):
            c = self.Store(self.root / "cloud" / "watchlist.json", self.root / "c" / "local.json")
            with self.assertRaises(store.Busy):
                c.add(self.new_item("movie", {"source": "tmdb", "source_id": "2", "title": "Alien"}))
        self.assertEqual([i["title"] for i in self.a.list()], ["Heat"])

    def test_the_sync_folder_going_away_keeps_changes_here_and_this_computers_copy_is_current(self):
        mirror = self.root / "a" / "watchlist.json"
        self.assertTrue(mirror.is_file(), "this computer's own copy kept up to date")
        gone = self.root / "drive-file"
        gone.write_text("x")
        self.a.path = gone / "watchlist.json"                  # (can't be written: the drive went away)
        self.a.update(self.heat["id"], {"progress": {"season": 1, "episode": 4}})
        self.assertEqual(self.a.away, gone / "watchlist.json")
        later = self.Store(mirror, self.root / "a" / "local.json")
        self.assertEqual(later.get(self.heat["id"])["progress"]["episode"], 4)

    def test_a_damaged_copy_beside_the_list_doesnt_stop_it_opening(self):
        bad = self.root / "cloud" / "watchlist (conflicted copy).json"
        bad.write_text('{"items": [{"id": "x", "title": "T", "modified": "yesterday", "collections": []}, 5],'
                       ' "collections": [{"id": "c"}], "deleted": ["x"]}', encoding="utf-8")
        c = self.Store(self.root / "cloud" / "watchlist.json", self.root / "c" / "local.json")
        self.assertIn("Heat", [i["title"] for i in c.list()])


class ListAndSortingTest(unittest.TestCase):
    def setUp(self):
        from watchlist.server import App
        from watchlist.store import new_item
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = App(self.root / "pc")
        self.addCleanup(self.app.torrents.shutdown)
        self.new_item = new_item

    def test_undo_keeps_a_title_youve_used_since_sort_now_added_it(self):
        dl = self.root / "Downloads"
        dl.mkdir()
        for n in (1, 2):
            (dl / f"Some Show - 0{n}.mkv").write_bytes(b"x" * 100)
        app = self.app
        app.settings.update({"downloads_folder": str(dl)})
        app.library.scan(force=True)
        item = app.store.add(self.new_item("anime", {"source": "mal", "source_id": "77", "title": "Some Show",
                                                     "seasons": [{"number": 1, "episodes": 12}]}))
        q = [q for q in app.library.pending() if q["kind"] == "title"][0]
        app.library.assign_many([app.sorter._decision(q, item["id"], "search", "found", added=True)])
        app.store.update(item["id"], {"progress": {"season": 1, "episode": 9}, "last_played": time.time()})
        app.undo_sorted(q["key"])
        self.assertTrue(any(i["id"] == item["id"] for i in app.store.list()))
        # (one untouched since it was added still goes)
        other = app.store.add(self.new_item("anime", {"source": "mal", "source_id": "78", "title": "Some Show"}))
        app.library.assign_many([app.sorter._decision(q, other["id"], "search", "found", added=True)])
        app.undo_sorted(q["key"])
        self.assertFalse(any(i["id"] == other["id"] for i in app.store.list()))

    def test_a_pack_undone_stays_undone(self):
        dl = self.root / "Downloads"
        top = dl / "Frieren S1+S2" / "Season 1"
        top.mkdir(parents=True)
        for n in (1, 2):
            (top / f"Frieren - 0{n}.mkv").write_bytes(b"x")
        app = self.app
        app.settings.update({"downloads_folder": str(dl)})
        item = app.store.add(self.new_item("anime", {"source": "mal", "source_id": "5", "title": "Frieren", "format": "series",
                                                     "seasons": [{"number": 1, "episodes": 12}]}))
        app.library.scan(force=True)
        rels = [v["rel"] for v in app.library.videos]
        app.library.assign_many([{"key": "pack:Frieren S1+S2:5", "files": rels, "item": item["id"], "by": "rules",
                                  "why": "part of the pack", "replace": True,
                                  "episodes": {r: [1, i + 1] for i, r in enumerate(rels)}}])
        app.undo_sorted("pack:Frieren S1+S2:5")
        self.assertEqual(app.sorter.run_now()["sorted"], 0)
        self.assertFalse(any(r in app.library.answers["files"] for r in rels))

    def test_titles_only_in_a_deleted_collection_stay_in_your_list(self):
        from watchlist import share
        app = self.app
        app.fill_later = lambda item_id: None
        code = share.to_code(share.pack("Picks", [{"title": "Heat", "type": "movie", "source": "tmdb", "source_id": "movie/949"}]))
        r = app.import_shared({"code": code, "add_to_list": False})
        app.store.delete_collection(r["collection"]["id"])
        self.assertEqual([(i["title"], i["listed"]) for i in app.store.list()], [("Heat", True)])

    def test_a_show_added_from_search_is_a_show_until_its_info_comes(self):
        self.assertEqual(self.new_item("tv", {"source": "tmdb", "source_id": "tv/1", "title": "Dark"})["format"], "series")
        self.assertEqual(self.new_item("anime", {"source": "mal", "source_id": "1", "title": "Frieren"})["format"], "series")
        self.assertEqual(self.new_item("anime", {"source": "mal", "source_id": "2", "title": "Akira",
                                                 "media_type": "movie"})["format"], "movie")
        self.assertEqual(self.new_item("movie", {"source": "tmdb", "source_id": "movie/1", "title": "Heat"})["format"], "movie")

    def test_use_myanimelist_into_a_title_you_have_keeps_whats_yours(self):
        from watchlist import metadata
        app = self.app
        app.settings.update({"mal_client_id": "fake"})
        show = self.root / "My Anime"
        show.mkdir()
        tm = app.store.add(self.new_item("tv", {"source": "tmdb", "source_id": "tv/1", "title": "Frieren", "format": "series",
                                                "seasons": [{"number": 1, "episodes": 28}]}))
        app.store.add(self.new_item("anime", {"source": "mal", "source_id": "52991", "title": "Sousou no Frieren",
                                              "format": "series", "seasons": [{"number": 1, "episodes": 28}]}))
        app.store.update(tm["id"], {"local_folder": str(show), "review": {"stars": 5, "text": "mine", "at": 1},
                                    "last_played": time.time()})
        app.torrents.records["abc"] = {"item_id": tm["id"], "hash": "abc", "files": [], "save_path": str(self.root),
                                       "overrides": {}}
        info = {"source": "mal", "source_id": "52991", "title": "Sousou no Frieren", "format": "series",
                "episodes_total": 28, "seasons": [{"number": 1, "episodes": 28}]}
        with mock.patch.object(metadata, "details", return_value=info):
            out = app.use_mal(app.store.get(tm["id"]), "52991")
        self.assertEqual((out["local_folder"], out["review"]["text"]), (str(show), "mine"))
        self.assertTrue(out["last_played"])
        self.assertEqual(app.torrents.records["abc"]["item_id"], out["id"])

    def test_two_threads_saving_the_same_file(self):
        import threading
        from watchlist.config import read_json, write_json
        path, failed = self.root / "library.json", []

        def save(n):
            for i in range(60):
                try:
                    write_json(path, {"n": n, "i": i}, compact=True)
                except OSError as e:
                    failed.append(e)
        threads = [threading.Thread(target=save, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(failed, [])
        self.assertIn("n", read_json(path, {}))


class SaveToDeviceStuckTest(unittest.TestCase):
    def test_a_conversion_that_gets_nowhere_stops(self):
        """A damaged file: ffmpeg says a lot (more than a pipe holds) and gets no further."""
        import sys
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mp4 = transcode.Mp4("x.mkv", {"duration": 100, "video": {"codec": "h264", "bits": 8, "pix_fmt": "yuv420p",
                                                                "profile": "", "height": 720, "width": 1280},
                                      "audio": [], "subs": [], "format": "matroska"}, Path(tmp.name) / "o.mp4")
        mp4.STUCK = 1
        talk = "import sys, time; sys.stderr.write('damaged ' * 50000); sys.stderr.flush(); time.sleep(30)"
        with mock.patch.object(mp4, "command", return_value=[sys.executable, "-c", talk]):
            start = time.time()
            with self.assertRaises(RuntimeError) as got:
                mp4.run()
        self.assertLess(time.time() - start, 10)
        self.assertIn("further", str(got.exception))


@unittest.skipUnless(transcode.find()[0], "needs ffmpeg")
class PhoneFromPartWayTest(unittest.TestCase):
    """A phone's video repackaged from part way (a torrent's stream, or a seek past what's ready)
    starts at a key frame before that point: its place and its subtitles are counted from there."""

    def test_it_starts_at_the_key_frame_and_the_subtitles_follow(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "s.srt").write_text("1\n00:00:15,000 --> 00:00:16,000\nfifteen\n", encoding="utf-8")
        src = root / "v.mkv"
        ffmpeg = transcode.find()[0]
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=duration=30:size=160x120:rate=10", "-i", str(root / "s.srt"), "-map", "0", "-map", "1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "100", "-keyint_min", "100", "-sc_threshold", "0", "-c:s", "srt",
                        str(src)], check=True, capture_output=True, timeout=60)
        info = transcode.probe(str(src))
        how = transcode.plan(info, {}, "original")
        self.assertEqual(how["video"], "copy")
        job = transcode.Job(str(src), info, how, root / "job", start=13, subs=[0]).run()
        self.addCleanup(job.stop)
        self.assertEqual(job.start, 10.0, "the key frame before 13 s")
        self.assertTrue(job.wait_for("video/index.m3u8", 30))
        end = time.time() + 30
        while job.alive() and time.time() < end:
            time.sleep(0.2)
        vtt = (root / "job" / "sub0.vtt").read_text(encoding="utf-8")
        self.assertIn("00:05.000 --> 00:06.000", vtt)                # 15 s, from 10 s
        self.assertAlmostEqual(job.ready_until(), 20, delta=1.5)     # 10 s to 30 s: all of it from there


if __name__ == "__main__":
    unittest.main()
