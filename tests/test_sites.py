"""Get magnet: your own torrent site, searched and read, the best torrent found."""

import hashlib
import http.server
import threading
import time
import unittest
import urllib.parse
from unittest import mock

from tests import test_server
from tests.test_packs import ENTRIES
from watchlist import ai, packs, sites, torrents
from watchlist.store import new_item

TRACKER = "udp://tracker.example.org:1337/announce"


def h(name):
    return hashlib.sha1(name.encode()).hexdigest()


def eps(prefix, numbers, folder=""):
    return [{"index": i, "path": f"{folder}{prefix} - {n:02d} [1080p].mkv", "size": 1_000_000_000}
            for i, n in enumerate(numbers)]


PACK = "[Anime Time] Haikyuu!! (S01+02+03+04+Movies+OVAs+OADs) [BD] [Dual Audio][1080p][HEVC 10bit x265]"
BATCH = "[Judas] Haikyuu!! Second Season (Season 2) [BD 1080p][HEVC x265][Batch]"
SHORT = "[Erai-raws] Haikyuu!! Second Season [720p] (01-25)"
ONE = "[SubsPlease] Haikyuu!! Second Season - 05 (1080p)"
DEAD = "[Old] Haikyuu!! S2 [480p] 01-25"
OTHER = "[Group] Naruto Shippuuden (001-500) [1080p]"
TOP = "[Judas] Haikyuu!! To the Top (01-13) [BD 1080p][Batch]"

FILES = {
    h(PACK): (eps(f"{PACK}/Season 1/[Anime Time] Haikyuu!!", range(1, 26))
              + eps(f"{PACK}/Haikyuu!! (Season 2)/[Anime Time] Haikyuu!! Second Season", range(1, 26))
              + [{"index": 99, "path": f"{PACK}/Movies/[Anime Time] Haikyuu!! The Dumpster Battle [1080p].mkv",
                  "size": 3_000_000_000}]),
    h(BATCH): eps("[Judas] Haikyuu!! Second Season/[Judas] Haikyuu!! S2", range(1, 26)),
    h(SHORT): eps("[Erai-raws] Haikyuu!! Second Season/[Erai-raws] Haikyuu!! Second Season", range(1, 21)),
    h(DEAD): eps("[Old] Haikyuu!! S2/[Old] Haikyuu!! S2", range(1, 26)),
    h(TOP): eps("[Judas] Haikyuu!! To the Top/[Judas] Haikyuu!! To the Top", range(1, 14)),
}


