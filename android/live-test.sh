#!/usr/bin/env bash
# On the emulator, after smoke-test.sh: the recommended sites' live check on the phone
# (watchlist/livecheck.py) - each site, three titles, found in the app's hidden WebView
# and played in its own player for five seconds. Leaves live-report-android.json behind.
# $1: site names (empty: all).
set -u
only="${1:-all}"
adb shell am force-stop app.watchanything
adb shell am start -n app.watchanything/.MainActivity --es live "$only"
for i in $(seq 900); do
  sleep 4
  adb exec-out run-as app.watchanything cat files/live-report.json > live-report-android.json 2>/dev/null || true
  if python3 -c 'import json,sys; sys.exit(0 if json.load(open("live-report-android.json")).get("done") else 1)' 2>/dev/null; then break; fi
  if [ $((i % 30)) = 0 ]; then adb exec-out screencap -p > "android-live-$i.png"; fi
done
python3 - <<'PY'
import json
r = json.load(open("live-report-android.json"))
rows = r["rows"]
ok = sum(x["played"] for x in rows)
print(f"\n{ok} of {len(rows)} played for 5 seconds on the phone" + ("" if r.get("done") else " (not finished)"))
for x in rows:
    print(f"{'OK  ' if x['played'] else 'FAIL'}  {x['site']:<10} {x['title']:<22} {x['what']:<6} {x['reached']:>5}s  "
          f"{'subs' if x.get('subtitle') else '    '}  {x.get('page') or ''}  {x.get('why') or ''}")
PY
adb logcat -d | grep -iE "python.stderr|watchanything|AndroidRuntime|ExoPlayer" | tail -60
