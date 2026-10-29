"""libtorrent on a phone: the same names torrents.py uses from libtorrent's
Python module, made from jlibtorrent (libtorrent built for Android, with a
Java interface), so the torrent code is the same on a phone as on a computer.

Only what the app uses is here. One difference: a piece is read straight from
the downloaded file (piece_bytes) rather than with read_piece and its alert,
whose data jlibtorrent doesn't hand to Java.
"""

import os
import threading
import time

from java import jarray, jclass, jint

_P = "com.frostwire.jlibtorrent.swig."
_lt = jclass(_P + "libtorrent")
_atp = jclass(_P + "add_torrent_params")
_alert = jclass(_P + "alert")
_session_handle = jclass(_P + "session_handle")
_error_code = jclass(_P + "error_code")
_flags_t = jclass(_P + "torrent_flags_t")
_pack = jclass(_P + "settings_pack")
_types = {kind: jclass(_P + "settings_pack$" + kind) for kind in ("string_types", "bool_types", "int_types")}
_TorrentInfo = jclass("com.frostwire.jlibtorrent.TorrentInfo")
_File = jclass("java.io.File")
_Vectors = jclass("com.frostwire.jlibtorrent.Vectors")
_th = jclass(_P + "torrent_handle")

__version__ = "jlibtorrent"
PIECES_FROM_DISK = True


# ---- bencoding (the .torrent files the app keeps)

class _Raw(bytes):
    """A bencoded value kept exactly as it came (an info dict: its hash depends on every byte)."""


def bdecode(data):
    data = bytes(data)
    value, end = _decode(data, 0)
    return value


def _decode(d, i):
    c = d[i:i + 1]
    if c == b"i":
        end = d.index(b"e", i)
        return int(d[i + 1:end]), end + 1
    if c == b"l":
        out, i = [], i + 1
        while d[i:i + 1] != b"e":
            v, i = _decode(d, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        out, i = {}, i + 1
        while d[i:i + 1] != b"e":
            k, i = _decode(d, i)
            start = i
            v, i = _decode(d, i)
            if k == b"info":
                v = _Raw(d[start:i])
            out[k] = v
        return out, i + 1
    colon = d.index(b":", i)
    n = int(d[i:colon])
    return d[colon + 1:colon + 1 + n], colon + 1 + n


def bencode(value):
    if isinstance(value, _Raw):
        return bytes(value)
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, (bytes, bytearray)):
        return b"%d:%s" % (len(value), bytes(value))
    if isinstance(value, (list, tuple)):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        items = sorted((k.encode() if isinstance(k, str) else k, v) for k, v in value.items())
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in items) + b"e"
    raise TypeError(f"can't bencode {type(value).__name__}")


# ---- flags and categories (plain numbers, as in libtorrent's Python module)

class torrent_flags:
    upload_mode = _lt.getUpload_mode().to_int()
    sequential_download = _lt.getSequential_download().to_int()
    paused = _lt.getPaused().to_int()
    auto_managed = _lt.getAuto_managed().to_int()


class alert_category:
    error = _alert.error_notification.to_int()
    status = _alert.status_notification.to_int()
    storage = _alert.storage_notification.to_int()


class options_t:
    delete_files = _session_handle.delete_files.to_int()


class file_storage:
    flag_pad_file = jclass(_P + "file_storage").flag_pad_file.to_int()


# ---- hashes

class _Hashes:
    def __init__(self, ih):
        self._ih = ih

    def get_best(self):
        return self._ih.get_best().to_hex()

    @property
    def v1(self):
        return self._ih.getV1().to_hex()


# ---- a torrent's files

class _Files:
    def __init__(self, fs):
        self._fs = fs
        self.n = fs.num_files()

    def num_files(self):
        return self.n

    def file_path(self, i):
        return self._fs.file_path(jint(i))

    def file_size(self, i):
        return self._fs.file_size(jint(i))

    def file_offset(self, i):
        return self._fs.file_offset(jint(i))

    def file_flags(self, i):
        return self._fs.file_flags(jint(i)).to_int()


class _Tracker:
    def __init__(self, url):
        self.url = url


