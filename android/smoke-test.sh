#!/usr/bin/env bash
# On the build server's Android emulator: install the APK, start it, and check
# the app's engine answers and its screen shows. Leaves screenshots behind.
set -u
adb install -r WatchAnything.apk
adb shell am start -n app.watchanything/.MainActivity
ok=0
for i in $(seq 120); do
  adb forward tcp:8765 tcp:8765 >/dev/null 2>&1
  if curl -sf --max-time 3 http://127.0.0.1:8765/api/ping; then ok=1; break; fi
  sleep 2
done
echo
sleep 15
adb exec-out screencap -p > android-home.png
if [ "$ok" = 1 ]; then
  curl -sf http://127.0.0.1:8765/ | head -c 200; echo
  curl -sf http://127.0.0.1:8765/api/state | head -c 400; echo
  # The settings screen too (opened by tapping where the gear is would be fragile: ask the page).
  adb shell input keyevent 82 || true
  timeout 600 bash android/torrent-test.sh || true
fi
timeout 30 adb logcat -d | grep -iE "python|chaquopy|jlibtorrent|libtorrent|watchanything|AndroidRuntime|FATAL" | tail -120
[ "$ok" = 1 ]
