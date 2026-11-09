"""Live check of the recommended sites (not part of the test suite: it uses the internet).
Every site gets three titles - anime sites three anime, the TV and film sites two TV
shows and a film - found the way the app finds them (the hidden browser included) and
played for five seconds in mpv; the first title's other servers are each found and played too
(WATCHLIST_SERVERS=0 skips that). python -m tests.live_sites [report.json] [site names...]"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from watchlist import hlsfix, player, scrape, webtools
from watchlist.livecheck import ANIME, EXTRA_SITES, SHOWS, SITES, cases

POS_LUA = """local best = 0
mp.observe_property("time-pos", "number", function(_, v) if v and v > best then best = v end end)
mp.register_event("end-file", function() mp.msg.info("POS " .. string.format("%.2f", best)) end)
mp.observe_property("track-list/count", "number", function()
    local a = 0
    for _, t in ipairs(mp.get_property_native("track-list") or {}) do if t.type == "audio" then a = a + 1 end end
    mp.msg.info("AUDIO " .. a)
end)
"""


def play5(mpv, found, script):
    cmd = [mpv, "--no-config", "--vo=null", "--ao=null", "--length=5", "--msg-level=all=info,ffmpeg=error",
           f"--script={script}", "--network-timeout=30", "--demuxer-lavf-o-append=extension_picky=0"]
    if found.get("referer"):
        cmd.append(f"--referrer={found['referer']}")
    cmd += [f"--http-header-fields-append={k}: {v}" for k, v in (found.get("headers") or {}).items()
            if k in ("Origin", "Cookie")]
    ua = (found.get("headers") or {}).get("User-Agent")
    if ua:
        cmd.append(f"--user-agent={ua}")
    cmd.append(hlsfix.playable(found, BASE))      # (as the app gives it to the player)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return 0.0, "mpv timed out"
    out = p.stdout + p.stderr
    pos = [float(x.split("POS ")[1].split()[0]) for x in out.splitlines() if "POS " in x]
    audio = max([int(x.split("AUDIO ")[1].split()[0]) for x in out.splitlines() if "AUDIO " in x] or [0])
    if pos and max(pos) >= 4.5 and not audio:
        return max(pos), "no sound: the video has no audio track"
    return (max(pos) if pos else 0.0), ("" if pos and max(pos) >= 4.5 else [x for x in out.strip().splitlines() if "POS" not in x and "AUDIO" not in x][-12:])


BASE = ""


def check_servers(s, site, item, season, episode, found, mpv, script, most=8):
    """Each of the episode's other servers (as the app's Servers list does): found, then played."""
    out = []
    for x in s.episode_servers(found)[:most]:
        if (found.get("stream") or {}).get("id") == x["id"]:
            continue
        row = {"label": x["label"], "kind": x["kind"]}
        try:
            got = s.resolve(site, item, season, episode, stream=f"{x['id']}:{x['kind']}:{x['label']}", fresh=True, remember=False)
            reached, why = play5(mpv, got, script)
            row.update(played=reached >= 4.5 and not why, why=why, same=got["url"] == found["url"])
        except Exception as e:  # noqa: BLE001
            row.update(played=False, why=str(e)[:160])
        print("   server", json.dumps(row), flush=True)
        out.append(row)
    return out


def main(argv):
    global BASE
    BASE = hlsfix.test_server()
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
    s.servers = lambda url: webtools.list_servers(url)
    servers = os.environ.get("WATCHLIST_SERVERS", "1") != "0"
    rows = []
    extra = "extra" in only
    only = [x for x in only if x != "extra"]
    for site in scrape.clean([dict(x) for x in (EXTRA_SITES if extra else SITES)]):
        if only and site["name"] not in only:
            continue
        for item, (season, episode) in cases(site):
            t0 = time.time()
            row = {"site": site["name"], "title": item["title"],
                   "what": f"S{season}E{episode}" if season else (f"E{episode}" if episode else "film")}
            try:
                found = s.resolve(site, item, season, episode)
                row["find_secs"] = round(time.time() - t0, 1)
                reached, why = play5(mpv, found, script)
                row.update(played=reached >= 4.5 and not why, reached=round(reached, 2), page=found.get("page"),
                           subtitle=bool(found.get("subtitle")), why=why,
                           server=(found.get("stream") or {}).get("label") or "",
                           opened=((found.get("entry") or {}).get("name") or "")[:60])
                if servers and not any(r["site"] == site["name"] for r in rows):
                    row["servers"] = check_servers(s, site, item, season, episode, found, mpv, script)
            except Exception as e:  # noqa: BLE001
                row.update(played=False, reached=0, why=str(e)[:200], log=s.log[-4:])
            row["secs"] = round(time.time() - t0)
            rows.append(row)
            print(json.dumps(row), flush=True)
    ok = sum(r["played"] for r in rows)
    print(f"\n{ok} of {len(rows)} played for 5 seconds")
    for r in rows:
        print(f"{'OK  ' if r['played'] else 'FAIL'}  {r['site']:<10} {r['title']:<22} {r['what']:<6} "
              f"{r['reached']:>5}s  {'subs' if r.get('subtitle') else '    '}  found in {r.get('find_secs', '-')}s  "
              f"{r.get('page') or r.get('why')}")
        for x in r.get("servers") or []:
            print(f"        server {x['label']:<20} {'plays' if x['played'] else 'no: ' + str(x['why'])[:90]}")
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
