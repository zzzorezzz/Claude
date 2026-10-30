"""Round 11: any site (anime, TV shows, films) by its TMDB id and its addresses, torrent
sites read right, the full and simple versions."""
import json
import tempfile
import unittest
from unittest import mock

from watchlist import scrape, sites
from watchlist.config import Settings


def tv(title, sid, year, seasons=8):
    return {"id": sid, "title": title, "type": "tv", "source": "tmdb", "source_id": sid, "format": "series",
            "year": year, "seasons": [{"number": n, "episodes": 10} for n in range(1, seasons + 1)]}


class NamesTest(unittest.TestCase):
    def test_shorter_names_are_searched_too(self):
        got = scrape.name_variants(["Chainsaw Man - The Movie: Reze Arc"])
        self.assertIn("Chainsaw Man The Movie: Reze Arc", got)
        self.assertIn("Chainsaw Man", got)
        self.assertIn("Reze Arc", got)
        self.assertIn("El Camino", scrape.name_variants(["El Camino: A Breaking Bad Movie"]))

    def test_a_result_with_the_titles_tmdb_id_is_the_title(self):
        scraper = scrape.Scraper(tempfile.mkdtemp())
        [site] = scrape.clean([{"name": "Atlantic", "url": "https://atlantic.test/search?q={title}"}])
        html = ('<a href="/title/movie/1720740">PARASITE 2025</a>'
                '<a href="/title/movie/496243">Parasite 2019</a><a href="/title/movie/1234">Parasite 1982</a>')
        item = {"id": "p", "title": "Parasite", "type": "movie", "source": "tmdb", "source_id": "movie/496243",
                "format": "movie", "year": 2019}
        with mock.patch.object(scrape, "fetch", return_value=(html, "https://atlantic.test/search?q=Parasite")):
            found = scraper.search(site, item)
        self.assertEqual(found[0]["url"], "https://atlantic.test/title/movie/496243")

    def test_the_release_year_settles_a_tie(self):
        page = scrape.parse('<a href="/movie/1-parasite-1982">Parasite 1982</a><a href="/movie/2-parasite-2019">Parasite 2019</a>',
                            "https://site.test/search")
        ranked = scrape.best_entry(scrape.candidates(page, "https://site.test/search"),
                                   {"names": ["Parasite"], "season": 1, "kind": "movie", "year": 2019, "part": None})
        self.assertIn("2019", ranked[0]["url"])


class EpisodeAddressesTest(unittest.TestCase):
    def test_season_and_episode_in_the_address(self):
        html = "".join(f'<a href="/watch/tv/1396/1/{n}">{n} Pilot</a>' for n in range(1, 8))
        eps = scrape.episode_links(scrape.parse(html, "https://bingr.test/tv/1396"))
        self.assertEqual(eps[(1, 3)], "https://bingr.test/watch/tv/1396/1/3")
        self.assertEqual(scrape.derive_episode(eps, 4, 4), "https://bingr.test/watch/tv/1396/4/4")
        html = "".join(f'<a href="/watch/1396?s=1&e={n}">Episode {n}</a>' for n in range(1, 4))
        eps = scrape.episode_links(scrape.parse(html, "https://shuttle.test/watch/1396"))
        self.assertEqual(scrape.derive_episode(eps, 4, 4), "https://shuttle.test/watch/1396?s=4&e=4")

    def test_a_tmdb_tv_page_that_plays_itself_gets_s_and_e(self):
        self.assertEqual(scrape._with_se("https://shuttle.test/watch/1399", 4, 4), "https://shuttle.test/watch/1399?s=4&e=4")
        self.assertEqual(scrape.tmdb_id(tv("Game of Thrones", "tv/1399", 2011)), ("tv", "1399"))
        self.assertEqual(scrape.tmdb_id({"source": "mal", "source_id": "1535"}), (None, None))

    def test_watch_parties_and_history_arent_watch_links(self):
        page = scrape.parse('<a href="/parties">Watch Parties</a><a href="/history">Watch History</a><a href="/watch/1">Watch now</a>',
                            "https://atlantic.test/title/tv/1")
        self.assertEqual(scrape.watch_links(page, "https://atlantic.test/title/tv/1"), ["https://atlantic.test/watch/1"])

    def test_a_signed_playlist_inside_another_address_is_read_whole(self):
        html = '<script>src="https://x.workers.dev/?url=https%3A%2F%2Fcdn.test%2Fmaster.m3u8&Policy=abc&Signature=def"</script>'
        found = scrape.videos_in(scrape.parse(html, "https://bingr.test/watch/movie/1"), html)
        self.assertTrue(found and found[0].endswith("Signature=def"), found)


class TorrentSitesTest(unittest.TestCase):
    def test_the_pirate_bay_is_read_from_its_json(self):
        site = {"name": "TPB", "url": "https://thepiratebay.org/search.php?q={title}&all=on"}
        self.assertTrue(sites.search_url(site, "Breaking Bad").startswith("https://apibay.org/q.php?q=Breaking+Bad"))
        data = json.dumps([{"name": "Breaking.Bad.S01.1080p", "info_hash": "6BBAD062" + "0" * 31 + "1", "seeders": "492",
                            "leechers": "20", "size": "1000"},
                           {"name": "No results returned", "info_hash": "0" * 40, "seeders": "0", "leechers": "0", "size": "0"}]).encode()
        rows = sites.results(data, "https://apibay.org/q.php?q=x")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["seeders"], rows[0]["size"]), (492, 1000))
        self.assertTrue(rows[0]["magnet"].startswith("magnet:?xt=urn:btih:6bbad062"))

    def test_a_name_cell_over_two_columns_keeps_the_seeders_in_place(self):
        html = b"""<table><tr><th>Category</th><th>Name</th><th>Comments</th><th>Link</th><th>Size</th><th>Date</th>
          <th>Seeders</th><th>Leechers</th><th>Downloads</th></tr>
          <tr><td>Anime</td><td colspan="2"><a href="/view/1">Death Note 01-37</a></td>
          <td><a href="magnet:?xt=urn:btih:d06f8833aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">m</a></td><td>12.3 GiB</td><td>2024</td>
          <td>304</td><td>12</td><td>9000</td></tr></table>"""
        [row] = sites.results(html, "https://nyaa.test/")
        self.assertEqual(row["seeders"], 304)


class ModeTest(unittest.TestCase):
    def test_full_or_simple_and_old_settings_are_full(self):
        with tempfile.TemporaryDirectory() as d:
            from pathlib import Path
            s = Settings(Path(d) / "settings.json")
            self.assertEqual(s.get()["mode"], "")
            self.assertEqual(s.update({"mode": "simple"})["mode"], "simple")
            with self.assertRaises(ValueError):
                s.update({"mode": "tiny"})
            self.assertFalse(s.get()["skip_intro"])


if __name__ == "__main__":
    unittest.main()
