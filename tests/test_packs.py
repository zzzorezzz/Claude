"""Packs: a whole franchise in one torrent or folder, matched entry by entry."""

import unittest
from unittest import mock

from tests import test_server
from watchlist import ai, metadata, packs
from watchlist.store import new_item

ROOT = "[Anime Time] Haikyuu!! (S01+02+03+04+Movies+OVAs+OADs) [BD] [Dual Audio][1080p][HEVC 10bit x265][AAC][Eng Sub]"
TAIL = " [BD][1080p][HEVC 10bit x265][AAC][Eng Sub].mkv"


def entry(sid, title, kind, episodes, date, relation="Sequel", names=()):
    return {"source_id": sid, "title": title, "names": [title, *names], "kind": kind,
            "media_type": kind.upper(), "episodes": episodes, "date": date, "year": int(date[:4]), "relation": relation}


# Haikyuu!! on MyAnimeList: every season, part, film and OVA is its own entry.
ENTRIES = [
    entry("20583", "Haikyuu!!", "tv", 25, "2014-04-06", "This title"),
    entry("29243", "Haikyuu!! Lev Genzan!", "ova", 1, "2014-11-04", "Side story"),
    entry("31086", "Haikyuu!! Movie 1: Owari to Hajimari", "movie", 1, "2015-07-03", "Summary"),
    entry("31087", "Haikyuu!! Movie 2: Shousha to Haisha", "movie", 1, "2015-09-18", "Summary"),
    entry("28891", "Haikyuu!! Second Season", "tv", 25, "2015-10-04"),
    entry("32935", "Haikyuu!! Karasuno Koukou vs. Shiratorizawa Gakuen Koukou", "tv", 10, "2016-10-08",
          names=["Haikyu!! 3rd Season"]),
    entry("36472", "Haikyuu!! Riku vs. Kuu", "ova", 2, "2019-08-02", "Side story"),
    entry("38883", "Haikyuu!! To the Top", "tv", 13, "2020-01-11"),
    entry("40776", "Haikyuu!! To the Top Part 2", "tv", 12, "2020-10-03", names=["Haikyuu!! To the Top 2nd Season"]),
    entry("52742", "Haikyuu!! Movie: Gomisuteba no Kessen", "movie", 1, "2024-02-16",
          names=["Haikyu!! The Dumpster Battle"]),
]


def pack(names):
    return [{"key": i, "rel": f"{ROOT}/{n}", "size": 400_000_000} for i, n in enumerate(names)]


def episodes_of(result):
    """{file name: (entry id, episode)} from a plan."""
    out = {}
    for g in result["groups"]:
        for key, (sid, ep) in g["parts"].items():
            out[key] = (sid, ep)
    return out


