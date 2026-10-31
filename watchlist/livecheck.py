"""The recommended sites' live check: every site, three titles (anime sites three anime,
the TV and film sites two TV shows and a film), each found the way the app finds it and
played for five seconds. On a computer: tests/live_sites.py (mpv); on a phone: run_phone()
(the phone's own player), started by the build server's emulator."""
import json
import threading
import time

SITES = [
    {"name": "AnikotoTV", "url": "https://anikototv.to/filter?keyword={title}", "types": ["anime"]},
    {"name": "AniZone", "url": "https://anizone.to/anime?search={title}", "types": ["anime"]},
    {"name": "Miruro", "url": "https://www.miruro.cx/search?query={title}&sort=-popularity", "types": ["anime"]},
    {"name": "Atlantic", "url": "https://atlantic.st/search?q={title}", "types": ["tv", "movie"]},
    {"name": "Bingr", "url": "https://bingr.one/explore?q={title}", "types": ["tv", "movie"]},
    {"name": "ShuttleTV", "url": "https://shuttletv.su/search?q={title}", "types": ["tv", "movie"]},
]
ANIME = [
    ({"title": "Death Note", "year": 2006}, 1),
    ({"title": "Bleach", "year": 2004}, 1),
    ({"title": "Naruto Shippuden", "aliases": ["Naruto: Shippuuden"], "year": 2007}, 1),
]
SHOWS = [
    ({"title": "Breaking Bad", "type": "tv", "source": "tmdb", "source_id": "tv/1396", "format": "series", "year": 2008,
      "seasons": [{"number": n, "episodes": 13} for n in range(1, 6)]}, (1, 1)),
    ({"title": "Game of Thrones", "type": "tv", "source": "tmdb", "source_id": "tv/1399", "format": "series", "year": 2011,
      "seasons": [{"number": n, "episodes": 10} for n in range(1, 9)]}, (4, 4)),
    ({"title": "Dune", "type": "movie", "source": "tmdb", "source_id": "movie/438631", "format": "movie", "year": 2021}, None),
]


def cases(site):
    """(title, (season, episode)) for a site."""
    if "anime" in site["types"]:
        return [(dict({"type": "anime", "source": "mal", "format": "series", "id": t["title"]}, **t), (None, e))
                for t, e in ANIME]
    return [(dict(t, id=t["title"]), se or (None, None)) for t, se in SHOWS]


def run_phone(report, only=()):
    """On a phone: each title found in the hidden WebView and played in the phone's player
    until it has played five seconds (or a minute went by). Writes the results to `report`
    as they come, then {"done": true} last."""
    threading.Thread(target=_run_phone, args=(report, list(only)), daemon=True).start()


def _run_phone(report, only):
    import tempfile
    from . import hlsfix, phone_player, scrape, webtools
    base = hlsfix.test_server()
    s = scrape.Scraper(tempfile.mkdtemp())
    s.render = lambda url: webtools.render(url)
    s.catch = lambda url, click=None: webtools.catch_video(url, click=click)
    rows = []

    def save(done=False):
        with open(report, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "done": done}, f, indent=1)
    for site in scrape.clean([dict(x) for x in SITES]):
        if only and site["name"] not in only:
            continue
        for item, (season, episode) in cases(site):
            t0 = time.time()
            row = {"site": site["name"], "title": item["title"],
                   "what": f"S{season}E{episode}" if season else (f"E{episode}" if episode else "film")}
            try:
                found = s.resolve(site, item, season, episode)
                entry = {"path": hlsfix.playable(found, base), "key": "check", "label": item["title"], "referer": found.get("referer"),
                         "headers": found.get("headers") or {}, "sub": None}
                session = phone_player.PhoneSession(item["id"], [entry], anime=item.get("type") == "anime").open()
                deadline, best = time.time() + 75, 0.0
                while time.time() < deadline and best < 5:
                    time.sleep(0.5)
                    best = max(best, session.position() or 0.0)
                session.stop()
                time.sleep(2)
                row.update(played=best >= 4.5, reached=round(best, 2), page=found.get("page"),
                           subtitle=bool(found.get("subtitle")), video=found["url"][:160],
                           why="" if best >= 4.5 else (session.error or "the player didn't get to 5 seconds"))
            except Exception as e:  # noqa: BLE001
                row.update(played=False, reached=0, why=str(e)[:200], log=s.log[-4:])
            row["secs"] = round(time.time() - t0)
            rows.append(row)
            save()
    save(done=True)
