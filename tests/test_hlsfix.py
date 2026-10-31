import unittest
from unittest import mock

from watchlist import hlsfix

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
TS = b"".join(b"\x47" + bytes([i % 256]) * 187 for i in range(5))


class HlsFixTest(unittest.TestCase):
    def test_a_picture_in_front_of_the_video_is_cut_off(self):
        sid = hlsfix.local_url("http://127.0.0.1:1/hls/t", "https://cdn.example/a.m3u8", {}, "https://site.example/").split("/")[5]
        with mock.patch.object(hlsfix, "_get", return_value=PNG + TS):
            kind, body = hlsfix.serve(sid, "https://img.example/x.image", "http://127.0.0.1:1/hls/t")
        self.assertEqual(kind, "video/mp2t")
        self.assertEqual(body, TS)

    def test_playlists_point_back_at_the_app(self):
        base = "http://127.0.0.1:1/hls/t"
        sid = hlsfix.local_url(base, "https://cdn.example/a/index.m3u8", {}, "").split("/")[5]
        text = b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:4,\nseg1.image?x=1\n'
        with mock.patch.object(hlsfix, "_get", return_value=text):
            kind, body = hlsfix.serve(sid, "https://cdn.example/a/index.m3u8", base)
        lines = body.decode().splitlines()
        self.assertEqual(kind, "application/vnd.apple.mpegurl")
        self.assertTrue(lines[3].startswith(f"{base}/{sid}/v.ts?u=https%3A%2F%2Fcdn.example%2Fa%2Fseg1.image"))
        self.assertIn('URI="http://127.0.0.1:1/hls/t/', lines[1])

    def test_an_ordinary_video_is_left_alone(self):
        with mock.patch.object(hlsfix, "_first_piece", return_value="https://cdn2.example/s.ts"), \
                mock.patch.object(hlsfix, "_get", return_value=TS):
            self.assertFalse(hlsfix.needs_fix("https://cdn2.example/m.m3u8", {}, ""))
        with mock.patch.object(hlsfix, "_first_piece", return_value="https://cdn3.example/s.image"), \
                mock.patch.object(hlsfix, "_get", return_value=PNG + TS):
            self.assertTrue(hlsfix.needs_fix("https://cdn3.example/m.m3u8", {}, ""))


if __name__ == "__main__":
    unittest.main()