class PackRulesTest(unittest.TestCase):
    def plan(self, names, use_ai=False):
        with mock.patch.object(packs, "entries", return_value=ENTRIES):
            return packs.plan({"mal_client_id": "cid"}, "20583", pack(names), use_ai=use_ai)

    def test_seasons_go_to_their_own_entries(self):
        names = [f"Season 1/[Anime Time] Haikyuu!! - {n:02d}{TAIL}" for n in (1, 25)]
        names += [f"Season 2/[Anime Time] Haikyuu!! S2 - {n:02d}{TAIL}" for n in (1, 25)]
        names += [f"Season 3/[Anime Time] Haikyuu!! S3 - {n:02d}{TAIL}" for n in (1, 10)]
        names += [f"Season 4/[Anime Time] Haikyuu!! S4 - {n:02d}{TAIL}" for n in (1, 13, 14, 25)]
        got = episodes_of(self.plan(names))
        self.assertEqual([got[i] for i in range(len(names))], [
            ("20583", 1), ("20583", 25), ("28891", 1), ("28891", 25), ("32935", 1), ("32935", 10),
            # Season 4 is two entries on MyAnimeList: 13 + 12.
            ("38883", 1), ("38883", 13), ("40776", 1), ("40776", 12)])

    def test_season_folders_with_the_shows_name(self):
        names = [f"Haikyuu!! (Season 2)/[Anime Time] Haikyuu!! Second Season - 05{TAIL}",
                 f"Haikyuu!! S04/[Anime Time] Haikyuu!! To the Top - 20{TAIL}",
                 f"Haikyuu!! 4th Season/[Anime Time] Haikyuu!! - 03{TAIL}"]
        got = episodes_of(self.plan(names))
        self.assertEqual(got[0], ("28891", 5))
        self.assertEqual(got[1], ("40776", 7))          # To the Top episode 20 = part 2 episode 7
        self.assertEqual(got[2], ("38883", 3))

    def test_episodes_numbered_from_the_start_of_the_show(self):
        names = [f"Season 2/[Anime Time] Haikyuu!! - {n:02d}{TAIL}" for n in (26, 50)]
        got = episodes_of(self.plan(names))
        self.assertEqual([got[0], got[1]], [("28891", 1), ("28891", 25)])

    def test_films_and_ovas_by_name(self):
        names = [f"Movies/[Anime Time] Haikyuu!! Movie 2 - Shousha to Haisha{TAIL}",
                 f"Movies/[Anime Time] Haikyuu!! The Dumpster Battle{TAIL}",
                 f"OVAs/[Anime Time] Haikyuu!! Lev Genzan!{TAIL}",
                 f"OADs/[Anime Time] Haikyuu!! Riku vs Kuu - 02{TAIL}",
                 f"Season 1/[Anime Time] Haikyuu!! - 01{TAIL}"]
        result = self.plan(names)
        got = episodes_of(result)
        self.assertEqual(got[0][0], "31087")
        self.assertEqual(got[1][0], "52742")
        self.assertEqual(got[2][0], "29243")
        self.assertEqual(got[3], ("36472", 2))
        self.assertEqual(got[4], ("20583", 1))
        self.assertEqual({g["kind"] for g in result["groups"]}, {"tv", "movie", "ova"})

    def test_extras_arent_episodes(self):
        names = [f"Season 1/Extras/[Anime Time] Haikyuu!! NCOP 01{TAIL}", f"Season 1/[Anime Time] Haikyuu!! - 01{TAIL}"]
        result = self.plan(names)
        extras = next(g for g in result["groups"] if g["kind"] == "extra")
        self.assertEqual(extras["parts"], {})

    def test_a_plain_season_pack_stays_on_its_title(self):
        names = [f"[Group] Haikyuu!! - {n:02d} [1080p].mkv" for n in (1, 2, 3)]
        got = episodes_of(self.plan(names))
        self.assertEqual(sorted(got.values()), [("20583", 1), ("20583", 2), ("20583", 3)])

    def test_pointing_a_group_elsewhere(self):
        names = [f"Season 4/[Anime Time] Haikyuu!! S4 - {n:02d}{TAIL}" for n in (1, 14)]
        result = self.plan(names)
        g = result["groups"][0]
        self.assertEqual(packs.regroup(g, ENTRIES, "28891", "20583"), {0: ["28891", 1], 1: ["28891", 14]})
        self.assertEqual(packs.regroup(g, ENTRIES, None, "20583"), {0: [None, None], 1: [None, None]})


class PackAITest(unittest.TestCase):
    def test_the_ais_matching_is_used_when_it_checks_out(self):
        names = [f"Season 4/[Anime Time] Haikyuu!! S4 - {n:02d}{TAIL}" for n in (1, 14)]
        names.append(f"Season 1/[Anime Time] Haikyuu!! - 01{TAIL}")
        index = {e["source_id"]: i for i, e in enumerate(ENTRIES)}
        reply = {"groups": [
            {"id": "tv:4", "parts": [{"entry": index["38883"], "from_ep": 1, "to_ep": 13, "entry_first_episode": 1},
                                     {"entry": index["40776"], "from_ep": 14, "to_ep": 25, "entry_first_episode": 1}],
             "why": "season 4 is To the Top and its part 2"},
            # An answer that doesn't fit the facts (Haikyuu!! has 25 episodes) is ignored.
            {"id": "tv:1", "parts": [{"entry": index["20583"], "from_ep": 1, "to_ep": 1, "entry_first_episode": 40}]}]}
        prompts = []
        with mock.patch.object(packs, "entries", return_value=ENTRIES), \
                mock.patch.object(ai, "available", return_value=["gemini"]), \
                mock.patch.object(ai, "ask", side_effect=lambda s, p, **kw: prompts.append(p) or (reply, "gemini")):
            result = packs.plan({"mal_client_id": "cid"}, "20583", pack(names))
        got = episodes_of(result)
        self.assertEqual([got[0], got[1], got[2]], [("38883", 1), ("40776", 1), ("20583", 1)])
        by_id = {g["id"]: g for g in result["groups"]}
        self.assertEqual((by_id["tv:4"]["by"], by_id["tv:1"]["by"]), ("ai", "rules"))
        self.assertIn("Gemini", result["by"])
        self.assertIn("To the Top Part 2", prompts[0])