class FakeSite(http.server.BaseHTTPRequestHandler):
    """A torrent site: a results table without magnets; each result's page shows
    its info hash as text (its magnet button only works with JavaScript), or
    offers a .torrent file."""
    rows = [(PACK, "12.4 GiB", 150), (ONE, "1.4 GiB", 900), (BATCH, "18.1 GiB", 60),
            (SHORT, "6.2 GiB", 400), (DEAD, "3.0 GiB", 0), (OTHER, "300 GiB", 80), (TOP, "9 GiB", 500)]
    torrent_file = None       # (name, bytes) of a result offered only as a .torrent file
    searches = []
    match = "haikyu"          # what a search has to have in it to find anything

    def log_message(self, *a):
        pass

    def send(self, body, ctype="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/search":
            q = urllib.parse.parse_qs(url.query).get("q", [""])[0]
            self.searches.append(q)
            rows = [r for r in self.rows if self.match in q.lower()] or []
            if self.torrent_file and self.match in q.lower():
                rows = rows + [(self.torrent_file[0], "9 GiB", 70)]
            body = "".join(
                f'<tr><td><a href="/c/1_2" title="Anime - English-translated"><img alt="Anime"></a></td>'
                f'<td><a href="/view/{i}#comments" class="comments">3</a><a href="/view/{i}" title="{name}">{name}</a></td>'
                f'<td class="text-center"><a href="#"><i class="fa fa-magnet"></i></a></td>'
                f"<td>{size}</td><td>2024-01-0{1 + i % 8} 10:00</td><td>{seeds}</td><td>3</td><td>1200</td></tr>"
                for i, (name, size, seeds) in enumerate(rows))
            self.send(f"""<html><head><title>Search</title><script>var magnets = {{}};</script></head><body>
                <table class="torrent-list"><thead><tr><th>Category</th><th>Name</th><th>Link</th><th>Size</th>
                <th>Date</th><th title="Seeders"><i class="fa fa-arrow-up"></i></th><th title="Leechers">L</th>
                <th title="Completed downloads">C</th></tr></thead><tbody>{body}</tbody></table></body></html>""")
        elif url.path.startswith("/view/"):
            i = int(url.path.split("/")[2])
            rows = self.rows + ([(self.torrent_file[0], "", 0)] if self.torrent_file else [])
            name = rows[i][0]
            if self.torrent_file and name == self.torrent_file[0]:
                self.send(f'<html><head><title>{name}</title></head><body><h3>{name}</h3>'
                          f'<a href="/download/{i}.torrent">Download torrent</a></body></html>')
                return
            self.send(f"""<html><head><title>{name} :: My Site</title></head><body>
                <h3 class="panel-title">{name}</h3>
                <div class="row"><div class="col-md-1">Info hash:</div><div class="col-md-5"><kbd>{h(name)}</kbd></div></div>
                <button onclick="openMagnet()">Magnet</button>
                <div>Trackers: {TRACKER}</div></body></html>""")
        elif url.path.startswith("/download/") and self.torrent_file:
            self.send(self.torrent_file[1], "application/x-bittorrent")
        else:
            self.send_response(404)
            self.end_headers()


class FakePeeker:
    """The people sharing each torrent answer with its files (or don't)."""

    def __init__(self, test):
        self.test, self.asked = test, []

    def add(self, magnet):
        self.asked.append(magnet)
        self.test.peeked.append([magnet])
        return sites._BTIH.search(magnet).group(1).lower()

    def poll(self):
        out = {}
        for m in self.asked:
            hh = sites._BTIH.search(m).group(1).lower()
            if hh in FILES:
                out[hh] = {"files": FILES[hh], "seeds": None, "name": ""}
        return out

    def close(self):
        pass


class SiteBase(test_server.ServerBase):
    def setUp(self):
        super().setUp()
        FakeSite.searches = []
        FakeSite.torrent_file = None
        self.site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeSite)
        threading.Thread(target=self.site.serve_forever, daemon=True).start()
        self.addCleanup(self.site.server_close)
        self.addCleanup(self.site.shutdown)
        self.base = f"http://127.0.0.1:{self.site.server_address[1]}"
        self.app.settings.update({"torrent_sites": [{"name": "My site", "url": self.base + "/search?q={title}&c=1_2"}]})
        self.peeked = []
        for target in (mock.patch.object(packs, "entries", return_value=ENTRIES),
                       mock.patch.object(ai, "available", return_value=[]),
                       mock.patch.object(torrents, "lt", torrents.lt),
                       mock.patch.object(self.app.torrents, "peeker", side_effect=lambda: FakePeeker(self)),
                       mock.patch.object(sites, "PEEK_WAIT", 1.5),
                       mock.patch.object(self.app.torrents, "add", side_effect=self.add_torrent)):
            target.start()
            self.addCleanup(target.stop)
        self.added = []
        self.item = self.app.store.add(new_item("anime", {
            "source": "mal", "source_id": "28891", "title": "Haikyuu!! Second Season",
            "original_title": "Haikyuu!! Second Season", "aliases": ["Haikyu!! 2nd Season"], "format": "series",
            "year": 2015, "episodes_total": 25, "seasons": [{"number": 1, "episodes": 25}]}))

    def add_torrent(self, item_id, magnet):
        self.added.append((item_id, magnet))
        return {"hash": sites._BTIH.search(magnet).group(1).lower()}

    def find(self):
        status, state = self.call("POST", f"/api/items/{self.item['id']}/find-torrent")
        self.assertEqual(status, 200, state)
        end = time.time() + 20
        while state["running"] and time.time() < end:
            time.sleep(0.1)
            _, state = self.call("GET", f"/api/items/{self.item['id']}/find-torrent")
        self.assertFalse(state["running"])
        return state


