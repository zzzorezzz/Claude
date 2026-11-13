"""Round 15 (1.3.9): playing from your sites - episodes that are buttons each their own video, a
poster with no name, the site and version you moved to kept for the next episodes, a server chosen
while the next episode was being found ahead, a slow play that doesn't take over a later one, the
look-ahead stopping after the last episode out, hidden pages that couldn't be set up closed."""
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

from watchlist import player, scrape, webplay
from watchlist.server import App
from watchlist.store import new_item


def found_at(url, label=None, kind="sub", page="p"):
    return {"url": url, "page": page, "referer": "", "headers": {}, "streams": [], "entry": {"url": "e"}, "subtitle": "",
            "stream": {"id": label, "label": label, "kind": kind} if label else None}


class ButtonEpisodesTest(unittest.TestCase):
    def test_each_episode_thats_a_button_gets_its_own_video(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        page = "http://127.0.0.1:9/show/abc"
        site = {"id": "s1", "name": "Fake", "url": "http://127.0.0.1:9/search?q={title}"}
        item = {"id": "it1", "title": "Show", "format": "series", "type": "anime"}
        s.episode_page = lambda site, item, season, episode, pin="": (
            f"{page}{scrape.CLICK_MARK}{urllib.parse.quote(f'Episode {episode}')}", {"url": page, "name": "Show"}, [])
        pressed = []

        def catch(target, click=None):
            pressed.append((click or {}).get("label"))
            return {"url": f"http://cdn.example/{(click or {}).get('label', 'none').replace(' ', '_')}.m3u8",
                    "headers": {"Referer": page}}
        s.catch = catch
        with mock.patch.multiple(scrape, scrambled_playlist=lambda *a, **k: False, scrambled_video=lambda *a, **k: False,
                                 refused=lambda *a, **k: False, hls_length=lambda *a, **k: None,
                                 master_playlist=lambda url, *a, **k: url, qualities=lambda *a, **k: [],
                                 pick_subtitle=lambda *a, **k: ""), \
                mock.patch.object(scrape.Scraper, "_labelled_streams", staticmethod(lambda u: False)):
            one, two = s.resolve(site, item, 1, 1), s.resolve(site, item, 1, 2)
        self.assertEqual((one["url"], two["url"]), ("http://cdn.example/Episode_1.m3u8", "http://cdn.example/Episode_2.m3u8"))
        self.assertEqual(pressed, ["Episode 1", "Episode 2"])


class NoNameCardTest(unittest.TestCase):
    def test_a_poster_with_no_name_doesnt_break_the_search(self):
        html = ('<a href="/anime/123456"><img alt="No Image"></a><a href="/anime/123456">No Image</a>'
                '<a href="/anime/frieren-beyond-journeys-end">Frieren: Beyond Journey\'s End</a>')
        got = scrape.candidates(scrape.parse(html, "https://site.test/search?q=frieren"), "https://site.test/search?q=frieren")
        self.assertEqual([c["url"] for c in got], ["https://site.test/anime/frieren-beyond-journeys-end"])


class OnlineBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = App(tmp.name)
        self.addCleanup(self.app.torrents.shutdown)
        # (never a real hidden browser: the look-ahead would open one on the real sites)
        self.app.scraper.can_render = self.app.scraper.can_catch = lambda: False

    def sites(self, *names, kind="tv"):
        self.app.settings.update({"video_sites": [{"name": n, "url": f"https://{n.lower()}.test/s?q={{title}}", "types": [kind]}
                                                  for n in names]})
        return self.app.settings.get()["video_sites"]

    def show(self, title="Show", kind="tv", episodes=3):
        return self.app.store.add(dict(new_item(kind, {"source": "tmdb" if kind == "tv" else "mal", "source_id": f"x/{title}",
                                                       "title": title, "seasons": [{"number": 1, "episodes": episodes}]}),
                                       format="series"))

    def session(self, item, site, found):
        self.app._online_found[(item["id"], "1:1")] = (site, found, time.time())
        entry = dict(self.app._web_entry(item, site, "1:1", found), online=True)
        entries, first = self.app._online_playlist(item, site, entry)
        s = webplay.WebSession("dev", item["id"], entries)
        s.swap, s.send = mock.Mock(), mock.Mock()
        return s, entries


class MovedOnTest(OnlineBase):
    def test_after_another_site_the_next_episodes_come_from_it_and_in_the_same_version(self):
        a, b = self.sites("A", "B")
        item = self.show()
        calls = []

        def resolve(site, it, season, episode, track="", **kw):
            calls.append((site["name"], episode, track))
            return found_at(f"https://cdn.{site['name']}/{episode}-{track or 'sub'}.m3u8", "x", track or "sub")
        self.app.scraper.resolve = resolve
        self.app._link_around = lambda *a, **k: None
        session, entries = self.session(item, a, found_at("https://cdn.A/1-dub.m3u8", "x", "dub"))
        self.app._other_site(session)
        self.assertEqual(session.swap.call_args.args[0]["path"], "https://cdn.B/1-dub.m3u8")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(entries[1]["path"]).query))
        self.assertEqual(self.app.online_video(item["id"], 1, 2, q.get("site", ""), q.get("track", "")),
                         "https://cdn.B/2-sub.m3u8" if calls[-1][2] != "dub" else "https://cdn.B/2-dub.m3u8")
        self.assertEqual(calls[-1][0], "B")

    def test_a_server_chosen_while_the_next_episode_was_found_ahead(self):
        (a,) = self.sites("A", kind="anime")
        item = self.show(kind="anime")
        pref = {"label": "Bad"}
        self.app.scraper.prefer_stream = lambda site, label, kind: pref.update(label=label)

        def resolve(site, it, season, episode, stream="", **kw):
            label = stream.split(":", 2)[2] if stream else pref["label"]
            if episode == 2:
                time.sleep(0.6)
            return found_at(f"https://cdn/{label}/{episode}.m3u8", label, page=f"https://a/ep{episode}")
        self.app.scraper.resolve = resolve
        session, _ = self.session(item, a, resolve(a, item, 1, 1))
        threading.Thread(target=self.app._link_around, args=(item["id"], a, "1:1", session), daemon=True).start()
        time.sleep(0.2)
        self.app._servers[(item["id"], "1:1")] = {"site": a["id"], "page": "https://a/ep1", "order": ["Good"], "running": False,
                                                  "listed": True, "items": {"Good": {"id": "Good", "site_id": a["id"], "site": "A",
                                                                                     "label": "Good", "kind": "sub", "state": "unchecked"}}}
        self.app.use_server(session, "Good")
        time.sleep(1.5)
        self.assertEqual(self.app.online_video(item["id"], 1, 2, a["id"], "sub"), "https://cdn/Good/2.m3u8")

    def test_a_site_that_opens_in_the_browser_gives_the_player_no_empty_video(self):
        a, b = self.sites("A", "B")
        self.app.settings.update({"video_sites": [a, dict(b, open="browser")]})
        item = self.show()

        def resolve(site, it, season, episode, **kw):
            if site["name"] == "A":
                raise scrape.ScrapeError("not on A")
            return dict(found_at(""), page="https://b.test/ep2")
        self.app.scraper.resolve = resolve
        with self.assertRaises(Exception) as got:
            self.app.online_video(item["id"], 1, 2, a["id"], "")
        self.assertNotIsInstance(got.exception, AssertionError)


