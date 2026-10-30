# Live source verification handoff — 2026-10-30

Do not package a final EXE until the five requested titles are playback-tested
through the app window for both Sub and Dub where the site offers both tracks.

## Implemented

- The Settings screen has one custom online-site section. Each row has AP, AK,
  and RA checkboxes; it no longer supplies preconfigured sites.
- AP enables the AnimePahe JSON/session route; AK is page/host extraction; RA
  captures the normal browser player's media request before trying ambiguous
  mirror buttons.
- Browser capture now retains Cookie along with Referer, Origin, and User-Agent.
- AnikotoTV (`anikototv.to`) watch pages load their real catalog from
  `/ajax/episode/list/{data-anime-id}` with `X-Requested-With:
  XMLHttpRequest`. `_episodes` now reads this endpoint and generates canonical
  `/watch/{slug}/ep-{number}` URLs. This fixes the former six-mirror count.

## Live findings

- `https://animepahe.com/api?...` redirects to `.pw`, and the API currently
  returns Cloudflare “Just a moment” HTML even with curl_cffi Chrome TLS
  impersonation. The app now reports this explicitly instead of “no series.”
  A real app-window browser session may be required to pass the challenge.
- `https://anikototv.to/search?keyword={title}` is the current search shape.
  The XHR catalog resolved 37 Death Note, 366 Bleach, 500 Naruto Shippuden,
  and 24 2.5 Dimensional Seduction episode numbers. The requested Haikyuu
  Shiratorizawa season did not appear in the site's live results at test time.
- AnikotoTV's player/server links are JavaScript-only, so test them with RA
  enabled. Its page initially displays mirror controls; RA autoplay capture is
  deliberately attempted before clicking these.
- ReAnime static search works at `https://reanime.to/search?q={title}` but its
  player is JavaScript-driven; test it with RA enabled.

## Second pass (same day) — capture fixes

The first live run through the real WebView2 capture found nothing on any
site. Causes found and fixed:

- Injected scripts (autoplay kick, stream click, ad block) were registered
  asynchronously after the first page had started loading, so they never ran
  there. They now also run on `DOMContentLoaded` (each is run-once guarded).
- The request filter used the two-argument `AddWebResourceRequestedFilter`,
  which does not report requests from cross-origin iframes (where hosts such
  as megaplay.buzz play). It now passes `CoreWebView2WebResourceRequestSourceKinds.All`.
- Capture windows were `hidden=True`; pages then report `visibilityState`
  hidden and players never request their playlist. They now open off-screen
  (x/y = -10000) instead, so a "Finding the video…" window may flash in the
  taskbar.
- The click script matched the first `div` whose text *contained* the label
  (a page container). It now prefers exact text, then the smallest match.
- A Dub choice on an RA site was ignored (autoplay is Sub). Dub now clicks the
  Dub button, or for ReAnime loads `?lang=dub`.
- Caught `.vtt` captions are kept and given to MPV as `--sub-file` for Sub
  streams (not Dub). megaplay serves Sub as soft subtitles.
- AnikotoTV: the six "streams" were its report-form `<input>`s. The real
  server list comes from `/ajax/server/list?servers={data-ids}`; inputs are no
  longer stream buttons.
- ReAnime: episode links (`/watch/{slug}?ep=N`) carry a tooltip "N Sub · Dub"
  and were rejected as server buttons.

Live results (episode 1, hidden-window capture, no MPV on the test machine):

- AnikotoTV: all five titles resolve for Sub and Dub, Sub/Dub are different
  playlists, Sub has its `.vtt`. Master playlist, variant, and first segment
  return 200 outside the browser with the captured Referer/Origin.
- Haikyuu Shiratorizawa *is* on AnikotoTV as "Haikyu!! 3rd Season"; it is
  found through the MAL English alias. With only the long Japanese-style
  title, the matcher accepted "High School Agent" at 26% (threshold 25) —
  the threshold is loose.
- AnimePahe: Cloudflare check on every request. Not worked around; the user
  must pass it in the built-in browser.

## Verification completed

- `python -m tests.ci` was run after the final code changes and completed with
  no observed failures (platform-specific mpv/socket tests are skipped).
- Targeted tests cover the XHR catalog, mirror-index selection, cookie capture,
  RA autoplay capture, and AnimePahe Cloudflare diagnosis.

## Before final packaging

1. Start the application in its pywebview window (not `--no-open`).
2. Add each site manually, selecting AP/AK/RA as appropriate.
3. For each requested title/episode, confirm a real MPV session starts and
   verify both Sub and Dub where available.
4. Rebuild with PyInstaller including `--collect-all curl_cffi`, then run the
   self-test, player test, and endpoint smoke test.
