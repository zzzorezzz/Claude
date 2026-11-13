"""Round 16 (1.4): every subtitle track a site has - every language, its signs-only and dub
captions told apart, those written into its page or told to its player, those dressed up as
pictures - through the app to the player; a site's video that's another episode's never played;
"Burn the Witch #0.8" found as itself; both languages' servers listed; hidden pages kept quiet."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from watchlist import scrape, webplay, webtools
from watchlist.server import App, make_server
from watchlist.store import new_item


def answer(body):
    r = mock.MagicMock()
    r.__enter__.return_value.read.return_value = body
    return r


def srt(lines, words="I can't believe you're here, that is what you said"):
    return "".join(f"{n + 1}\n00:{n // 60:02d}:{n % 60:02d},000 --> 00:{n // 60:02d}:{n % 60:02d},900\n{words} {n}\n\n"
                   for n in range(lines)).encode()


class PageSubtitlesTest(unittest.TestCase):
    def test_a_list_written_as_json_inside_the_page_is_read_with_its_labels(self):
        # (AniZone: Livewire writes the player's settings into an attribute, escaped twice)
        tracks = [{"title": "English - Forced", "format": "srt", "language": "en", "default": False, "forced": "yes",
                   "file": "https://cdn.test/v1/subtitles/0_en.srt"},
                  {"title": "English", "format": "srt", "language": "en", "default": True, "forced": "no",
                   "file": "https://cdn.test/v1/subtitles/1_en.srt"},
                  {"title": "Spanish - Latin American", "language": "es-419", "file": "https://cdn.test/v1/subtitles/15_es-419.srt"},
                  {"title": "Chapters", "file": "https://cdn.test/v1/chapters.vtt", "kind": "chapters"}]
        inner = json.dumps({"subtitles": tracks}).replace('"', "\\u0022").replace("/", "\\\\\\/")
        page = f'<div wire:snapshot="{{&quot;data&quot;:&quot;{inner}&quot;}}"></div>'
        got = scrape.page_subtitles(page, "https://site.test/anime/x/1")
        self.assertEqual([(x["label"], x["lang"], x["forced"], x["default"]) for x in got],
                         [("English - Forced", "en", True, False), ("English", "en", False, True),
                          ("Spanish - Latin American", "es", False, False)])
        self.assertEqual(got[1]["url"], "https://cdn.test/v1/subtitles/1_en.srt")

    def test_track_tags_and_a_players_settings_are_read_but_not_its_thumbnails(self):
        page = """<video><track kind="subtitles" src="/subs/ep1-por.vtt" srclang="pt" label="Português">
                  <track kind="metadata" src="/thumbs/sprite.vtt"></video>
                  <script>jwplayer().setup({tracks: [{file: "https://c.test/fr.vtt", label: "Français", kind: "captions"},
                  {file: "https://c.test/thumbnails.vtt", kind: "thumbnails"}]})</script>"""
        got = scrape.page_subtitles(page, "https://site.test/watch/1")
        self.assertEqual([(x["url"], x["lang"]) for x in got],
                         [("https://site.test/subs/ep1-por.vtt", "pt"), ("https://c.test/fr.vtt", "fr")])

    def test_languages_from_labels_codes_and_file_names(self):
        for text, code in [("English - Forced", "en"), ("eng", "en"), ("en-US", "en"), ("Portuguese (Brazil)", "pt"),
                           ("German - Dubtitle", "de"), ("Bahasa Indonesia", "id"), ("Croatian", "hr"), ("Deutsch", "de"), ("", "")]:
            self.assertEqual(scrape.lang_of(text), code, text)
        self.assertEqual(scrape._url_lang("https://x.test/sub/1_en.srt"), "en")
        self.assertEqual(scrape._url_lang("https://x.test/subs/spa.vtt"), "es")
        self.assertEqual(scrape._url_lang("https://x.test/a/5d59.jpeg"), "")
        self.assertEqual(scrape._url_lang("https://x.test/s.vtt?id=4"), "")        # ("id" there isn't Indonesian)
        # (letters inside a hashed name aren't a language)
        self.assertEqual(scrape._url_lang("https://x.test/s/3fa91c/master.vtt"), "")
        self.assertEqual(scrape._url_lang("https://x.test/kz81es0q.vtt"), "")

    def test_an_unlabelled_english_file_with_a_hashed_name_is_english(self):
        with mock.patch("urllib.request.urlopen", return_value=answer(srt(200))):
            got = scrape.subtitle_tracks(["https://x.test/s/3fa91c/master.vtt"])
        self.assertEqual((got[0]["lang"], got[0]["label"]), ("en", "English"))

    def test_a_page_that_cant_be_read_again_never_loses_the_video(self):
        with mock.patch.object(scrape, "fetch", side_effect=LookupError("unknown encoding: utf8mb4")), \
                mock.patch.object(scrape, "subtitle_tracks", return_value=[]):
            self.assertEqual(scrape.site_subtitles([], {}, pages=("https://site.test/watch/1",)), [])

    def test_share_buttons_and_comments_arent_servers(self):
        page = scrape.parse('<iframe src="https://t.sharethis.com/x"></iframe><iframe src="https://theanimecommunity.com/embed/1">'
                            '</iframe><iframe src="https://player.test/e/abc"></iframe>', "https://site.test/watch/1")
        self.assertEqual([x["label"] for x in scrape.streams(page)], ["player.test"])


class SubtitleTracksTest(unittest.TestCase):
    def test_every_language_ordered_whole_english_first_and_signs_told_apart(self):
        bodies = {"https://c.test/a/1.jpeg": srt(300),                         # (subtitles served as a picture)
                  "https://c.test/a/2.jpeg": srt(20),                          # (English, far shorter: signs only)
                  "https://c.test/a/3.jpeg": srt(250, "que no es lo que pero por eso está aquí una vez"),
                  "https://c.test/a/thumbs.vtt": b"WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nsprite.jpg#xywh=0,0,160,90\n"}
        opened = []

        def urlopen(req, **k):
            opened.append(req.full_url)
            return answer(bodies[req.full_url])
        listed = [{"url": "https://c.test/v/2_en.srt", "label": "English - Dubtitle", "lang": "en", "forced": False, "default": False}]
        with mock.patch("urllib.request.urlopen", side_effect=urlopen):
            got = scrape.subtitle_tracks(list(bodies), {"Referer": "https://site.test/"}, listed)
        self.assertEqual([(x["label"], x["lang"], x["forced"], x["dub"]) for x in got],
                         [("English", "en", False, False), ("English - Dubtitle", "en", False, True),
                          ("English (signs)", "en", True, False), ("Spanish", "es", False, False)])
        self.assertNotIn("https://c.test/v/2_en.srt", opened)               # (named by the page: taken at its word)
        self.assertEqual(got[0]["format"], "srt")
        with mock.patch("urllib.request.urlopen", side_effect=urlopen):
            self.assertEqual(scrape.pick_subtitle(list(bodies)), "https://c.test/a/1.jpeg")

    def test_a_json_list_of_subtitles_is_followed_with_its_labels(self):
        bodies = {"https://api.test/subs?id=1": json.dumps([{"file": "https://c.test/x/por.vtt", "label": "Português"},
                                                             {"file": "https://c.test/x/en.vtt", "label": "English"}]).encode(),
                  "https://c.test/x/por.vtt": b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nVoce nao sabe o que diz\n",
                  "https://c.test/x/en.vtt": b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nYou don't know what you're saying\n"}
        with mock.patch("urllib.request.urlopen", side_effect=lambda req, **k: answer(bodies[req.full_url])):
            got = scrape.subtitle_tracks(["https://api.test/subs?id=1"])
        self.assertEqual([(x["label"], x["lang"], x["format"]) for x in got], [("English", "en", "vtt"), ("Português", "pt", "vtt")])

    def test_the_players_and_the_pages_lists_are_put_together(self):
        page = '<script>player.setup({"tracks":[{"file":"https://c.test/ar.srt","label":"Arabic"}]})</script>'
        with mock.patch.object(scrape, "fetch", return_value=(page, "https://site.test/watch/1")) as fetched, \
                mock.patch.object(scrape, "subtitle_tracks", side_effect=lambda urls, headers=None, listed=(): list(listed)):
            got = scrape.site_subtitles([], {"Referer": "https://player.test/e/1"},
                                        pages=("https://site.test/watch/1", "https://player.test/e/1"),
                                        listed=[{"url": "https://c.test/de.vtt", "label": "Deutsch", "lang": "ger"}])
        self.assertEqual([(x["url"], x["lang"]) for x in got], [("https://c.test/de.vtt", "de"), ("https://c.test/ar.srt", "ar")])
        self.assertEqual(fetched.call_count, 2)

    def test_what_a_player_is_told_comes_back_from_its_page(self):
        self.assertIn(webtools.SUBS, webtools.SUBS_JS)
        opts = {}
        sent = [{"u": "https://c.test/en.vtt", "l": "English", "g": "en", "f": False, "d": True},
                {"u": "javascript:alert(1)", "l": "x"}, {"u": "https://c.test/en.vtt", "l": "again"}]
        self.assertTrue(webtools.subs_signal(webtools.SUBS + urllib.parse.quote(json.dumps(sent)), opts))
        self.assertEqual(opts["listed"], [{"url": "https://c.test/en.vtt", "label": "English", "lang": "en", "forced": False, "default": True}])
        self.assertFalse(webtools.subs_signal("https://c.test/en.vtt", opts))


class PlayerSubtitlesTest(unittest.TestCase):
    def setUp(self):
        self.app = App(tempfile.mkdtemp(prefix="wa-r16-"))
        self.server = make_server(self.app, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.app.torrents.port = self.server.server_address[1]
        self.tracks = [{"url": "https://c.test/1_en.srt", "label": "English", "lang": "en", "forced": False, "format": "srt"},
                       {"url": "https://c.test/0_en.srt", "label": "English - Forced", "lang": "en", "forced": True, "format": "srt"},
                       {"url": "https://c.test/a/9.jpeg", "label": "Arabic", "lang": "ar", "forced": False, "format": "vtt"}]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.shutdown()

    def found(self, kind):
        return {"url": "https://cdn.test/master.m3u8", "referer": "https://site.test/watch/1", "headers": {},
                "subtitle": self.tracks[0]["url"], "subtitles": self.tracks, "stream": {"kind": kind}}

    def test_every_track_goes_to_the_player_the_right_one_chosen(self):
        sub = self.app._site_subs(self.found("sub"))
        self.assertEqual([(t["title"], t["lang"], t["select"]) for t in sub],
                         [("English", "en", True), ("English - Forced", "en", False), ("Arabic", "ar", False)])
        self.assertTrue(all(t["url"].startswith(f"http://127.0.0.1:{self.app.torrents.port}/subs/") for t in sub))
        self.assertTrue(sub[0]["url"].endswith(".srt") and sub[2]["url"].endswith(".vtt"))
        dub = self.app._site_subs(self.found("dub"))
        self.assertEqual([t["select"] for t in dub], [False, True, False])          # (a dub: its signs only)
        self.assertFalse(any(t["select"] for t in self.app._site_subs(self.found("sub"), choose=False)))

    def test_your_own_subtitles_come_once_chosen_over_the_sites(self):
        item = self.app.store.add(dict(new_item("anime", {"source": "mal", "source_id": "1", "title": "Show"}), format="series"))
        with mock.patch.object(self.app, "saved_sub", return_value="C:/subs/ep1.srt"):
            entry = self.app._web_entry(item, {"name": "Site", "url": "https://site.test/"}, "1:1", self.found("sub"))
        self.assertIsNone(entry["sub"])
        self.assertTrue(entry["added_mine"])
        self.assertEqual([(t["title"], t["select"]) for t in entry["extras"]],
                         [("Your subtitles", True), ("English", False), ("English - Forced", False), ("Arabic", False)])

    def test_the_player_gets_a_sites_subtitles_through_the_app_as_its_player_did(self):
        link = self.app._site_subs(self.found("sub"))[2]["url"]
        with mock.patch.object(webplay, "open_remote", return_value=answer(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nmarhaba\n")) as remote:
            with urllib.request.urlopen(link, timeout=10) as r:
                body, kind = r.read(), r.headers.get("Content-Type")
        self.assertIn(b"marhaba", body)
        self.assertTrue(kind.startswith("text/vtt"))
        self.assertEqual(remote.call_args[0][0], "https://c.test/a/9.jpeg")
        self.assertEqual(remote.call_args[0][1]["Referer"], "https://site.test/watch/1")
        wrong = link.replace(f"/subs/{self.app.torrents.token}/", "/subs/nope/")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(wrong, timeout=10)
        self.assertEqual(e.exception.code, 404)

    def test_a_video_another_episode_plays_is_told_apart(self):
        self.app._online_found[("it", "1:2")] = ({"name": "S", "id": "s"}, {"url": "https://cdn.test/ep2.m3u8"}, 0)
        self.assertTrue(self.app._another_episodes("it", "1:1", {"url": "https://cdn.test/ep2.m3u8"}))
        self.assertFalse(self.app._another_episodes("it", "1:2", {"url": "https://cdn.test/ep2.m3u8"}))
        self.assertFalse(self.app._another_episodes("it", "1:1", {"url": "https://cdn.test/ep1.m3u8"}))


class FindingTest(unittest.TestCase):
    def test_a_number_written_with_a_hash_is_the_titles_own(self):
        wanted = {"names": ["Burn the Witch #0.8"], "season": None, "kind": "movie", "year": None, "part": None}
        found = [{"url": "https://s.test/watch/burn-the-witch", "name": "Burn the Witch", "names": ["Burn the Witch"]},
                 {"url": "https://s.test/watch/burn-the-witch-08", "name": "Play BURN THE WITCH #0.8",
                  "names": ["Play BURN THE WITCH #0.8", "BURN THE WITCH #0.8"]}]
        self.assertEqual(scrape.best_entry(found, wanted)[0]["url"], "https://s.test/watch/burn-the-witch-08")
        self.assertIn("Burn the Witch 0.8", scrape.name_variants(["Burn the Witch #0.8"]))

    def test_a_server_that_says_no_language_doesnt_make_the_list_two_languages(self):
        s = scrape.Scraper(tempfile.mkdtemp())
        s.servers = mock.Mock(return_value=[])
        s.can_catch = lambda: True
        found = {"page": "https://s.test/watch/1", "streams": [{"id": "a", "label": "sun", "kind": "dub"},
                                                               {"id": "b", "label": "theanimecommunity.com", "kind": ""}]}
        s.episode_servers(found)
        s.servers.assert_called_once()


class QuietTest(unittest.TestCase):
    def test_a_hidden_window_says_its_hidden_so_its_kept_quiet(self):
        opts = {"adblock": True}
        with mock.patch.object(webtools, "_webview") as wv, mock.patch.object(webtools, "patch"):
            webtools._hidden_window("https://site.test/page", opts)
        self.assertTrue(opts["hidden"])
        self.assertIs(webtools._pending["https://site.test/page"], opts)
        wv.return_value.create_window.assert_called_once()

    def test_the_phones_hidden_pages_are_muted_but_not_the_one_you_see(self):
        from watchlist import droidweb
        bridge = mock.Mock()
        bridge.pageOpen.return_value = "p1"
        bridge.pageUserAgent.return_value = "UA"
        with mock.patch.object(droidweb.launcher, "android_bridge", bridge, create=True), \
                mock.patch.object(droidweb.Page, "_watch", lambda self: None):
            droidweb.Page(["x"])
            droidweb.Page(["y"], hidden=False)
        hidden, shown = [json.loads(c[0][0]) for c in bridge.pageOpen.call_args_list]
        self.assertIn(webtools.MUTE_JS, hidden)
        self.assertNotIn(webtools.MUTE_JS, shown)


if __name__ == "__main__":
    unittest.main()