class LaterPlayTest(OnlineBase):
    def test_a_slow_play_doesnt_take_over_a_later_one(self):
        self.sites("A")
        slow, other = self.show("Slow Show"), self.show("Other Show")

        def resolve(site, it, season, episode, **kw):
            if it["title"] == "Slow Show":
                time.sleep(1)
            return found_at(f"https://cdn.test/{it['title'].replace(' ', '')}{episode}.m3u8")
        self.app.scraper.resolve = resolve
        self.app._link_around = lambda *a, **k: None
        opened = []

        class S:
            pos, ended, listeners = 0.0, False, []

            def send(self, *a):
                return True

        def fake_play(session):
            opened.append(self.app.store.get(session.item_id)["title"])
            return S()
        with mock.patch.object(player, "find_mpv", return_value="mpv"), \
                mock.patch.object(self.app.player, "play", side_effect=fake_play), \
                mock.patch.object(self.app, "_online_extras"), mock.patch.object(self.app, "_web_started", return_value=(True, "")), \
                mock.patch("watchlist.hlsfix.needs_fix", return_value=False):
            t = threading.Thread(target=self.app.play_scraped, args=(self.app.store.get(slow["id"]), {"episode": 1, "run": "r1"}))
            t.start()
            time.sleep(0.2)
            self.app.play_scraped(self.app.store.get(other["id"]), {"episode": 1, "run": "r2"})
            t.join()
        self.assertEqual(opened, ["Other Show"])


