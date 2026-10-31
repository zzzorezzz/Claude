#!/usr/bin/env bash
# On the emulator, after smoke-test.sh: the recommended sites' live check on the phone
# (watchlist/livecheck.py) - each site, three titles, found in the app's hidden WebView
# and played in its own player for five seconds. Leaves live-report-android.json behind.
# $1: site names (empty: all).
set -u
only="${1:-all}"
adb shell am force-stop app.watchanything
adb shell am start -n app.watchanything/.MainActivity --es live "$only"
quiet=0
last=""
for i in $(seq 700); do
  sleep 4
  timeout 20 adb exec-out run-as app.watchanything cat files/live-report.json > live-report-android.json 2>/dev/null || true
  if python3 -c 'import json,sys; sys.exit(0 if json.load(open("live-report-android.json")).get("done") else 1)' 2>/dev/null; then break; fi
  now=$(md5sum live-report-android.json 2>/dev/null | cut -c1-32)
  if [ "$now" = "$last" ]; then quiet=$((quiet + 4)); else quiet=0; last="$now"; fi
  # (nothing new for 8 minutes: something's stuck - stop and show why)
  if [ "$quiet" -ge 480 ]; then echo "LIVE TEST: no progress for 8 minutes"; break; fi
  if [ $((i % 30)) = 0 ]; then timeout 20 adb exec-out screencap -p > "android-live-$i.png"; python3 -c 'import json; r=json.load(open("live-report-android.json")); print(len(r["rows"]), "done so far")' 2>/dev/null; fi
done
[ -s live-report-android.json ] || echo '{"rows": [], "done": false}' > live-report-android.json
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
timeout 30 adb logcat -d | grep -iE "python.std|AndroidRuntime|ExoPlayer|FATAL" | tail -120