class PackTorrentTest(test_server.ServerBase):
    """A franchise pack added to one title: each file goes to its own title."""

    H = "ab" * 20

    def setUp(self):
        super().setUp()
        for target in (mock.patch.object(packs, "entries", return_value=ENTRIES),
                       mock.patch.object(ai, "available", return_value=[])):
            target.start()
            self.addCleanup(target.stop)
        self.s1 = self.mal_item("20583")
        self.s2 = self.mal_item("28891")
        names = [f"Season 1/[Anime Time] Haikyuu!! - {n:02d}{TAIL}" for n in (1, 2)]
        names += [f"Haikyuu!! (Season 2)/[Anime Time] Haikyuu!! Second Season - {n:02d}{TAIL}" for n in (1, 5)]
        names += [f"Season 4/[Anime Time] Haikyuu!! S4 - {n:02d}{TAIL}" for n in (1, 14)]
        names += [f"Movies/[Anime Time] Haikyuu!! The Dumpster Battle{TAIL}", "Season 1/Haikyuu!! - 01.ass"]
        self.app.torrents.records[self.H] = {
            "hash": self.H, "item_id": self.s1["id"], "magnet": "magnet:?xt=urn:btih:" + self.H, "name": ROOT,
            "save_path": self.tmp.name, "added": 1, "paused": True, "priorities": {}, "overrides": {}, "finished": {},
            "files": [{"index": i, "path": f"{ROOT}/{n}", "size": 400_000_000} for i, n in enumerate(names)]}
        self.app.torrents.make_plan(self.H)

    def mal_item(self, sid):
        e = next(e for e in ENTRIES if e["source_id"] == sid)
        fmt = "movie" if e["kind"] == "movie" else "series"
        return self.app.store.add(new_item("anime", {
            "source": "mal", "source_id": sid, "title": e["title"], "format": fmt, "year": e["year"],
            "seasons": [] if fmt == "movie" else [{"number": 1, "episodes": e["episodes"]}]}))

    def episodes(self, item):
        return {k: v["index"] for k, v in self.app.torrents.episodes(self.app.store.get(item["id"])).items()}

    def test_each_season_on_its_own_title(self):
        self.assertEqual(self.app.torrents.records[self.H]["plan"]["status"], "ready")
        self.assertEqual(self.episodes(self.s1), {"1:1": 0, "1:2": 1})
        self.assertEqual(self.episodes(self.s2), {"1:1": 2, "1:5": 3})
        # Titles not in the list yet: their files wait for them.
        view = self.app.torrents.plan_view(self.H)
        self.assertEqual(view["missing"], ["38883", "40776", "52742"])
        # The Torrents window shows the pack under both titles, each file with its title.
        self.assertEqual(len(self.app.torrents.all(self.s2["id"])["torrents"]), 1)
        files = {f["index"]: f for f in self.app.torrents.view(self.H)["files"]}
        self.assertEqual((files[3]["item_title"], files[3]["episode"]), ("Haikyuu!! Second Season", "1:5"))
        self.assertIsNone(files[4]["item_id"])

    def test_adding_the_missing_titles(self):
        def details(settings, type_, source, sid):
            e = next(e for e in ENTRIES if e["source_id"] == sid)
            fmt = "movie" if e["kind"] == "movie" else "series"
            return {"source": "mal", "source_id": sid, "title": e["title"], "format": fmt, "year": e["year"],
                    "seasons": [] if fmt == "movie" else [{"number": 1, "episodes": e["episodes"]}]}
        with mock.patch.object(metadata, "details", side_effect=details):
            status, view = self.call("POST", f"/api/torrents/{self.H}/plan/add", {})
        self.assertEqual(status, 200, view)
        self.assertEqual((view["added"], view["missing"]), (3, []))
        top2 = self.app.store.find_source("mal", "40776")
        self.assertEqual(self.episodes(top2), {"1:1": 5})          # season 4 episode 14
        film = self.app.store.find_source("mal", "52742")
        info = self.app.local_info(film, None, None)
        self.assertTrue(info["exists"], info)
        # Adding the same magnet on another of its titles is fine.
        self.assertEqual(self.app.torrents._owns_any(self.app.torrents.records[self.H], top2["id"]), True)

    def test_changing_a_group_and_a_file(self):
        status, view = self.call("POST", f"/api/torrents/{self.H}/plan", {"group": "tv:2", "source_id": None})
        self.assertEqual(status, 200, view)
        self.assertEqual(self.episodes(self.s2), {})
        status, view = self.call("POST", f"/api/torrents/{self.H}/plan", {"group": "tv:2", "source_id": "28891"})
        self.assertEqual(self.episodes(self.s2), {"1:1": 2, "1:5": 3})
        g = next(g for g in view["groups"] if g["id"] == "tv:2")
        self.assertEqual((g["by"], g["targets"][0]["item_id"]), ("you", self.s2["id"]))
        # One file by hand, onto another title.
        status, _ = self.call("POST", f"/api/torrents/{self.H}/files/1", {"episode": "1:7", "item_id": self.s2["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.episodes(self.s2)["1:7"], 1)
        self.assertNotIn("1:2", self.episodes(self.s1))
        self.assertTrue(self.app.torrents.owns(self.s2["id"], self.app.torrents.stream_url(self.H, 1, "x.mkv")))

    def test_removing_the_title_it_was_added_to_keeps_it_for_the_others(self):
        self.call("DELETE", f"/api/items/{self.s1['id']}")
        self.assertIn(self.H, self.app.torrents.records)
        self.assertEqual(self.app.torrents.records[self.H]["item_id"], self.s2["id"])
        self.call("DELETE", f"/api/items/{self.s2['id']}")
        self.assertNotIn(self.H, self.app.torrents.records)


class PackDownloadsTest(unittest.TestCase):
    """Sort now with a whole franchise in one folder of your downloads."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from tests.test_scanner import touch
        from watchlist.server import App
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.dl = root / "Downloads"
        self.dl.mkdir()
        self.app = App(root / "data")
        self.app.settings.update({"mal_client_id": "cid", "downloads_folder": str(self.dl)})
        names = [f"Season 1/[Anime Time] Haikyuu!! - {n:02d}{TAIL}" for n in (1, 2)]
        names += [f"Haikyuu!! (Season 2)/[Anime Time] Haikyuu!! Second Season - {n:02d}{TAIL}" for n in (1, 5)]
        names += [f"Season 4/[Anime Time] Haikyuu!! S4 - {n:02d}{TAIL}" for n in (1, 14)]
        names += [f"Movies/[Anime Time] Haikyuu!! The Dumpster Battle{TAIL}"]
        for n in names:
            touch(self.dl, f"{ROOT}/{n}")

        def details(settings, type_, source, sid):
            e = next(e for e in ENTRIES if e["source_id"] == sid)
            fmt = "movie" if e["kind"] == "movie" else "series"
            return {"source": "mal", "source_id": sid, "title": e["title"], "format": fmt, "year": e["year"],
                    "aliases": e["names"], "seasons": [] if fmt == "movie" else [{"number": 1, "episodes": e["episodes"]}]}
        self.searched = []

        def search(mal, query):
            self.searched.append(query)
            return [{"source_id": e["source_id"], "title": e["title"], "aliases": e["names"], "year": e["year"],
                     "date": e["date"], "media_kind": e["kind"], "media_type": e["media_type"]}
                    for e in ENTRIES if e["kind"] == "tv"]
        for target in (mock.patch.object(packs, "entries", return_value=ENTRIES),
                       mock.patch.object(ai, "available", return_value=[]),
                       mock.patch.object(metadata, "details", side_effect=details),
                       mock.patch.object(metadata, "search", return_value=[]),
                       mock.patch.object(metadata, "related", return_value=[]),
                       mock.patch.object(metadata.MAL, "search", search)):
            target.start()
            self.addCleanup(target.stop)

    def episodes(self, sid):
        item = self.app.store.find_source("mal", sid)
        self.assertIsNotNone(item, sid)
        return self.app.library.state()["items"].get(item["id"], {}).get("episodes")

    def test_each_part_goes_to_its_own_title(self):
        self.app.sorter.run_now()
        self.assertEqual(self.searched, ["haikyuu"])
        self.assertEqual(self.episodes("20583"), ["1:1", "1:2"])
        self.assertEqual(self.episodes("28891"), ["1:1", "1:5"])
        self.assertEqual(self.episodes("38883"), ["1:1"])
        self.assertEqual(self.episodes("40776"), ["1:1"])           # season 4 episode 14
        film = self.app.store.find_source("mal", "52742")
        self.assertEqual(len(self.app.library.videos_for(film["id"])), 1)
        auto = self.app.library.state()["auto"]
        self.assertTrue(all(a["key"].startswith("pack:") for a in auto), auto)
        # Worked out once: the next Sort now leaves it alone.
        self.app.sorter.run_now()
        self.assertEqual(self.searched, ["haikyuu"])

    def test_corrects_what_was_sorted_by_name_before(self):
        from tests.test_library import add
        s1 = add(self.app, "anime", "Haikyuu!!", source_id="20583", seasons=[{"number": 1, "episodes": 25}])
        lib = self.app.library
        lib.scan(force=True)
        # An older Sort now put every "Haikyuu!! - NN" in Haikyuu!! itself; one file you placed yourself.
        rels = [v["rel"] for v in lib.videos]
        lib.assign_many([{"key": "old", "files": rels, "item": s1["id"], "by": "rules"}])
        mine = next(r for r in rels if "S4 - 01" in r)
        lib.route(mine, s1["id"], 1, 20)
        self.app.sorter.run_now()
        self.assertEqual(self.searched, [])                           # known from what's sorted already
        self.assertEqual(self.episodes("20583"), ["1:1", "1:2", "1:20"])
        self.assertEqual(self.episodes("28891"), ["1:1", "1:5"])
