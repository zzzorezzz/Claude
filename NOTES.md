# Notes: where we left off, and how the builds work

## Where we left off (2026-10-30)

Branch: `claude/blissful-mendel-jghzf5`. All work is committed and pushed. No pull request has been opened.

Latest builds:

| Platform | State | Run |
|---|---|---|
| Android (APK) | Phone player, torrents, watch parties. Built and emulator-checked before round 10. | [run 40896330674](https://github.com/zzzorezzz/Claude/actions/runs/40896330674) |
| Windows (exe) | Round 10, the ad blocker, video sites the ani-cli way (streams, qualities, yt-dlp bundled). Built, tests green. | [run 41022598257](https://github.com/zzzorezzz/Claude/actions/runs/41022598257) |
| Mac (dmg) | Round 9 + the profile-picture fix. | built with the `[mac]` commit `b757170` |

Windows has everything, including the ani-cli-style video sites and yt-dlp (run 41022598257). Mac and Android don't have round 10. Build only the one the user asks for. `gh` is signed in on the user's PC.

### Round 10 (2026-10-30)

- **Your video sites** (`watchlist/scrape.py`, Settings → Your video sites): "Play online (my sites)" searches the user's own site, scores the results against the title (season, kind, extra words, part), opens the entry, finds the episode links (following a Watch/Episodes link), then the video (video/source tags, player settings in scripts, iframes two levels deep). It checks the first bytes of the video and plays it in mpv with `--referrer`. Each site can be set to open in a browser window instead. What it finds is remembered in `scraped.json`: entries for 30 days, the episode pages, and video addresses for 10 minutes. Watch parties carry the video URL (`party["url"]`).
- **Play this with** on the title page (`play_with`, kept per computer in `local.json`). The automatic order: file, then torrent, then ask between video sites and services (with only one set up, that one is used).
- **Torrents**:
  - `packs.quick_own` matches pack files by name straight away, until the MyAnimeList plan is ready.
  - The season chain leaves out side stories and ONAs, and follows season numbers in the entries' names.
  - `clean_old` deletes torrent files 48 h after they arrive (first seen in the folder) unless they've been played since or the show is kept.
- **Title page**:
  - The side shows the Find everything connected button, then Related: up to 5 similar names (`/api/items/<id>/similar`).
  - One folded **Extra stuff** section: torrent files that aren't episodes, as a folder tree, plus extras and same-folder files.
- **App**:
  - Select (multi-select) on the category, Watched and collection pages.
  - Sounds (WebAudio, Settings → Look).
  - Setup prompt and step-by-step guide (`setup_prompt` setting; Settings → Setup guide).
  - Picture cropper.
  - The party panel sits above the windows (z-index 150).
  - The built-in browser is the default (a one-time migration for saved settings).
- **Player**: the controls auto-hide after 5 s (`--cursor-autohide=5000` + uosc `autohide=yes`).
- **Android**: the HLS module (`media3-exoplayer-hls`) and the Referer header for site videos. Not built yet.

### After round 10 (2026-10-30)

- **Changing a video site's link** starts it afresh. The scraper's memory is tied to the link, not the site id. With several sites, there's a button per site.
- **Ad blocker** (`watchlist/webtools.py`, Settings → Browser → Block ads and pop-ups, `adblock` setting). On Windows it patches pywebview's `EdgeChrome.on_webview_ready` / `on_new_window_request`:
  - WebResourceRequested refuses requests to `AD_HOSTS` and `AD_PATH`.
  - NewWindowRequested is handled, so pop-ups aren't opened.
  - `ADBLOCK_JS` hides ad boxes and removes invisible click layers.
  - The app's own windows (127.0.0.1) are left alone. On a Mac the script runs after each load.
- **Scraper fallbacks**:
  - `webtools.render` reads a page after its JavaScript has run in a hidden window, for search results and episode lists.
  - `webtools.catch_video` opens the episode in a hidden, muted window, clicks play in every frame (`AUTOPLAY_JS`), and takes the first `.m3u8` / `.mp4` request with its Referer, Origin and User-Agent. This is Windows only. Sites that need it are remembered (`_sites` in `scraped.json`).
  - `scrape.unpack` handles the `eval(function(p,a,c,k,e,d)` packer and base64 strings.
  - Tried for real on this PC in a hidden WebView2 window (pywebview 6.2, from a scratch venv): it caught an m3u8 inside an embedded player in 3 s, and the pop-under script was blocked.
- **Test an episode** next to each video site: `/api/video-sites/diagnose` shows every step.
- **Try <next site>** while searching, on failure and while playing. A skipped site never starts playing later (`_scrape_runs`).
- Kept generic on purpose: no per-host decryption for particular video hosts.

### Video sites, ani-cli style (2026-10-30)

The user wanted it to work like ani-cli on their own (legal) sites. ani-cli's providers aren't used; only the same steps, done generically in `scrape.py`:
- **Episodes:** `episode_links` skips stream/server buttons (`SERVER_WORDS`, ancestors' class/id via the parser's `_stack`), and only accepts bare numbers as a run of look-alike links.
- **Streams:**
  - `streams(page)` returns `[{label, kind sub/dub/raw, url, how page/embed/click}]`, and `order_streams` puts the last choice first (`_sites[url]` prefs: `kind`, `label`, `quality`, `way`).
  - `resolve` tries up to 4 streams. For each: the page as it is, then `ytdlp_video` (the yt-dlp Python API, with its exact headers incl. Cookie), then `webtools.catch_video(click={label, kind})`.
  - CLICK_JS clicks the button in the hidden window and pings `CLICKED`. Only requests after that count.
- **Quality:** `qualities()` parses an HLS master, and `pick_quality` chooses. The play window shows stream and quality chips.
- **Player closing at once:** mpv writes `--log-file` (`player.web_log`). `_web_started` waits up to 12 s; if mpv ended, `why_it_stopped` reads the log (403…) and play_scraped tries the next stream (`skip`), up to 3.
- **yt-dlp in the builds:** `pip install yt-dlp`, `--hidden-import yt_dlp --collect-submodules yt_dlp`, and the exe self-check requires `yt_dlp`. Android doesn't have it.
- **Tried for real here** (scratch venv with pywebview, libtorrent, yt-dlp): the hidden window clicked "HD-1" under Dub and caught only the dub stream. All 307 tests pass.

### Built for Windows on 2026-10-30 (with the Internet Archive source)

- **Internet Archive source** (2026-10-30, commit after f98dbdd): "+ Add the Internet Archive" in Settings → Your video sites (site `kind: "archive"`, url `https://archive.org`, no `{title}` needed).
  - `scrape.archive_search` calls `advancedsearch.php` (`mediatype:movies`, sorted by downloads, with the year).
  - `scrape.archive_files` reads `/metadata/<id>` for the video files with their heights.
  - `archive_episodes` maps a series' files by episode number; `archive_versions` gives one copy per quality.
  - `Scraper._archive` plugs into `resolve`, so qualities, the play window and mpv work as for other sites.
  - `best_entry` now uses a result's year (another year −15; the right year wins ties).
  - Tried live against archive.org: Night of the Living Dead (1968) → its 1080p…240p files.
- Claude declined a generic "JSON API mode" shaped like AnimePahe's API; the Internet Archive was offered instead.
- Claude also declined tuning the hidden browser's stream clicking for Anikoto (an unlicensed anime site), per the user's own rule about unlicensed streaming sites. The video-site tools stay generic, for the user's own or legal sites.

### Not tested yet

- A real phone ↔ real computer party, and a phone joining as a guest.
- The scraper against the user's real site (only tested with a local fake site). Sites that build their player with JavaScript only open in the browser.
- Round 10 in the built Windows app (built, not tried by hand yet); Mac and Android aren't built.

### Setup guide

The user's guide steps, plus one they asked for: "Your country, name and picture" (optional). They didn't want the Windows "Run anyway" / firewall step. Add nothing else unless asked.

### Possible next steps

- Bigger emulator screen for nicer screenshots (the emulator is 320x640).
- jlibtorrent 2.0.12.9 copies its native library to the cache on each start. 2.0.15.0 fixes that once it's on FrostWire's Maven repo.

### Rules from the user (keep following)

- Never put TMDB / MyAnimeList / AI keys in the repo. They live in `settings.json` in the app's data folder.
- Don't build scrapers or downloaders for unlicensed streaming sites. "Your torrent sites" is generic, for the user's own sites.
- Commit and push only to this branch. No PRs unless asked.
- When the user asks a question or says "explain first / don't build", answer without building.

## How the builds work

Everything builds on GitHub Actions in one workflow, `.github/workflows/build-exe.yml`. It runs on pushes to `claude/**`, `main` and `master`, or by hand (**Run workflow**, which builds all three). The commit message decides which jobs run:

| Commit message contains | Windows | Mac | Android |
|---|---|---|---|
| nothing special | ✔ | | |
| `[mac]` | ✔ | ✔ | |
| `[mac] [mac only]` | | ✔ | |
| `[android]` | ✔ | | ✔ |
| `[android] [android only]` | | | ✔ |

Each job runs the full test suite first (`python -m tests.ci`: if a test hangs it prints every thread's stack and stops after 8 minutes, and a failed run is tried once more, for the timing-dependent torrent tests). The results are the run's **Artifacts**:
- `WatchAnything-windows`: `Watch Anything.exe`
- `WatchAnything-mac`: `WatchAnything.dmg`
- `WatchAnything-android`: `WatchAnything.apk`
- `android-screenshots`

Artifacts download as zips.

### Windows exe (`windows` job, windows-latest)

1. Installs `libtorrent` from pip, then PyInstaller 6.22.1, pywebview 6.2 and certifi.
2. Downloads mpv (the built-in player) and the uosc controls. The uosc scripts and fonts are git-ignored and fetched at build time.
3. `pyinstaller main.py --onefile --windowed`, with `packaging/watchlist.ico` and the `watchlist/web` and `mpv_config` data, gives `dist/Watch Anything.exe`.
4. Checks the exe has everything it needs (`python -m watchlist` self-report), that mpv plays and is followed, and that the exe starts and serves the app.

### Mac dmg (`mac` job, macos-latest, Apple Silicon)

1. Same steps as Windows: libtorrent, mpv and uosc, then PyInstaller builds `Watch Anything.app` with `packaging/watchlist.icns`.
2. Checks the app doesn't register as the default for any file type, has everything it needs, plays with mpv and starts.
3. `hdiutil create` makes `WatchAnything.dmg`, with an Applications shortcut for drag-to-install.
4. The app isn't signed. Users open it once through System Settings → Privacy & Security → **Open Anyway** (see README).

### Android apk (`android` job, ubuntu-latest)

The project is in `android/`. It uses Gradle 8.5, AGP 8.2.2, JDK 17, compileSdk 34 and minSdk 24, for arm64-v8a and x86_64 only.

1. Copies `watchlist/` into `android/app/src/main/python/`, without the mpv files (git-ignored there).
2. Chaquopy 15.0.1 bundles Python 3.12 and runs `watchlist.android.start()`. That starts the same server on port 8765, and `MainActivity`'s WebView loads it.
3. Dependencies:
   - Media3 ExoPlayer 1.2.1: the phone player, `PlayerActivity`.
   - jlibtorrent 2.0.12.9 (`com.frostwire:jlibtorrent` plus the `-android-arm64` and `-android-x86_64` jars), from FrostWire's Maven repo `https://dl.frostwire.com/maven`. This is declared in `android/settings.gradle.kts`.
4. `gradle -p android assembleDebug` gives `WatchAnything.apk`. It's a debug-signed APK, installed by sideloading.
5. `reactivecircus/android-emulator-runner` (API 30, x86_64) runs `android/smoke-test.sh`. That installs and starts the app and checks `/api/ping`. It then runs `android/torrent-test.sh`, which streams Big Buck Bunny (Creative Commons) from its torrent and hosts a watch party with it. Look for `TORRENT TEST: OK` and `PARTY TEST: OK` in the job log.

How the phone pieces fit together:

| Piece | Where | What it does |
|---|---|---|
| Java → Python | `watchlist/android.py` | `start(bridge, files_dir)`, and `player_event(session, json)` from the player twice a second |
| Python → Java | `MainActivity.Bridge` | `openUrl`, `openPlayer(json)`, `playerCommand(session, json)` (seek, pause, play, speed, text, replace, close) |
| JS → Java | `window.Android` | `play`, `openUrl`, `share`, `hasFiles`, `askForFiles` |
| Progress | `POST /api/android/progress` | Where you are in an episode, and when it's finished |
| Torrents | `watchlist/lt_android.py` | Mimics the subset of libtorrent's Python API that `torrents.py` uses. Stream pieces are read from the downloaded files (`PIECES_FROM_DISK`), because jlibtorrent doesn't pass `read_piece` data to Java. |

### Developing without CI

- Tests: `python -m unittest discover -s tests -t .` (CI uses `python -m tests.ci`). Without libtorrent, 7 tests in `test_sites` fail locally; a venv with `pip install libtorrent` runs them all. The phone pieces are tested with a stand-in Java module, in `tests/test_android.py` and `PhoneTest` in `tests/test_round9.py`.
- JS syntax check: `node --check watchlist/web/app.js`.
- This cloud session can't reach Google's or FrostWire's Maven repos, so Android only builds on CI.
