# Notes: where we left off, and how the builds work

## Where we left off (2026-10-30)

Branch: `claude/blissful-mendel-jghzf5`. All work is committed and pushed. No pull request has been opened.

Latest builds:

| Platform | State | Run |
|---|---|---|
| Android (APK) | Phone player, torrents, watch parties. Built and emulator-checked before round 10. | [run 40896330674](https://github.com/zzzorezzz/Claude/actions/runs/40896330674) |
| Windows (exe) | Round 9 + the profile-picture fix. Not rebuilt since the Android work or round 10. | [run 40884107879](https://github.com/zzzorezzz/Claude/actions/runs/40884107879) |
| Mac (dmg) | Round 9 + the profile-picture fix. | built with the `[mac]` commit `b757170` |

Round 10 was committed with `[mac only]` (no build): none of the platforms has it yet. Build only the one the user asks for.

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

### Not tested yet

- A real phone ↔ real computer party, and a phone joining as a guest.
- The scraper against the user's real site (only tested with a local fake site). Sites that build their player with JavaScript only open in the browser.
- Round 10 on any built app.

### Setup guide: steps the user's guide doesn't have (asked whether to add them)

- Your video sites (the new scraper).
- Your country (for which services have a title).
- Your name and picture for watch parties.
- On Windows: "Windows protected your PC" → More info → Run anyway, and the firewall prompt the first time a torrent runs.

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

Each job runs the full test suite first (`python -m unittest discover -s tests -t .`). The results are the run's **Artifacts**:
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

- Tests: `python -m unittest discover -s tests -t .`. The phone pieces are tested with a stand-in Java module, in `tests/test_android.py` and `PhoneTest` in `tests/test_round9.py`.
- JS syntax check: `node --check watchlist/web/app.js`.
- This cloud session can't reach Google's or FrostWire's Maven repos, so Android only builds on CI.
