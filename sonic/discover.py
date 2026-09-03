"""Mix discovery: stop trawling URLs.

Per scene, per week:
  1. discover candidate mixes — NTS episode search (undocumented JSON API,
     probe-first discipline) and Mixcloud's public API (popular by tag)
  2. dedupe against the mixes table
  3. resolve audio with yt-dlp (battle-tested extractors for both)
  4. mixscan each; store hits in mix_plays, mix metadata in mixes

Commands:
  probe    — hit both discovery APIs for one tag, print what came back
  scan     — the weekly job:  python -m sonic.discover scan --per-scene 2

Scene search tags live in scene_map.json as "tags": [...] per genre entry
(falls back to the scene slug with dashes as spaces).

Honest notes: NTS's API is undocumented and may shift (probe exists for
that day); yt-dlp is the audio path and its failures are logged per mix,
never fatal to the run; audio is analysed and deleted, never kept.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from .store import Store
from . import fingerprints as fp

UA = {"User-Agent": "signal-sound-layer/0.1 (research; earlysignal.live)"}
MIX_RATES = (0.97, 1.0, 1.03)

MIX_SCHEMA = """
CREATE TABLE IF NOT EXISTS mixes (
    mix_url     TEXT PRIMARY KEY,
    scene       TEXT NOT NULL,
    source      TEXT NOT NULL,          -- nts | mixcloud
    title       TEXT,
    published   TEXT,
    scanned_at  REAL,
    duration_s  INTEGER,
    n_hits      INTEGER,
    unmatched_share REAL,
    error       TEXT
);
CREATE TABLE IF NOT EXISTS mix_plays (
    mix_url     TEXT NOT NULL,
    track_id    TEXT NOT NULL,
    offset_s    REAL NOT NULL,
    votes       INTEGER NOT NULL,
    rate        REAL NOT NULL,
    PRIMARY KEY (mix_url, track_id)
);
"""


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# -- discovery ---------------------------------------------------------------

def nts_search(tag: str, limit: int = 6) -> list[dict]:
    """Episode search on nts.live's undocumented JSON API. Variants tried in
    order; failures return [] with the reason printed (probe-first lesson)."""
    variants = [
        f"https://www.nts.live/api/v2/search?q={urllib.parse.quote(tag)}"
        f"&limit={limit}&offset=0&types%5B%5D=episode",
        f"https://www.nts.live/api/v2/search/episodes?q={urllib.parse.quote(tag)}"
        f"&limit={limit}",
    ]
    for url in variants:
        try:
            d = _get_json(url)
            results = d.get("results", [])
            out = []
            for r in results:
                art = r.get("article") or r
                path = (art.get("path") or r.get("path") or "")
                if not path:
                    continue
                out.append({
                    "url": "https://www.nts.live" + path,
                    "title": art.get("title") or r.get("title") or "",
                    "published": (r.get("local_date") or art.get("updated") or "")[:10],
                    "source": "nts"})
            if out:
                return out
        except Exception as e:
            print(f"  nts variant failed ({type(e).__name__}: {str(e)[:60]})",
                  flush=True)
    return []


def mixcloud_popular(tag: str, limit: int = 6) -> list[dict]:
    """Mixcloud public API: popular cloudcasts for a tag. Carries play count
    and uploader follower count so the caller can favour prominent artists."""
    slug = tag.lower().replace(" ", "-")
    for url in (f"https://api.mixcloud.com/discover/{slug}/popular/?limit={limit}",
                f"https://api.mixcloud.com/search/?q={urllib.parse.quote(tag)}"
                f"&type=cloudcast&limit={limit}"):
        try:
            d = _get_json(url)
            out = []
            for r in d.get("data", []):
                u = r.get("user") or {}
                out.append({"url": r.get("url", ""),
                            "title": r.get("name", ""),
                            "published": (r.get("created_time") or "")[:10],
                            "plays": r.get("play_count") or 0,
                            "artist": u.get("name") or u.get("username") or "",
                            "followers": u.get("follower_count") or 0,
                            "source": "mixcloud"})
            out = [o for o in out if o["url"]]
            if out:
                return out
        except Exception as e:
            print(f"  mixcloud variant failed ({type(e).__name__}: {str(e)[:60]})",
                  flush=True)
    return []


MAX_AGE_DAYS = 120        # a "current" mix: older than this says little
                          # about what is being played now
MIN_PLAYS = 1500          # mixcloud prominence floor (uploads with fewer
                          # plays are not the sets the scene is hearing)
MIN_FOLLOWERS = 500       # uploader reach floor
# Formats that are radio furniture rather than artist sets: talk-heavy
# breakfast/news shows, chart rundowns, generic "sessions" filler.
EXCLUDE_TITLE = re.compile(
    r"\b(breakfast|early bird|morning show|wake ?up|sunrise|daybreak|news|talk|interview|chart show|top 40|"
    r"weather|podcast ep|q&a|discussion)\b", re.I)


def _age_days(published: str) -> float:
    if not published or len(published) < 10:
        return 9999.0
    try:
        y, m, d = (int(x) for x in published[:10].split("-"))
        import datetime as _dt
        return (_dt.date.today() - _dt.date(y, m, d)).days
    except Exception:
        return 9999.0


def rank_candidates(cands: list[dict], max_age_days: int = MAX_AGE_DAYS,
                    min_plays: int = MIN_PLAYS) -> list[dict]:
    """Keep contemporary sets by prominent artists; drop radio furniture.

    Ranking favours plays and uploader reach, with a recency bonus, so the
    scan spends its compute on mixes the scene actually heard rather than
    whatever a tag search surfaced first.
    """
    keep = []
    for c in cands:
        if EXCLUDE_TITLE.search(c.get("title") or ""):
            continue
        age = _age_days(c.get("published", ""))
        known_age = age < 9000
        # absence of evidence is not evidence of staleness: NTS search
        # often omits dates and mixcloud's search endpoint omits play
        # counts. Filtering on missing fields deleted every curated show.
        if known_age and age > max_age_days:
            continue
        if c.get("source") == "mixcloud":
            plays, foll = c.get("plays"), c.get("followers")
            if plays is not None and foll is not None and plays and foll:
                if plays < min_plays and foll < MIN_FOLLOWERS:
                    continue
        c = dict(c, _age=age if known_age else None)
        keep.append(c)
    def score(c):
        import math
        reach = math.log10(max(c.get("plays") or 0, 1) + 1) \
            + 0.5 * math.log10(max(c.get("followers") or 0, 1) + 1)
        if c["source"] == "nts":
            reach = 3.0
        if c["_age"] is None:
            return reach + 0.5          # unknown recency: rank below known
        recency = max(0.0, 1.0 - c["_age"] / max(max_age_days, 1))
        return reach + 2.0 * recency
    return sorted(keep, key=score, reverse=True)


def scene_tags(scene_map: dict) -> dict[str, list[str]]:
    out = {}
    for k, cfg in scene_map.items():
        if k.startswith("_"):
            continue
        tags = cfg.get("tags") or [cfg["scene"].replace("-", " ")]
        out[cfg["scene"]] = tags
    return out


# -- audio -------------------------------------------------------------------

def fetch_audio(url: str, max_minutes: int) -> str:
    """Resolve mix audio via yt-dlp, then transcode to mono 22k05 WAV:
    yt-dlp delivers m4a/AAC, which libsndfile cannot decode (every mix in
    the first live scan failed on exactly this). ffmpeg is already a dep."""
    ddir = tempfile.mkdtemp()
    cmd = ["yt-dlp", "-q", "-f", "bestaudio/best", "-P", ddir,
           "-o", "mix.%(ext)s",
           "--no-playlist", "--socket-timeout", "20", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    import glob as _g
    got = sorted(_g.glob(os.path.join(ddir, "mix.*")))
    if r.returncode != 0 or not got:
        raise RuntimeError(f"yt-dlp: {r.stderr.strip()[-160:]}")
    raw = got[0]
    wav = raw + ".wav"
    try:
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-i", raw, "-ac", "1", "-ar", "22050",
             "-t", str(max_minutes * 60), wav],
            capture_output=True, text=True, timeout=900)
        if r.returncode != 0 or not os.path.exists(wav):
            raise RuntimeError(f"ffmpeg: {r.stderr.strip()[-160:]}")
    finally:
        try:
            os.unlink(raw)
        except OSError:
            pass
    return wav


# -- commands ----------------------------------------------------------------

def cmd_probe(args):
    print("NTS:", json.dumps(nts_search(args.tag, 3), indent=1))
    print("Mixcloud:", json.dumps(mixcloud_popular(args.tag, 3), indent=1))


def cmd_scan(args):
    store = Store(args.db)
    store.conn.executescript(MIX_SCHEMA)
    try:
        store.conn.execute("ALTER TABLE mix_plays ADD COLUMN wvotes REAL")
        store.conn.commit()
    except Exception:
        pass   # column already present
    fpc = fp.open_store(args.fp_db)
    n_indexed = fpc.execute(
        "SELECT COUNT(*) c FROM fp_tracks").fetchone()["c"]
    print(f"fingerprint index: {n_indexed} tracks", flush=True)
    with open(args.scene_map) as f:
        smap = json.load(f)
    tags = scene_tags(smap)

    scanned, failed = 0, 0
    rescan = bool(getattr(args, "rescan", False))
    for scene, taglist in tags.items():
        cands = []
        if rescan:
            # re-score what is already stored (matcher changed): same URLs, plays replaced
            for r in store.conn.execute("SELECT mix_url, source, title, published FROM mixes WHERE scene=? AND error IS NULL", (scene,)):
                cands.append({"url": r[0], "source": r[1], "title": r[2] or "", "published": r[3], "plays": 10**9, "_age": 0})
            fresh = cands; n_raw = len(cands)
        else:
            pool = max(12, args.per_scene * 8)   # dedupe + filters eat most
            for tag in taglist:
                cands += nts_search(tag, pool)
                cands += mixcloud_popular(tag, pool)
            fresh = [c for c in cands if not store.conn.execute(
                "SELECT 1 FROM mixes WHERE mix_url=? AND error IS NULL",
                (c["url"],)).fetchone()]
            n_raw = len(fresh)
            fresh = rank_candidates(fresh, args.max_age_days, args.min_plays)
        print(f"  [{scene}] {n_raw} candidates -> {len(fresh)} current/prominent",
              flush=True)
        for c in (fresh if rescan else fresh[:args.per_scene]):
            who = c.get("artist") or ""
            reach = f" {c['plays']:,} plays" if c.get("plays") else ""
            print(f"  [{scene}] {c['source']}: {c['title'][:52]}"
                  f"{(' — ' + who) if who else ''}{reach}"
                  f" ({str(int(c['_age'])) + 'd old' if c.get('_age') is not None else 'date unknown'})",
                  flush=True)
            path = None
            try:
                path = fetch_audio(c["url"], args.max_minutes)
                y = fp.load_audio(path, max_seconds=args.max_minutes * 60)
                hits = fp.match_mix(fpc, y, rates=MIX_RATES)
                dur = len(y) / fp.SR
                covered = min(dur, len(hits) * 180.0)
                with store.tx() as conn:
                    conn.execute("DELETE FROM mix_plays WHERE mix_url=?", (c["url"],))
                    conn.execute(
                        "INSERT OR REPLACE INTO mixes VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                        (c["url"], scene, c["source"], c["title"], c["published"],
                         time.time(), int(dur), len(hits),
                         round(1 - covered / dur, 3) if dur else None))
                    for h in hits:
                        conn.execute(
                            "INSERT OR REPLACE INTO mix_plays (mix_url, track_id, offset_s, votes, rate, wvotes) VALUES (?,?,?,?,?,?)",
                            (c["url"], h["track_id"], h["mix_offset_s"],
                             h["votes"], h["rate"], h.get("wvotes")))
                scanned += 1
                print(f"    {len(hits)} tracks identified in {int(dur//60)}min",
                      flush=True)
            except Exception as e:
                failed += 1
                with store.tx() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO mixes (mix_url, scene, source, "
                        "title, published, scanned_at, error) VALUES (?,?,?,?,?,?,?)",
                        (c["url"], scene, c["source"], c["title"], c["published"],
                         time.time(), f"{type(e).__name__}: {str(e)[:160]}"))
                print(f"    failed: {type(e).__name__}: {str(e)[:100]}", flush=True)
            finally:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
    print(json.dumps({"mixes_scanned": scanned, "failed": failed,
                      "fingerprint_index": n_indexed}))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--tag", default="amapiano")
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("scan")
    p.add_argument("--db", default="sonic.db")
    p.add_argument("--fp-db", default="fingerprints.db")
    p.add_argument("--scene-map", default="scene_map.json")
    p.add_argument("--per-scene", type=int, default=2)
    p.add_argument("--rescan", action="store_true", help="re-scan mixes already in the store (matcher changes)")
    p.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    p.add_argument("--min-plays", type=int, default=MIN_PLAYS)
    p.add_argument("--max-minutes", type=int, default=150)
    p.set_defaults(fn=cmd_scan)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