class ReadingTest(SiteBase):
    def test_reading_the_results_page(self):
        r = sites.test_site(self.app.settings.get()["torrent_sites"][0], "Haikyuu")
        self.assertEqual(r["count"], 7)
        names = [x["name"] for x in r["examples"]]
        self.assertEqual(names[0], PACK)
        self.assertEqual([x["seeders"] for x in r["examples"]], [150, 900, 60, 400, 0, 80, 500])
        self.assertEqual(r["examples"][2]["size"], int(18.1 * 1024 ** 3))
        self.assertEqual(r["with_magnet"], 0)          # the magnets are on each result's page

    def test_a_results_page_page(self):
        data, final, _ = sites.fetch(self.base + "/view/2")
        d = sites.details(data, final)
        self.assertEqual(d["hash"], h(BATCH))
        self.assertEqual(d["trackers"], [TRACKER])
        magnet = sites.make_magnet(d["hash"], BATCH, d["trackers"])
        self.assertTrue(magnet.startswith(f"magnet:?xt=urn:btih:{h(BATCH)}&dn="))
        self.assertIn("&tr=udp%3A%2F%2Ftracker.example.org%3A1337%2Fannounce", magnet)

    def test_a_list_of_magnet_links(self):
        page = (f'<ul><li><a href="magnet:?xt=urn:btih:{h("x")}&dn=Frieren+S01+1080p">Frieren S01 1080p</a> 4.2 GB</li>'
                '<li><a href="magnet:?xt=urn:btih:CIYSLLZ3ZUPGCXHUWDJQHUAEPMZMT6RX&dn=Other">Other one here</a></li></ul>')
        rows = sites.results(page.encode(), "http://x/")
        self.assertEqual([r["name"] for r in rows], ["Frieren S01 1080p", "Other one here"])
        self.assertEqual(rows[0]["hash"], h("x"))
        self.assertEqual(len(rows[1]["hash"]), 40)

    def test_what_names_say(self):
        self.assertTrue(sites.describe_release(PACK)["pack"])
        self.assertEqual(sites.describe_release(SHORT)["range"], (1, 25))
        self.assertEqual(sites.describe_release(ONE)["episode"], 5)
        self.assertIsNone(sites.describe_release(BATCH)["episode"])
        self.assertEqual(sites.describe_release(BATCH)["resolution"], 1080)
        self.assertTrue(sites.describe_release(BATCH)["bd"])

    def test_settings_check_the_link(self):
        status, data = self.call("PUT", "/api/settings", {"torrent_sites": [{"name": "x", "url": "https://x/search"}]})
        self.assertEqual(status, 400, data)
        self.assertIn("{title}", data["error"])
        status, data = self.call("POST", "/api/sites/test", {"site": {"name": "Mine", "url": self.base + "/search?q={title}"},
                                                             "query": "Haikyuu"})
        self.assertEqual((status, data["count"]), (200, 7))


class FindingTest(SiteBase):
    def test_the_best_ones_are_checked_and_offered(self):
        state = self.find()
        self.assertEqual(state["error"], "", state["log"])
        names = [c["name"] for c in state["choices"]]
        self.assertNotIn(ONE, names)                   # one episode isn't what's needed
        self.assertNotIn(OTHER, names)                 # another anime
        by_name = {c["name"]: c for c in state["choices"]}
        self.assertTrue(by_name[PACK]["complete"])
        self.assertEqual((by_name[PACK]["has"], by_name[PACK]["wanted"], by_name[PACK]["others"]), (25, 25, 2))
        self.assertTrue(by_name[BATCH]["complete"])
        self.assertEqual((by_name[SHORT]["has"], by_name[SHORT]["complete"]), (20, False))
        # Complete ones first, whatever their seeders; one nobody shares last.
        self.assertEqual(set(names[:2]), {PACK, BATCH})
        self.assertLess(by_name[SHORT]["score"], by_name[BATCH]["score"])
        self.assertEqual(by_name[TOP]["has"], 0)       # To the Top: another title of the series
        self.assertEqual(set(names[-2:]), {DEAD, TOP})
        # Searched by its own name and by the series' name (for packs).
        self.assertIn("haikyuu", [q.lower() for q in FakeSite.searches])
        # Two as good as each other: you choose (nothing added yet).
        if state["added"] is None:
            status, state = self.call("POST", f"/api/items/{self.item['id']}/find-torrent/use", {"key": h(BATCH)})
            self.assertEqual(status, 200, state)
        item_id, magnet = self.added[0]
        self.assertEqual(item_id, self.item["id"])
        self.assertIn("urn:btih:", magnet)
        self.assertIn("tracker.example.org", urllib.parse.unquote(magnet))

    def test_a_clear_winner_is_added(self):
        self.addCleanup(setattr, FakeSite, "rows", FakeSite.rows)
        FakeSite.rows = [(BATCH, "18.1 GiB", 60), (SHORT, "6.2 GiB", 400), (ONE, "1.4 GiB", 900)]
        state = self.find()
        self.assertEqual(state["added"]["hash"], h(BATCH), state)
        self.assertEqual(len(self.added), 1)

    def test_nothing_found(self):
        self.app.store.update(self.item["id"], {"title": "Frieren", "original_title": "Sousou no Frieren", "aliases": []})
        with mock.patch.object(packs, "entries", return_value=[]):
            state = self.find()
        self.assertIn("nothing on your site matched", state["error"])

    def test_no_site(self):
        self.app.settings.update({"torrent_sites": []})
        state = self.find()
        self.assertIn("add a torrent site for anime in Settings", state["error"])