class torrent_info:
    """From a .torrent file's path, its bytes, or what bdecode gave."""

    def __init__(self, source, _handle=None):
        self._handle = _handle
        if hasattr(source, "num_files") and hasattr(source, "files"):   # a jlibtorrent torrent_info
            self._ti = source
            return
        if isinstance(source, str):
            self._ti = _TorrentInfo(_File(source)).swig()
            return
        data = bencode(source) if isinstance(source, (dict, list)) else bytes(source)
        self._ti = _TorrentInfo(jarray("B")(data)).swig()

    def files(self):
        return _Files(self._ti.files())

    def name(self):
        return self._ti.name()

    def num_files(self):
        return self._ti.num_files()

    def piece_length(self):
        return self._ti.piece_length()

    def piece_size(self, piece):
        return self._ti.piece_size(jint(piece))

    def info_hashes(self):
        return _Hashes(self._ti.info_hashes())

    def trackers(self):
        v = self._ti.trackers()
        return [_Tracker(v.get(i).getUrl()) for i in range(v.size())]

    def metadata(self):
        """The info dict's bytes (from the torrent's resume data, which carries it)."""
        if self._handle is None:
            raise RuntimeError("no metadata")
        atp = self._handle.get_resume_data(_th.save_info_dict)
        data = bytes(_Vectors.byte_vector2bytes(_atp.write_resume_data_buf(atp)))
        info = bdecode(data).get(b"info")
        if not info:
            raise RuntimeError("no metadata")
        return bytes(info)


# ---- what you add

class add_torrent_params:
    def __init__(self, atp=None):
        self._p = atp if atp is not None else _atp.create_instance()
        self._ti = None

    @property
    def ti(self):
        return self._ti

    @ti.setter
    def ti(self, value):
        self._ti = value
        self._p.set_ti(value._ti)

    @property
    def flags(self):
        return self._p.getFlags().to_int()

    @flags.setter
    def flags(self, value):
        self._p.setFlags(_flags_t.from_int(jint(value & 0x7FFFFFFF)))

    @property
    def save_path(self):
        return self._p.getSave_path()

    @save_path.setter
    def save_path(self, value):
        self._p.setSave_path(value)

    @property
    def name(self):
        return self._p.getName()

    @property
    def file_priorities(self):
        return list(_Vectors.byte_vector2bytes(self._p.get_file_priorities()))

    @file_priorities.setter
    def file_priorities(self, prio):
        self._p.set_file_priorities2(_Vectors.bytes2byte_vector(jarray("B")(bytes(int(x) for x in prio))))

    @property
    def info_hashes(self):
        if self._ti is not None:
            return self._ti.info_hashes()
        return _Hashes(self._p.getInfo_hashes())


def parse_magnet_uri(uri):
    ec = _error_code()
    atp = _atp.parse_magnet_uri(uri, ec)
    if ec.value() != 0:
        raise ValueError(ec.message())
    return add_torrent_params(atp)


# ---- a torrent being downloaded

class _Status:
    def __init__(self, st):
        self.has_metadata = st.getHas_metadata()
        self.num_complete = st.getNum_complete()
        self.num_incomplete = st.getNum_incomplete()
        self.list_seeds = st.getList_seeds()
        self.list_peers = st.getList_peers()
        self.num_seeds = st.getNum_seeds()
        self.num_peers = st.getNum_peers()
        self.download_rate = st.getDownload_rate()
        self.upload_rate = st.getUpload_rate()
        self.total_wanted = st.getTotal_wanted()
        self.total_wanted_done = st.getTotal_wanted_done()
        self.total_done = st.getTotal_done()
        self.progress = st.getProgress()
        self.is_seeding = st.getIs_seeding()
        self.is_finished = st.getIs_finished()
        self.state = str(st.getState())


class _Handle:
    def __init__(self, h):
        self._h = h

    def __eq__(self, other):
        return isinstance(other, _Handle) and self._h.op_eq(other._h)

    def __hash__(self):
        return hash(self.info_hashes().get_best())

    def is_valid(self):
        return self._h.is_valid()

    def status(self):
        return _Status(self._h.status())

    def info_hashes(self):
        return _Hashes(self._h.info_hashes())

    def torrent_file(self):
        ti = self._h.torrent_file_ptr()
        if ti is None or not ti.is_valid():
            return None
        return torrent_info(ti, _handle=self._h)

    def file_progress(self):
        v = jclass(_P + "int64_vector")()
        self._h.file_progress(v)
        return list(_Vectors.int64_vector2longs(v))

    def file_priority(self, index, priority=None):
        if priority is None:
            return self._h.file_priority2(jint(index))
        self._h.file_priority2(jint(index), jint(priority))

    def prioritize_files(self, prio):
        self._h.prioritize_files2(jclass(_P + "int_vector")(jarray("I")([int(x) for x in prio])))

    def set_flags(self, flags):
        self._h.set_flags(_flags_t.from_int(jint(flags)))

    def unset_flags(self, flags):
        self._h.unset_flags(_flags_t.from_int(jint(flags)))

    def pause(self):
        self._h.pause()

    def resume(self):
        self._h.resume()

    def have_piece(self, piece):
        return self._h.have_piece(jint(piece))

    def set_piece_deadline(self, piece, ms):
        self._h.set_piece_deadline(jint(piece), jint(ms))

    def clear_piece_deadlines(self):
        self._h.clear_piece_deadlines()

    def scrape_tracker(self):
        self._h.scrape_tracker()

    def connect_peer(self, endpoint):
        host, port = endpoint
        ec = _error_code()
        addr = jclass(_P + "address").from_string(host, ec)
        if ec.value() != 0:
            raise ValueError(ec.message())
        self._h.connect_peer(jclass(_P + "tcp_endpoint")(addr, jint(port)))

    def read_piece(self, piece):
        raise RuntimeError("use piece_bytes on a phone")


