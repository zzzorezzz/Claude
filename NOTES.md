# Notes: where we left off, and how the builds work

## Round 1.3.9 (2026-11-13): the whole app checked through and fixed

Docs: `dist/1.3.9/` (update log, guide, `Watch Anything - Report.md`). Tests for this round: `tests/test_round15.py`, `tests/test_round15_sites.py`, `tests/test_party_hardening.py`, plus new cases in `tests/test_torrents.py`.

- **Torrents** (`torrents.py`): resume data (`_save_resume` every RESUME_EVERY and at shutdown after `ses.pause()`, `_wrote_resume` writes `<hash>.resume` + `rec["on_disk"]` sizes, `_resumed` skips it when a file is gone or shorter; `delete_files` / `remove` / `_let_go` forget it). `rec["have"]` (`_note_have`, every 10 s, not while checking) + `_finished()` so Keep / `remove_keeping` don't lose files during a recheck. `replace_for(keep_done=None)` = the title's Keep; a pack other titles use goes to `_let_go` (overrides "none" for this title's files, the record given to another owner). `prepare` leaves `rec["whole"]` files alone. `_retry_errors`: a torrent in an error state (`status().errc`) is `clear_error()`ed every RETRY_AFTER when the disk has room. `reassign(old, new)` for Use MyAnimeList instead. A file whose data arrived only in the `.parts` file while skipped still makes libtorrent reject resume data (it rechecks: safe).
- **Episode names** (`scanner.py`): `_RANGE`, `_S_EP`, `_HALF` (-> season 0, no episode); `episode_map` counts a season "from the start of the show" only when that season's lowest episode is past both its size and the seasons before it.
- **Store / sync** (`store.py`): `Busy` (a list file that can't be opened) -> `blocked` (read again at the next check, `_save` refuses); every write does `_check(force=True)` first; `mirror` (data/watchlist.json) written on every synced save; a failed write to the sync folder sets `away` and saves to the mirror (`App.retry_sync` goes back); `_merge` type-checks rows; `_still_shown` lists titles left in no collection. `config.write_json`: a tmp name per thread, retried replace.
- **Your sites** (`server.py`): `_online_site_now` (the site moved to: next episodes from it), `_online_gen` + `_online_choice()` (a server / site / version chosen: what was being found ahead under the last choice isn't kept), `_finding` events carry `wanted` (another site's look-up isn't waited for), `_play_seq` (only the latest play opens), browser-only sites skipped for the player. `scrape.resolve` caches by `page_key` (with `#wa-press=`). `webtools.carried()` passes "background" to pool threads. `webtools.CARD_LINKS_JS` (render `cards=True` for searches): result cards without links pressed with pushState/replaceState/window.open held back, their addresses put in a hidden box outside `<body>` (the site may redraw).
- **Player** (`player.py`): `_between` / `_held` / `_arrived()`: after end-file/start-file, time-pos and duration wait until mpv says which entry it is (path / playlist-pos), or 2 s after file-loaded.
- **Phone**: `transcode.keyframe_before` + `Job.past` (SEEK_PAST 0.15 s, ffmpeg's 3/23 s B-frame back-off) so a copied video's `start` is its real key frame; `-output_ts_offset` on the subtitles. `player.js`: `WP.begin` (one "start at"), `wpClose(already, which)`, Escape order, `launch.offline`. `phone.js`: `awaySaved`, `savePiecesGone`. `sw.js` v2 caches player.js. `server._web_ended` stops the job only for the device's current session. Keys masked to phones (`SECRET_SETTINGS`, `MASK`). `_sub_allowed` for phone subtitle paths. `webplay.open_remote` only http(s).
- **Parties** (agent-written, reviewed): `partyserver.py` validates every message (bad -> dropped, direct sender cut off), per-client outbox + writer thread (MAX_QUEUED), DROP_AFTER 45 s, `close_port` (UPnP DeletePortMapping) on stop + atexit; `relay.py` envelopes `n`/`t`/`s`/`g` (`_envelope`, `_Numbered`), REPLAY_WINDOW, IDLE; older-format relay messages are dropped.
- **UI** (`app.js`): `openModal` focuses the window + `modalsInert()`; `closeModal` gives focus back; `closeMenu()` also hides the top-bar dropdowns; review drafts kept across `drawTitle`; view-transition promises caught.
- **Testing here**: scratchpad `uiserve.py` (a test copy with fake TMDB/MAL; `PHONE=1` adds 127.0.0.2), `ui_phone.py` / `phone_run.sh` (pairs a headless phone and plays), `bbb.py` (Big Buck Bunny end to end). Playwright's `wait_for_function` is blocked by the app's CSP: poll with `page.evaluate`.

## Round 1.3.8.4 (2026-11-12): the right episode, matching that asks once, 117 sites

Reports: `dist/1.3.8.4/Watch Anything - Full check report.md` (sites, the whole-app check, what's left) and `... - Report and features.md` (every feature). Next chat: `NEXT_CHAT_PROMPT.md`.

- **Wrong episode**: `webtools.MENU_NAMED_JS` (`menuNamed`): a "Server" menu is named by its own few words, never an episode's button (Miruro's hold their summary). `Scraper._here.taken` (videos other episodes played) and `_here.episode` + `other_episode()` (a player addressed `{tmdb id}/{s}/{e}` for another episode) are refused in `_catch_stream` and `resolve`; `webtools.unwanted(skip)` makes both catchers wait past them.
- **Matching** (`scrape.score`, `_entry`): `_fold` (romaji long vowels, wo/o), `_numbers` (a number one name has and the other hasn't: -40; not a card's "37 eps", "8.4", leading counts), MATCH_TO_PLAY 65, ask between 40 and 65, a rival within 6/10 points under 90 asks, `_alike` (same own words, same season) twins go to `_by_episodes` then `_by_year`, else ask ("has more than one X: which is it?"); a kind said on any of a card's names counts (`best_entry`); `_NO_NAME` ("No Image") and year-only links named by their address slug (short ids stripped). Picking another entry clears its videos.
- **Sites**: Anikoto/HiAnime copies (`watch-main` / `wrapper` / `main-wrapper` data-id; `/ajax/episode/list/{id}`, data-num or data-number; also from the title's own /watch/ page); one episode link followed to its list; `derive_episode` from one `?ep=1`-style link; `find_search(typer=webtools.type_search)` types into a site's box (`TYPE_SEARCH_JS`, `render(then=...)`), and `TYPED` ("#wa-type={title}") for results shown as you type; `works()` accepts a smaller library; `_NOT_SERVER` labels and comment buttons aren't servers; `refused()` swaps a 403 copy for another caught one; only http(s) videos; `cdp._opened` retries a page while Chromium starts.
- **Player**: `watchlist_skip.lua` `shortcuts` (uosc `open-menu` from `input-bindings`; ? / F1; `#!` only on menu lines in input.conf - uosc reads it even in comments). Phone player: `WP_KEYS`, `wpKeys()`, right-click -> `wpMenu`.
- **Fixes from the review**: `REMOTE_KEEPS_OFF` settings and a phone can't move a title's folder; `clean_leftovers` stops when `torrents.json.broken` exists; `write_json(durable=True)` only for the list and torrents.json (fsync on every save made CI's Windows tests time out); CSP allows `blob:` media/connect/worker (saved iPad videos, hls.js); `player.finished` two-minute rule only over 10 minutes; `_body` refuses negative lengths; `.wp-btn svg { pointer-events: none }`; `_piece` turns libtorrent's invalid-handle RuntimeError into TorrentError.
- **Testing**: `tests/live_app.py` finds `packaging/mpv/mpv.exe` itself (without mpv, Play opens the page in the real browser). The site sweep lives in the session scratchpad (`sweep_one.py`, `sweep.py` with SLOT_BASE for separate Chromium profiles, `sweep_sum.py`); at most 3 hidden browsers at once or results are wrong.

## Round 1.3.8 (2026-11-09): parties together, every server, a better finder

- **Party sync** (`party.py`): behind > 2 s seeks forward (FASTFORWARD), behind > 0.6 s plays at 1.05x; pressing play while the room plays seeks to the room (no doSeek sent); a step back within STALL_STEP while the player waits (`stalled` / `stall_seen` on player.Session from mpv's paused-for-cache, WebSession / PhoneSession from the phone's "stall") isn't a seek; LOOP_MAX jumps back in LOOP_WINDOW stop following them. The phone's player starts muted with "Tap for sound" when play() is refused (Safari autoplay).
- **Servers**: `webtools.SERVERS_JS` (every frame) reads a page's server lists (boxes named server/source/mirror/provider/embed, a "Server" heading's list, a <select>, a "Server" dropdown opened once) and reports them to SERVERS; `catch_video` returns "servers", `list_servers` only lists. `scrape.clicked_streams` makes them streams "click|<frame>|<label>|<kind>", pressed by CLICK_JS in that frame. `resolve(stream=..., remember=False)` finds one without making it the site's choice. `App.server_list / _check_servers / use_server / _other_site`; mpv: uosc `open-menu`/`update-menu` ("watchlist-servers", S, Ctrl+F, two controls buttons); phone: menu + "Other site"; Android: "Servers" dialog (`android.servers/use_server/other_site`).
- **Finder**: no Sub click for non-anime; `by_tmdb` address templates per site; fuzzy words and a softer year in `score`; MATCH_TO_PLAY 55 (25-55 asks); a guessed /watch/<id> only when its page isn't another show's or a 404; `find_search` (home page -> search link, two sample titles must both be found) and {slug}; <base href>; this title's own episode links over other cards; the page's "Play now" and *play-button* classes; MANIFEST_JS on Windows and Mac too; `webtools.trailer` by host (not "youtube" anywhere); `scrambled_video`; a page that played nothing has its listed servers tried; no second 30 s catch of the same page; 15 s when other servers exist.
- **Keys**: mpv UP/DOWN/wheel volume, - = audio delay, z x subtitle delay; phone player the same (no audio delay in a browser).
- Live tests: `tests/live_sites.py` checks each server of the first title; `python -m tests.live_sites x.json extra` runs `livecheck.EXTRA_SITES` (everythingmoe's lists). Nepu, AnimePahe, VidBox's player sit behind Cloudflare Turnstile; Gotaku's and ReAnime's videos are scrambled for their own players: not worked around.

## Round "Use on your phone" (2026-11-06)

Not built yet (exe/dmg/apk); everything committed on `claude/blissful-mendel-jghzf5`.

**Fixes first:** Home → Currently watching (`watching` on an item: set by any play in `Store.update` when `last_played` is in the patch; ⋯ → Where I am / take off; + Add one). Shared collections: `livecollections.py` (WLC3 link = a 16-byte key; the collection is a retained, encrypted MQTT message on the relay brokers; the sharer re-posts on change and every 6 h, followers look every 30 min; `follow_ids` so titles the follower took out stay out). Watch party Next after a Sub/Dub or other-source swap: `player.Session` now decides the episode from mpv's `path` (the swap latch stuck when mpv coalesced playlist-pos changes); the host is held on every episode change (`moved` events only). Extra files: also skips any file a title plays from (`_sources`); the user's report wasn't reproduced on a copy of their data.

**The phone (Parts 1-5):**
- `remote.py`: Access (listeners per LAN/Tailscale IP on `phone_port`, refreshed every 30 s; allowed Host names; `_answers` self-check spots a firewall), Devices (pair requests with 6-digit codes, rate limit 5 per 10 min per IP, `devices.json` keeps sha256 of the token), Awake (SetThreadExecutionState / caffeinate). `server.remote_listener` is Handler with `remote=True`; `_remote_gate` lets in only SHELL files, `/api/ping`, `/api/pair/*` and `/stream/web/*` without the `wa_device` cookie; LOCAL_ONLY routes. `_request` (thread-local) carries the device and turns `open_link` into `open_here`.
- `webplay.py`: Tickets (HMAC, 8 h max, 2 h idle), WebSession (same interface as player.Session; numbered commands, `wait()` for SSE), `rewrite()` for site HLS, Jobs (one per device, idle 2 min, 1 h, 5 GB). `transcode.py`: find/probe/plan/encoder/Job (HLS fMP4 with var_stream_map: video plus one rendition per audio track; WebVTT sidecars; `-ss` restarts reset timestamps with setpts/asetpts).
- Server: `web_media` (site: remote ticket; file: direct or job), `web_seek`, `/api/web/<sid>/{open,state,seek,source,version,events}`, `_web_stream` (file ranges, job files with `#EXT-X-START`, the remote proxy, subtitles). The play endpoints and party start/join/open branch on `_web_device()`; `party_device`.
- Front: `phone.js` (REMOTE, pairing and unreachable screens, the PC's Allow/Deny pop-up, the Settings card with QR codes, tab bar, Lists sheet, Add to Home Screen tip, service worker on https only, speed test), `player.js` (the phone's player; hls.js except on Apple's WebKit), `sw.js`, `manifest.webmanifest`, `icons/`.
- Testing here: Playwright (WebKit iPhone for layout; Edge at phone size for playback, since WebKit on Windows has no H.264), loopback 127.0.0.2/.3 as "the phone's address" (the user's Windows firewall blocks python.exe on Private networks: don't change it). A real iPhone's Safari wasn't tried.
- ffmpeg: `packaging/ffmpeg` (git-ignored); CI fetches BtbN 8.1 shared (Windows) and eugeneware b6.1.1 arm64 (Mac). The self-test reports `ffmpeg` and `phone`.


## Where we left off (2026-10-30)

Branch: `claude/blissful-mendel-jghzf5`. All work is committed and pushed. No pull request has been opened.

Latest builds:

| Platform | State | Run |
|---|---|---|
| Android (APK) | Phone player, torrents, watch parties. Built and emulator-checked before round 10. | [run 40896330674](https://github.com/zzzorezzz/Claude/actions/runs/40896330674) |
| Windows (exe) | Round 10, the ad blocker, video sites (streams, qualities, yt-dlp) and the Internet Archive source. Built, tests green. | [run 41033130778](https://github.com/zzzorezzz/Claude/actions/runs/41033130778) |
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

### Waiting for the next Windows build (run 41032153407 was cancelled by the user; build only when they ask)

- **Internet Archive source** (2026-10-30, commit after f98dbdd): "+ Add the Internet Archive" in Settings → Your video sites (site `kind: "archive"`, url `https://archive.org`, no `{title}` needed).
  - `scrape.archive_search` calls `advancedsearch.php` (`mediatype:movies`, sorted by downloads, with the year).
  - `scrape.archive_files` reads `/metadata/<id>` for the video files with their heights.
  - `archive_episodes` maps a series' files by episode number; `archive_versions` gives one copy per quality.
  - `Scraper._archive` plugs into `resolve`, so qualities, the play window and mpv work as for other sites.
  - `best_entry` now uses a result's year (another year −15; the right year wins ties).
  - Tried live against archive.org: Night of the Living Dead (1968) → its 1080p…240p files.
- Claude declined a generic "JSON API mode" shaped like AnimePahe's API; the Internet Archive was offered instead.
- Also not done, on purpose: making the hidden browser better at clicking through sub/dub server lists and capturing the video from embedded third-party players (aimed at unlicensed streaming sites). Don't extend the scraper in that direction.
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
