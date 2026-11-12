"""Live check of the whole app on the recommended sites (not part of the test suite: it uses the
internet) - as you use it: an episode played in the player (and still playing 20 seconds on), the
Servers list opened and another server chosen (a dub where there is one) and played from where it
was, then the app quit, started again on the same data and the next episode played.
python -m tests.live_app [report.json] [site names...]"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from watchlist import cdp, player, server, store
from watchlist.livecheck import SITES, cases

_command = player.Session.command


def _quiet(self):
    # (no picture or sound wanted: a build machine has neither)
    cmd = _command(self)
    return cmd[:1] + ["--vo=null", "--ao=null", "--force-window=no"] + [c for c in cmd[1:] if not c.startswith("--force-window")]


player.Session.command = _quiet


def _playing(session, secs):
    """Seconds until the player is past its first second (None: it never was)."""
    t = time.time()
    while time.time() - t < secs:
        if session.ended:
            return None
        if session.pos and session.pos > 1:
            return round(time.time() - t, 1)
        time.sleep(0.5)
    return None


def _goes_on(session, secs=20):
    """Seconds played in `secs` (a video that stalls plays few)."""
    start = session.pos or 0
    time.sleep(secs)
    return round((session.pos or 0) - start, 1)


def _app(data):
    app = server.App(data)
    srv = server.make_server(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return app, srv


def _close(app, srv):
    s = app.player.session
    if s:
        s.stop()
    app.shutdown()
    srv.shutdown()


def one_site(site, root):
    data = Path(root) / site["name"]
    title, (season, episode) = cases(site)[0]
    row = {"site": site["name"], "title": title["title"]}
    series = title.get("format") == "series"
    app, srv = _app(data)
    try:
        app.settings.update({"video_sites": [site]})
        kind = title.get("type") or "anime"
        meta = {"source": title.get("source") or "mal", "source_id": title.get("source_id") or "1", "title": title["title"],
                "format": title.get("format") or "series", "year": title.get("year"),
                "seasons": title.get("seasons") or ([{"number": 1, "episodes": 30}] if series else [])}
        item = app.store.add(store.new_item(kind, meta))
        t = time.time()
        body = {"from_start": True}
        if series:
            body.update(season=season or 1, episode=episode or 1)
        res = app.play_scraped(app.store.get(item["id"]), body)
        row["found_secs"] = round(time.time() - t, 1)
        s = app.player.session
        if not res.get("ok") or not s:
            row["why"] = res.get("error") or "nothing played"
            return row
        row["started_in"] = _playing(s, 90)
        row["played_20s"] = _goes_on(s) if row["started_in"] is not None else 0
        # The Servers list, as the player's menu shows it; another server chosen (a dub first).
        t = time.time()
        got = app.server_list(s)
        while time.time() - t < 120:
            got = app.server_list(s)
            if any(not x["on"] and x["state"] == "ok" for x in got["items"]) or not got["checking"]:
                break
            time.sleep(2)
        row["servers"] = [f"{x['label']}{' (' + x['kind'] + ')' if x['kind'] else ''}{' *' if x['on'] else ''}:{x['state']}"
                          for x in got["items"]]
        row["servers_secs"] = round(time.time() - t, 1)
        ready = [x for x in got["items"] if not x["on"] and x["state"] in ("ok", "checking", "unchecked")
                 and not x["id"].startswith("site|")]
        pick = next((x for x in ready if x["kind"] == "dub"), None) or (ready[0] if ready else None)
        if pick:
            before = s.pos or 0
            t = time.time()
            app.use_server(s, pick["id"])
            row["switched_to"] = pick["label"] + (f" ({pick['kind']})" if pick["kind"] else "")
            # (playing again: past where it was, or on from wherever the new copy starts)
            while time.time() - t < 90 and not s.ended:
                if s.loaded and s.pos and abs(s.pos - before) < 30 and time.time() - t > 3:
                    break
                time.sleep(0.5)
            row["switch_secs"] = round(time.time() - t, 1)
            row["switch_played_20s"] = _goes_on(s)
        _close(app, srv)
        # Quit and started again: the next episode (or the film again).
        app, srv = _app(data)
        t = time.time()
        if series:
            body.update(episode=(episode or 1) + 1)
        res = app.play_scraped(app.store.get(item["id"]), body)
        s = app.player.session
        row["again_found_secs"] = round(time.time() - t, 1)
        row["again_started_in"] = _playing(s, 90) if res.get("ok") and s else None
        row["again_played_20s"] = _goes_on(s) if row["again_started_in"] is not None else 0
    except Exception as e:  # noqa: BLE001 - told in the report
        row["why"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        try:
            _close(app, srv)
        except Exception:  # noqa: BLE001
            pass
    return row


def ok(row):
    return (not row.get("why") and row.get("started_in") is not None and row.get("played_20s", 0) >= 10
            and (not row.get("switched_to") or row.get("switch_played_20s", 0) >= 10)
            and row.get("again_started_in") is not None and row.get("again_played_20s", 0) >= 10)


def main(argv):
    report = argv[0] if argv and argv[0].endswith(".json") else ""
    only = [a for a in argv if not a.endswith(".json")]
    root = tempfile.mkdtemp()
    rows = []
    for site in SITES:
        if only and site["name"] not in only:
            continue
        row = one_site(dict(site), root)
        row["ok"] = ok(row)
        rows.append(row)
        print(json.dumps(row), flush=True)
    print(f"\n{sum(r['ok'] for r in rows)} of {len(rows)} sites played, switched server and played again after a restart")
    if report:
        with open(report, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
    cdp.shutdown()
    shutil.rmtree(root, ignore_errors=True)
    return 0 if rows and all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    if sys.platform == "win32" and os.environ.get("WATCHLIST_CDP") != "1":
        # (Windows finds videos in hidden Edge windows: they need pywebview running)
        import webview
        from watchlist import launcher
        webview.create_window("Watch Anything", html="<p></p>", hidden=True)
        launcher._webview = webview
        webview.start(lambda: threading.Thread(target=lambda: os._exit(main(sys.argv[1:])), daemon=True).start(),
                      private_mode=True)
    os._exit(main(sys.argv[1:]))
