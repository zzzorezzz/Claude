"""A stand-in for mpv in tests: speaks mpv's JSON IPC and pretends to play.

FAKE_MPV_PLAN: JSON list, one step per playlist entry:
  {"dur": 100, "to": 95, "eof": true}   play to 95s of 100, then the file ends
  {"dur": 100, "to": 40}                stop at 40s (the window is closed)
FAKE_MPV_ARGS: file to write the command line to.
"""
import json
import os
import socket
import sys
import time

args = sys.argv[1:]
if os.environ.get("FAKE_MPV_ARGS"):
    with open(os.environ["FAKE_MPV_ARGS"], "w") as fh:
        json.dump(args, fh)
address = next(a.split("=", 1)[1] for a in args if a.startswith("--input-ipc-server="))
start = 0
for a in args:
    if a.startswith("--start="):
        start = int(a.split("=", 1)[1])
        break
plan = json.loads(os.environ.get("FAKE_MPV_PLAN", "[]"))

server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(address)
server.listen(1)
server.settimeout(10)
conn, _ = server.accept()
conn.settimeout(0.5)
try:
    conn.recv(65536)  # the observe_property commands
except socket.timeout:
    pass


def send(msg):
    conn.sendall(json.dumps(msg).encode() + b"\n")


for i, step in enumerate(plan):
    send({"event": "property-change", "id": 3, "name": "playlist-pos", "data": i})
    send({"event": "property-change", "id": 2, "name": "duration", "data": step["dur"]})
    pos = start if i == 0 else 0
    while pos < step["to"]:
        pos = min(step["to"], pos + 10)
        send({"event": "property-change", "id": 1, "name": "time-pos", "data": pos})
        time.sleep(0.005)
    if step.get("eof"):
        send({"event": "end-file", "reason": "eof"})
conn.close()
server.close()
