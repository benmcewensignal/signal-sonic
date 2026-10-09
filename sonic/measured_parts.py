"""The crawl's measures, kept in parts.

data/tracklists/measured.jsonl reached 104.7 MB on 8 October 2026, a few hundred records short of the 100 MB GitHub
refuses outright: the next run's push was refused five times and its save failed, as every run's would have after it.
New lines from the crawl's own measuring go to measured-2.jsonl, measured-3.jsonl and so on, each closed at 45 MB,
under GitHub's 50 MB warning.

From 9 October the measuring runs alongside the crawl (tracklists-measure.yml) instead of in turn with it. Each of its
passes writes one file, data/tracklists/measured/<pass>.jsonl, which nothing else writes, so the two never edit the
same file. Readers read every part in order: the crawl's, then the passes' by number.

Records whose preview could not be measured are listed in data/tracklists/measured/failed.txt, one id a line per failed
attempt; after two failures a record is left out of the backlog rather than retried by every pass.
"""
import collections
import glob
import os
import re

ROLL = 45_000_000
FAILS = 2
_PART = re.compile(r"measured(?:-(\d+))?\.jsonl$")
_PASS = re.compile(r"(\d+)\.jsonl$")


def _n(p):
    m = _PART.search(os.path.basename(p))
    return int(m.group(1)) if m and m.group(1) else 1


def pass_dir(folder="data/tracklists"):
    return os.path.join(folder, "measured")


def parts(folder="data/tracklists"):
    """Every part, oldest first: measured.jsonl, measured-2.jsonl..., then measured/<pass>.jsonl by pass number."""
    top = sorted((p for p in glob.glob(os.path.join(folder, "measured*.jsonl")) if _PART.fullmatch(os.path.basename(p))), key=_n)
    passes = [p for p in glob.glob(os.path.join(pass_dir(folder), "*.jsonl")) if _PASS.fullmatch(os.path.basename(p))]
    return top + sorted(passes, key=lambda p: int(_PASS.fullmatch(os.path.basename(p)).group(1)))


def current(folder="data/tracklists"):
    """The part the crawl's own measuring appends to: the newest top-level part, or the next once it is past ROLL."""
    ps = sorted((p for p in glob.glob(os.path.join(folder, "measured*.jsonl")) if _PART.fullmatch(os.path.basename(p))), key=_n)
    if not ps:
        return os.path.join(folder, "measured.jsonl")
    last = ps[-1]
    if os.path.getsize(last) < ROLL:
        return last
    return os.path.join(folder, f"measured-{_n(last) + 1}.jsonl")


def lines(folder="data/tracklists"):
    """Every non-empty line of every part, in order."""
    for p in parts(folder):
        with open(p) as f:
            for ln in f:
                if ln.strip():
                    yield ln


def failed(folder="data/tracklists", at_least=FAILS):
    """Records that failed to measure at least `at_least` times."""
    p = os.path.join(pass_dir(folder), "failed.txt")
    if not os.path.exists(p):
        return set()
    c = collections.Counter(ln.strip() for ln in open(p) if ln.strip())
    return {t for t, k in c.items() if k >= at_least}
