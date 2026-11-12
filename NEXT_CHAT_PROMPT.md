# Watch Anything – go through the whole app and fix everything that's broken

You're continuing work on **Watch Anything**, my desktop app (Windows and Mac, plus an Android app and a "phone link" for iPhone/iPad). It keeps a watch list of TV shows, anime and films, and plays them from downloads, torrents, my own video sites and streaming services, with watch parties.

The code is in this repository, on the branch `claude/blissful-mendel-jghzf5`. The last version is **1.3.8.4**; its builds and documents are in `dist/1.3.8.4/`.

## Your job

Look at the **whole app, top to bottom**, and fix anything that is buggy or doesn't work as it's supposed to. Test each fix for real. When you're done, build a new version and give me a report.

## Read these first, before changing anything

1. `dist/1.3.8.4/Watch Anything - Report and features.md`: **Part 2** is every feature and how it's supposed to work. That's the standard to check the app against.
2. `dist/1.3.8.4/Watch Anything - Full check report.md`: **section 4** lists the problems already found and not fixed yet; **section 3** lists every site and why it does or doesn't play.
3. `NOTES.md` (how the code is laid out, how builds work, earlier rounds), `README.md`, and `dist/1.3.8.4/Watch Anything - Update log.txt`.
4. The code: `watchlist/` (mainly `server.py`, `scrape.py`, `webtools.py`, `cdp.py`, `torrents.py`, `packs.py`, `scanner.py`, `library.py`, `store.py`, `player.py`, `party.py`, `partyserver.py`, `relay.py`, `remote.py`, `webplay.py`, `transcode.py`, `metadata.py`, `ai.py`, `mpv_scripts/watchlist_skip.lua`, `mpv_config/input.conf`), and the UI (`web/app.js`, `web/phone.js`, `web/player.js`, `web/style.css`). Tests are in `tests/`.

## Order of work

1. **Fix every unfixed problem in section 4 of the full check report.**
   - Most important first: the torrent and download problems that delete or lose files, then the phone player and watch party ones, then the rest.
   - For each one: reproduce it, fix it, and add a test that would have caught it.
2. **Go through every feature in Part 2 of "Report and features", one section at a time.**
   - Use it the way I would, on a test copy of the app (see the rules below).
   - Find what's broken, slow or confusing, and fix it.
   - Read the code around each feature too: look for crashes on odd input, threads that race, things never cleaned up, missing error handling, and UI that doesn't refresh or breaks at phone width.
   - Cover everything: the list and home screen, folders and collections, sharing, Find Media, downloads sorting and packs, the built-in player and its menu and keys, VLC and other players, playing from my sites (search, matching, episodes, servers, sub/dub), streaming services, torrents (adding, streaming, the Torrents window, Get magnet, cleanup), watch parties, AI helpers, the phone link (pairing, layout, player, saving to the iPad), Android, sync between computers, settings and the setup guide.
3. **Then, if there's time, the sites that don't play yet** (section 3 of the full check report): search not found, video not found, and so on.
   - Fix what can be fixed in general, so it helps every site, not just one.
   - The test harness used last time is described in "How to test" below.
4. **Finish:**
   - Run every test.
   - Run the recommended sites on Windows, Mac and Android.
   - Build the new version.
   - Write the update log, guide and a report.

## Rules (keep all of these)

- **Never touch my real data:** not `G:\My Drive\Watchlist` (my sync folder), and not `%APPDATA%\Watchlist`. Test with temporary data folders only.
- **Don't change the Windows firewall** or open firewall ports by any other means.
- **Never build any way of exposing the PC to the internet:** no port forwarding instructions, no tunnels to third-party servers.
- **Every API route, stream and file route checks the token for requests that aren't local;** requests from 127.0.0.1 stay as they are.
  - Rate-limit pairing attempts.
  - Never log tokens.
  - Never put keys or tokens in the repo or in URLs: streams use short-lived signed tickets.
  - Never put a site's real video URL or its headers in the phone's URL.
- **Don't get around Cloudflare checks or other anti-bot pages.** A site that needs one opens in the browser instead.
- **Libraries:** only from cdnjs or jsdelivr with exact pinned versions, or bundled.
- **Builds:** the exe, dmg and apk go in `dist/<version>/` along with `Watch Anything - Update log.txt` and `Watch Anything - Guide.txt`.
- **Commits and CI:**
  - Commit with `git -c user.name=Claude -c user.email=noreply@anthropic.com commit`, ending the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - Pushing to `claude/**` builds Windows. Putting `[mac] [android]` in the commit message builds the Mac and Android versions too; adding `[live]` also runs the recommended-sites test on GitHub's Mac.
  - Fetch builds with `gh run download`.
- **Write code like the code around it:** same naming, same short comments, same style.
- **Version:** call this one **1.3.9** (in `watchlist/__init__.py` and `android/app/build.gradle.kts`) unless I say otherwise.

## How to test

- **Python with everything installed** (pywebview, libtorrent, Playwright's needs):
  `C:/Users/zorux/AppData/Local/Temp/claude/D--watch-anything-github-Claude/18c7230b-7e8c-448d-8dc9-5756e707497c/scratchpad/wvenv/Scripts/python.exe`
  - If that's gone, make a new virtualenv from `requirements.txt` plus `libtorrent`.
  - System Python has Playwright: use it to drive the UI in Edge (`channel="msedge"`).
- **All the tests:** `python -m unittest discover -s tests -t .` (about 500 tests).
  - GitHub's test run must finish within **8 minutes**, so don't make tests slow. Avoid real network calls in tests, and avoid waiting for the disk on every save.
- **A test copy of the app:** `server.App(tempfile.mkdtemp())` and `server.make_server(app, port)`. Calls to the API need the header `X-Watchlist: 1`. `tests/test_server.py` and `tests/test_remote.py` show how; the latter covers the phone link from another address.
- **The video player** is `packaging/mpv/mpv.exe`. Run from source, the app only finds it when `WATCHLIST_MPV` is set. Without it, Play opens episode pages in my real browser, so always set it.
- **Recommended sites, whole app:** `python -m tests.live_app report.json AniZone Miruro Atlantic Bingr ShuttleTV`. Add `WATCHLIST_CDP=1` to test the Mac's way (hidden Chromium) on Windows.
- **Recommended sites, three titles each:** `python -m tests.live_sites report.json`.
- **AnikotoTV was down** at the last check (its address didn't exist). If it still is, skip it.
- **AniZone and Miruro block GitHub's computers,** so test them from this PC.
- **The site sweep from last time:** `sweep_one.py`, `sweep.py` and `sweep_sum.py` in the scratchpad folder above. Site lists are in `sites1.json` and `sites2.json`; the combined results are in `sites_final.json`.
  - Run **at most 3 hidden browsers at once**. With more, results are wrong because the PC is overloaded.
- **Torrents:** test with the public-domain Big Buck Bunny magnet (`dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c`, with WebTorrent's web seed `ws=https://webtorrent.io/torrents/`). Never download anything else.
- **The phone link and its player:** Playwright at phone size, and at iPad size with a mouse. `phone.js` treats any host other than 127.0.0.1 as a phone.
- **Don't open windows or tabs on my screen** unless that's the point of the test. Use headless or off-screen browsers and `--vo=null` for mpv.

## The report I want at the end

1. **Every problem you found:** what it was, what you did, and how you checked it worked.
2. **What you couldn't fix, and why.**
3. **Each recommended site** on Windows, Mac and Android.
4. **Anything you'd suggest for 1.4.**

Keep it in plain words. Save it in `dist/1.3.9/`.
