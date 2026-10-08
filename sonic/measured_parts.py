"""The crawl's measures, kept in parts.

data/tracklists/measured.jsonl reached 104.7 MB on 8 October 2026, a few hundred records short of the 100 MB GitHub
refuses outright: the next run's push was refused five times and its save failed, as every run's would have after it.
New lines go to measured-2.jsonl, measured-3.jsonl and so on, each closed at 45 MB, under GitHub's 50 MB warning.
Readers read every part in order, so a record's lines keep the order they had in the one file.
"""
import glob
import os
import re

ROLL = 45_000_000
_PART = re.compile(r"measured(?:-(\d+))?\.jsonl$")


def _n(p):
    m = _PART.search(os.path.basename(p))
    return int(m.group(1)) if m and m.group(1) else 1


def parts(folder="data/tracklists"):
    """Every part, oldest first: measured.jsonl, then measured-2.jsonl, measured-3.jsonl..."""
    ps = [p for p in glob.glob(os.path.join(folder, "measured*.jsonl")) if _PART.fullmatch(os.path.basename(p))]
    return sorted(ps, key=_n)


def current(folder="data/tracklists"):
    """The part new lines go to: the newest, or the next one once the newest is past ROLL."""
    ps = parts(folder)
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
