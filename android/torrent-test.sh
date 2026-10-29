#!/usr/bin/env bash
# On the emulator, with the app running: stream a free film (Big Buck Bunny,
# Creative Commons) from its torrent through the phone's torrent engine.
# Prints "TORRENT TEST: OK" when bytes of the video came through.
set -u
API=http://127.0.0.1:8765
H='-H X-Watchlist:1 -H Content-Type:application/json'
MAGNET='magnet:?xt=urn:btih:dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c&dn=Big+Buck+Bunny&tr=udp%3A%2F%2Fexplodie.org%3A6969&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337&tr=udp%3A%2F%2Ftracker.empire-js.us%3A1337&tr=udp%3A%2F%2Ftracker.leechers-paradise.org%3A6969&tr=wss%3A%2F%2Ftracker.btorrent.xyz&tr=wss%3A%2F%2Ftracker.openwebtorrent.com&ws=https%3A%2F%2Fwebtorrent.io%2Ftorrents%2F'
item=$(curl -sf $H -X POST $API/api/items -d '{"type":"movie","source":"tmdb","source_id":"movie/10378","hint":{"title":"Big Buck Bunny","year":2008}}')
id=$(echo "$item" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "item $id"
python3 - "$MAGNET" > /tmp/magnet.json <<'PY'
import json, sys; print(json.dumps({"magnet": sys.argv[1]}))
PY
curl -s $H -X POST "$API/api/items/$id/torrents" --data @/tmp/magnet.json | head -c 600; echo
files=""
for i in $(seq 60); do
  view=$(curl -s $H "$API/api/torrents")
  files=$(echo "$view" | python3 -c 'import json,sys; t=json.load(sys.stdin)["torrents"]; print(len(t[0]["files"]) if t else 0)')
  [ "$files" != "0" ] && break
  sleep 3
done
echo "files: $files"
echo "$view" | head -c 1500; echo
local=$(curl -s $H "$API/api/items/$id/local")
url=$(echo "$local" | python3 -c '
import json, sys
def walk(v):
    if isinstance(v, str) and "/stream/" in v: yield v
    elif isinstance(v, dict):
        for x in v.values(): yield from walk(x)
    elif isinstance(v, list):
        for x in v: yield from walk(x)
print(next(walk(json.load(sys.stdin)), ""))')
echo "stream: $url"
if [ -n "$url" ]; then
  python3 - "$url" > /tmp/buf.json <<'PY'
import json, sys; print(json.dumps({"path": sys.argv[1], "from_start": True}))
PY
  for i in $(seq 20); do
    curl -s $H -X POST "$API/api/items/$id/stream-buffer" --data @/tmp/buf.json | head -c 300; echo
    sleep 3
  done
  got=$(curl -s --max-time 120 -r 0-1048575 "$url" | wc -c)
  echo "bytes streamed: $got"
  if [ "$got" -gt 100000 ]; then
    echo "TORRENT TEST: OK"
    # A watch party hosted on the phone, playing the film: the phone player opens
    # by itself and the party hears from it.
    curl -s --max-time 120 $H -X POST "$API/api/party/start" -d "{\"item_id\": \"$id\"}" | head -c 600; echo
    for i in $(seq 30); do
      st=$(curl -s $H "$API/api/party")
      echo "$st" | grep -q '"host_playing": "[^"]' && break
      sleep 2
    done
    echo "$st" | head -c 800; echo
    sleep 8
    adb exec-out screencap -p > android-party-player.png
    echo "$st" | grep -q '"host_playing": "[^"]' && echo "PARTY TEST: OK" || echo "PARTY TEST: FAILED"
    curl -s $H -X POST "$API/api/party/leave" | head -c 200; echo
    exit 0
  fi
fi
echo "TORRENT TEST: FAILED"
exit 1
