"""Live check of the recommended sites (not part of the test suite: it uses the internet).
Every site gets three titles - anime sites three anime, the TV and film sites two TV
shows and a film - found the way the app finds them (the hidden browser included) and
played for five seconds in mpv. python -m tests.live_sites [report.json] [site names...]"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from watchlist import player, scrape, webtools

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
POS_LUA = """local best = 0
mp.observe_property("time-pos", "number", function(_, v) if v and v > best then best = v end end)
mp.register_event("end-file", function() mp.msg.info("POS " .. string.format("%.2f", best)) end)
"""


def play5(mpv, found, script):
    cmd = [mpv, "--no-config", "--vo=null", "--ao=null", "--length=5", "--msg-level=all=info,ffmpeg=error",
           f"--script={script}", "--network-timeout=30"]
    if found.get("referer"):
        cmd.append(f"--referrer={found['referer']}")
    cmd += [f"--http-header-fields-append={k}: {v}" for k, v in (found.get("headers") or {}).items()
            if k in ("Origin", "Cookie")]
    ua = (found.get("headers") or {}).get("User-Agent")
    if ua:
        cmd.append(f"--user-agent={ua}")
    cmd.append(found["url"])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return 0.0, "mpv timed out"
    out = p.stdout + p.stderr
    pos = [float(x.split("POS ")[1].split()[0]) for x in out.splitlines() if "POS " in x]
    return (max(pos) if pos else 0.0), ("" if pos and max(pos) >= 4.5 else [x for x in out.strip().splitlines() if "POS" not in x][-12:])


def main(argv):
    report = argv[0] if argv and argv[0].endswith(".json") else ""
    only = [a for a in argv if not a.endswith(".json")]
    mpv = os.environ.get("WATCHLIST_MPV") or player.find_mpv() or shutil.which("mpv")
    if not mpv:
        print("no mpv")
        return 2
    script = os.path.join(tempfile.mkdtemp(), "pos.lua")
    with open(script, "w", encoding="utf-8") as f:
        f.write(POS_LUA)
    s = scrape.Scraper(tempfile.mkdtemp())
    s.render = lambda url: webtools.render(url)
    s.catch = lambda url, click=None: webtools.catch_video(url, click=click)
    rows = []
    for site in scrape.clean([dict(x) for x in SITES]):
        if only and site["name"] not in only:
            continue
        cases = ([(dict({"type": "anime", "source": "mal", "format": "series"}, **t), (None, e)) for t, e in ANIME]
                 if "anime" in site["types"] else [(t, se or (None, None)) for t, se in SHOWS])
        for item, (season, episode) in cases:
            item = dict(item, id=item["title"])
            t0 = time.time()
            row = {"site": site["name"], "title": item["title"],
                   "what": f"S{season}E{episode}" if season else (f"E{episode}" if episode else "film")}
            try:
                found = s.resolve(site, item, season, episode)
                reached, why = play5(mpv, found, script)
                row.update(played=reached >= 4.5, reached=round(reached, 2), page=found.get("page"),
                           subtitle=bool(found.get("subtitle")), why=why)
            except Exception as e:  # noqa: BLE001
                row.update(played=False, reached=0, why=str(e)[:200], log=s.log[-4:])
            row["secs"] = round(time.time() - t0)
            rows.append(row)
            print(json.dumps(row), flush=True)
    ok = sum(r["played"] for r in rows)
    print(f"\n{ok} of {len(rows)} played for 5 seconds")
    for r in rows:
        print(f"{'OK  ' if r['played'] else 'FAIL'}  {r['site']:<10} {r['title']:<22} {r['what']:<6} "
              f"{r['reached']:>5}s  {'subs' if r.get('subtitle') else '    '}  {r.get('page') or r.get('why')}")
    if report:
        with open(report, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
    try:
        from watchlist import cdp
        cdp.shutdown()
    except Exception:  # noqa: BLE001
        pass
    return 0 if rows and ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