_file_maps = {}
_file_maps_lock = threading.Lock()


def piece_bytes(handle, piece, save_path):
    """A downloaded piece, read from the files it's part of (zeros where a file isn't kept)."""
    ti = handle.torrent_file()
    key = handle.info_hashes().get_best()
    with _file_maps_lock:
        layout = _file_maps.get(key)
        if layout is None:
            fs = ti.files()
            layout = [(fs.file_offset(i), fs.file_size(i), fs.file_path(i)) for i in range(fs.num_files())]
            _file_maps[key] = layout
    start = piece * ti.piece_length()
    end = start + ti.piece_size(piece)
    out = bytearray(end - start)
    for offset, size, path in layout:
        a, b = max(start, offset), min(end, offset + size)
        if a >= b:
            continue
        try:
            with open(os.path.join(save_path, path), "rb") as f:
                f.seek(a - offset)
                chunk = f.read(b - a)
        except OSError:
            continue
        out[a - start:a - start + len(chunk)] = chunk
    return bytes(out)


# ---- alerts (only the kinds the app looks at; the rest come as a plain alert)

class alert:
    def __init__(self, a):
        self._a = a

    def message(self):
        return self._a.message()


class read_piece_alert(alert):
    pass


class external_ip_alert(alert):
    def __init__(self, a):
        super().__init__(a)
        self.external_address = _alert.cast_to_external_ip_alert(a).get_external_address().to_string()


class _torrent_alert(alert):
    def __init__(self, a, cast):
        super().__init__(a)
        self.handle = _Handle(cast(a).getHandle())


class torrent_error_alert(_torrent_alert):
    def __init__(self, a):
        super().__init__(a, _alert.cast_to_torrent_error_alert)


class file_error_alert(_torrent_alert):
    def __init__(self, a):
        super().__init__(a, _alert.cast_to_file_error_alert)


_ALERTS = {
    jclass(_P + "external_ip_alert").alert_type: external_ip_alert,
    jclass(_P + "torrent_error_alert").alert_type: torrent_error_alert,
    jclass(_P + "file_error_alert").alert_type: file_error_alert,
}


# ---- the session

class session:
    def __init__(self, settings):
        pack = _pack()
        for k, v in settings.items():
            for kind, cls in _types.items():
                name = getattr(cls, k, None)
                if name is None:
                    continue
                if kind == "string_types":
                    pack.set_str(name.swigValue(), str(v))
                elif kind == "bool_types":
                    pack.set_bool(name.swigValue(), bool(v))
                else:
                    pack.set_int(name.swigValue(), jint(int(v)))
                break
        self._s = jclass(_P + "session")(jclass(_P + "session_params")(pack))
        self._alerts = jclass(_P + "alert_ptr_vector")()

    def add_torrent(self, p):
        ec = _error_code()
        h = self._s.add_torrent(p._p, ec)
        if ec.value() != 0:
            raise RuntimeError(ec.message())
        return _Handle(h)

    def remove_torrent(self, handle, flags=0):
        if flags:
            self._s.remove_torrent(handle._h, jclass(_P + "remove_flags_t").from_int(jint(flags)))
        else:
            self._s.remove_torrent(handle._h)

    def listen_port(self):
        return self._s.listen_port()

    def wait_for_alert(self, ms):
        return self._s.wait_for_alert_ms(ms)

    def pop_alerts(self):
        self._alerts.clear()
        self._s.pop_alerts(self._alerts)
        out = []
        for i in range(self._alerts.size()):
            a = self._alerts.get(i)
            kind = _ALERTS.get(a.type())
            try:
                out.append(kind(a) if kind else alert(a))
            except Exception:  # noqa: BLE001 - an alert we couldn't read is skipped
                continue
        return out
