"""Shared collections that stay up to date.

A collection shared this way has one link that never changes ("WLC3." and a key). The
sharer's app keeps the collection's latest version on the same free public relays the watch
party's backup uses (relay.py), encrypted with that key: the relays keep bytes they can't
read, and only someone with the link can read them or put a version there. Whoever added
the collection from the link (or a file holding it) has their app look now and then, and
titles added to it (or taken out) are added (or taken out) for them too.

The relays keep only the latest version, and only while they're up: the sharer's app puts it
back whenever it changes and every few hours while it's open.
"""

import base64
import json
import queue
import re
import secrets
import threading
import time
import zlib

from . import relay

PREFIX = "WLC3."
WAIT = 8                       # seconds a relay has to answer


class LiveError(Exception):
    pass


def new_key():
    return secrets.token_bytes(16)


def to_code(key: bytes):
    return PREFIX + base64.urlsafe_b64encode(key).decode().rstrip("=")


def read_code(text):
    """The key in a link, or None when there isn't one."""
    m = re.search(re.escape(PREFIX) + r"([A-Za-z0-9_-]{22})(?![A-Za-z0-9_-])", text or "")
    if not m:
        return None
    try:
        key = base64.urlsafe_b64decode(m.group(1) + "==")
    except ValueError:
        return None
    return key if len(key) == 16 else None


def key_text(key: bytes):
    return base64.urlsafe_b64encode(key).decode().rstrip("=")


def key_bytes(text):
    return base64.urlsafe_b64decode(str(text) + "=" * (-len(str(text)) % 4))


def _topic(key):
    return relay.Box(key).topic + "/collection"


def _each_relay(job, brokers=None):
    """`job(broker)` on every relay at once; what each gave back (None when it failed)."""
    brokers = list(brokers or relay.BROKERS)
    out = queue.Queue()

    def run(b):
        try:
            out.put(job(b))
        except Exception:  # noqa: BLE001 - that relay is down or refused: the others count
            out.put(None)
    for b in brokers:
        threading.Thread(target=run, args=(b,), daemon=True).start()
    results = []
    for _ in brokers:
        try:
            results.append(out.get(timeout=WAIT + 4))
        except queue.Empty:
            results.append(None)
    return results


def publish(key, data, brokers=None):
    """Keep this version of the collection on every relay that takes it. How many did."""
    blob = relay.Box(key).seal(zlib.compress(json.dumps({"v": 1, "at": time.time(), "data": data},
                                                        ensure_ascii=False).encode(), 9))
    topic = _topic(key)

    def one(broker):
        conn = relay.Mqtt(broker, [], lambda *a: None, timeout=WAIT)
        try:
            conn.publish(topic, blob, retain=True)
            return conn.acked.wait(WAIT)
        finally:
            conn.close()
    return sum(1 for r in _each_relay(one, brokers) if r)


def fetch(key, brokers=None):
    """The newest version any relay has: {"at", "data"}. Raises LiveError when none has one."""
    box, topic = relay.Box(key), _topic(key)

    def one(broker):
        got = queue.Queue()
        conn = relay.Mqtt(broker, [topic], lambda t, payload: got.put(payload), timeout=WAIT)
        try:
            blob = got.get(timeout=WAIT)
        except queue.Empty:
            return None
        finally:
            conn.close()
        raw = box.open(blob)
        if raw is None:
            return None
        version = json.loads(zlib.decompress(raw, bufsize=1 << 20))
        return version if isinstance(version, dict) and isinstance(version.get("data"), dict) else None
    found = [r for r in _each_relay(one, brokers) if r]
    if not found:
        raise LiveError("the shared collection couldn't be found right now - the app it's shared from puts it "
                        "back while it's open, so try again later")
    return max(found, key=lambda v: v.get("at") or 0)