class LookAheadTest(OnlineBase):
    def test_it_stops_going_forward_past_the_last_episode_out(self):
        (a,) = self.sites("A")
        item = self.show(episodes=24)
        asked = []

        def episode_page(site, it, season, episode, pin=""):
            asked.append(episode)
            if episode > 6:
                raise scrape.ScrapeError("not out yet")
            return f"https://a.test/ep{episode}", {"url": "e"}, []
        self.app.scraper.episode_page = episode_page
        self.app._link_around(item["id"], a, "1:5", None)
        self.assertEqual(asked, [6, 4, 7, 8, 9, 10, 3, 2, 1])


class TeachTest(OnlineBase):
    def test_a_search_done_by_hand_becomes_the_sites_search_link(self):
        from watchlist import picker
        opened = []

        def start(item_id, season, episode, site_id, url, list_url=""):
            job = picker.Job(item_id, season, episode, site_id, url)
            picker._jobs[job.id] = job
            opened.append(url)
            return job
        with mock.patch.object(picker, "start", side_effect=start):
            got = self.app.teach_start({"url": "https://anime.example/home", "types": ["anime"]})
        self.assertEqual((opened, got["title"]), (["https://anime.example/home"], "Naruto"))
        job = picker.get(got["id"])
        self.assertEqual(self.app.teach_view(got["id"])["url"], "")
        job.page = "https://anime.example/filter?keyword=Naruto&sort=default"      # (you searched)
        job.page = "https://anime.example/anime/naruto-1234"                       # (and opened its page)
        self.assertEqual(self.app.teach_view(got["id"])["url"], "https://anime.example/filter?keyword={title}&sort=default")
        # (a search link given before: its site's home page opens)
        with mock.patch.object(picker, "start", side_effect=start):
            self.app.teach_start({"url": "https://tv.example/search/{title}", "types": ["tv"]})
        self.assertEqual(opened[-1], "https://tv.example/")


class SubtitlesForTheRestTest(OnlineBase):
    def test_another_episodes_file_isnt_saved_for_this_one(self):
        from watchlist import subfinder
        item = self.show("Frieren", kind="anime", episodes=6)

        def c(ep):
            return {"id": str(ep), "name": "Frieren", "release": f"[SubsPlease] Frieren - {ep:02d} (1080p)", "season": 1,
                    "episode": ep, "downloads": 100, "format": "srt", "url": "", "uploader": "u", "fps": "23.976"}
        saved = {}
        job = {"_answer": "", "done": 0, "total": 0}
        with mock.patch.object(subfinder, "search", side_effect=lambda it, s, e: [c(1), c(2)] if e == 5 else [c(e)]), \
                mock.patch.object(self.app, "_store_sub", side_effect=lambda i, k, best: best["release"]), \
                mock.patch.object(self.app, "_save_sub", side_effect=lambda i, k, path: saved.update({k: path})):
            self.app._subs_for_the_rest(self.app.store.get(item["id"]), 1, 1, c(1), job)
        self.assertNotIn("1:5", saved)
        self.assertEqual(saved.get("1:2"), "[SubsPlease] Frieren - 02 (1080p)")


if __name__ == "__main__":
    unittest.main()
