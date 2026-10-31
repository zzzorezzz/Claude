"""Diagnosis (not a test): which mpv settings play AnikotoTV's chunks (a picture's
header first, then the video) on this computer. python -m tests.diag_hls"""
import subprocess
import sys
import tempfile

from watchlist import cdp, player, scrape, webtools
from watchlist.livecheck import SITES, cases

VARIANTS = {
    "plain": [],
    "picky0": ["--demuxer-lavf-o-append=extension_picky=0"],
    "picky0+all": ["--demuxer-lavf-o-append=extension_picky=0", "--demuxer-lavf-o-append=allowed_segment_extensions=ALL"],
    "picky0+probe": ["--demuxer-lavf-o-append=extension_picky=0", "--demuxer-lavf-probesize=10000000",
                     "--demuxer-lavf-analyzeduration=10"],
}


def main():
    mpv = player.find_mpv() or "mpv"
    print(subprocess.run([mpv, "--version"], capture_output=True, text=True).stdout)
    s = scrape.Scraper(tempfile.mkdtemp())
    s.render = lambda u: webtools.render(u)
    s.catch = lambda u, click=None: webtools.catch_video(u, click=click)
    site = scrape.clean([dict(SITES[0])])[0]
    item, (se, ep) = cases(site)[0]
    f = s.resolve(site, item, se, ep)
    h = f.get("headers") or {}
    base = [mpv, "--no-config", "--vo=null", "--ao=null", "--length=3", "-v", f"--referrer={f['referer']}",
            f"--user-agent={h.get('User-Agent', '')}"]
    for name, extra in VARIANTS.items():
        p = subprocess.run(base + extra + [f["url"]], capture_output=True, text=True, errors="replace")
        out = (p.stdout + p.stderr).splitlines()
        keep = [x for x in out if any(w in x.lower() for w in ("hls", "probe", "format", "picky", "extension", "error", "png", "mpegts", "exiting"))]
        print(f"===== {name}: exit {p.returncode}")
        print("\n".join(x[:220] for x in keep[-25:]))
    cdp.shutdown()


if __name__ == "__main__":
    sys.exit(main())