@unittest.skipUnless(torrents.lt, "libtorrent isn't installed")
class TorrentFileTest(SiteBase):
    def test_a_torrent_file_gives_the_hash_and_files_at_once(self):
        import os
        import tempfile
        from pathlib import Path
        lt = torrents.lt
        name = "[Kaizoku] Haikyuu!! Second Season [BD 1080p] (Batch)"
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / name
            folder.mkdir()
            for n in range(1, 26):
                (folder / f"[Kaizoku] Haikyuu!! S2 - {n:02d} [1080p].mkv").write_bytes(os.urandom(64))
            fs = lt.file_storage()
            lt.add_files(fs, str(folder))
            t = lt.create_torrent(fs, 16384)
            t.add_tracker(TRACKER)
            lt.set_piece_hashes(t, d)
            data = lt.bencode(t.generate())
        info = sites.torrent_file(data)
        self.assertEqual(len(info["files"]), 25)
        self.assertEqual(info["trackers"], [TRACKER])
        self.addCleanup(setattr, FakeSite, "rows", FakeSite.rows)
        FakeSite.rows = []
        FakeSite.torrent_file = (name, data)
        state = self.find()
        self.assertEqual(state["error"], "", state["log"])
        [choice] = state["choices"]
        self.assertTrue(choice["complete"])
        self.assertEqual(choice["key"], info["hash"])
        self.assertEqual(self.peeked, [])               # its files came with it
        self.assertEqual(state["added"]["hash"], info["hash"])



SHOW = "Breaking.Bad.S01.1080p.BluRay.x264-GRP"
SHOW2 = "Breaking.Bad.S02.720p.WEB-DL"
SHOW_ALL = "Breaking Bad (2008) Complete Series S01-S05 1080p BluRay"
SHOW_ONE = "Breaking.Bad.S01E03.1080p.WEB"
FILM = "Inception.2010.1080p.BluRay.x264-GRP"
FILM_SMALL = "Inception.2010.Trailer.1080p"


def tv_eps(prefix, season, numbers):
    return [{"index": i, "path": f"{prefix}/{prefix}.S{season:02d}E{n:02d}.mkv", "size": 900_000_000}
            for i, n in enumerate(numbers)]


FILES.update({
    h(SHOW): tv_eps("Breaking.Bad.S01", 1, range(1, 8)),
    h(SHOW2): tv_eps("Breaking.Bad.S02", 2, range(1, 14)),
    h(SHOW_ALL): tv_eps("BB", 1, range(1, 8)) + tv_eps("BB", 2, range(1, 14)),
    h(FILM): [{"index": 0, "path": f"{FILM}/{FILM}.mkv", "size": 9_000_000_000},
              {"index": 1, "path": f"{FILM}/Sample/sample.mkv", "size": 40_000_000}],
    h(FILM_SMALL): [{"index": 0, "path": f"{FILM_SMALL}.mp4", "size": 30_000_000}],
})


class TvAndFilmTest(SiteBase):
    """Your site for TV shows and films too: the season you're on, or the film."""

    def setUp(self):
        super().setUp()
        self.app.settings.update({"torrent_sites": [{"name": "Mine", "url": self.base + "/search?q={title}",
                                                     "types": ["tv", "movie"]}]})
        self.addCleanup(setattr, FakeSite, "rows", FakeSite.rows)
        self.addCleanup(setattr, FakeSite, "match", FakeSite.match)

    def test_a_tv_show_wants_the_season_youre_on(self):
        FakeSite.rows = [(SHOW2, "8 GiB", 300), (SHOW, "12 GiB", 80), (SHOW_ONE, "1 GiB", 900), (SHOW_ALL, "60 GiB", 40)]
        FakeSite.match = "breaking"
        self.item = self.app.store.add(new_item("tv", {
            "source": "tmdb", "source_id": "tv/1396", "title": "Breaking Bad", "format": "series", "year": 2008,
            "seasons": [{"number": 1, "episodes": 7}, {"number": 2, "episodes": 13}]}))
        state = self.find()
        self.assertEqual(state["error"], "", state["log"])
        by = {c["name"]: c for c in state["choices"]}
        self.assertNotIn(SHOW_ONE, by)                          # one episode
        self.assertEqual((by[SHOW]["has"], by[SHOW]["wanted"], by[SHOW]["complete"]), (7, 7, True))
        self.assertEqual((by[SHOW_ALL]["has"], by[SHOW_ALL]["seasons"]), (7, [1, 2]))
        self.assertEqual(by[SHOW2]["has"], 0)                   # season 2 isn't the one you're on
        self.assertIn("Breaking Bad S01", FakeSite.searches)

    def test_a_film(self):
        FakeSite.rows = [(FILM_SMALL, "30 MiB", 500), (FILM, "9 GiB", 120)]
        FakeSite.match = "inception"
        self.item = self.app.store.add(new_item("movie", {
            "source": "tmdb", "source_id": "movie/27205", "title": "Inception", "format": "movie", "year": 2010}))
        state = self.find()
        self.assertEqual(state["error"], "", state["log"])
        self.assertEqual(state["added"]["hash"], h(FILM), state["choices"])
        self.assertIn("Inception 2010", FakeSite.searches)        # (searched at once with the others: any order)

    def test_sites_only_for_their_kinds(self):
        self.item = self.app.store.get(self.item["id"])          # an anime: this site is for TV and films
        state = self.find()
        self.assertIn("add a torrent site for anime", state["error"])
