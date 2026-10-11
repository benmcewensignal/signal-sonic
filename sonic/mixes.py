"""Mix layer commands.

  reindex — fingerprint the reference corpus: for every analysed Beatport
            track not yet in the fingerprint index, re-resolve its preview
            URL by id, download, hash, index, delete the audio.
                python -m sonic.mixes reindex --limit 500
  mixscan — find indexed tracks inside one mix:
                python -m sonic.mixes mixscan --audio path_or_url
            Output: track_id, position in the mix, vote strength, and the
            share of the mix that matched nothing we know (the unreleased-
            density signal).

The index lives in the same sonic.db. Recall is a floor, not a census
(tempo-shift limits documented in fingerprints.py) — valid for trend
weighting, never for royalty-grade claims.
"""
from __future__ import annotations
import argparse
import json
import os
import time
from .store import Store
from . import fingerprints as fp
from .beatport import _get, get_token, download_preview


def importance(fpc):
    """How much a record matters to recognition, as a sort key on its id (smaller first), the same tiers the index
    build keeps within its limit (audio/build_index.py in signal-sonic-audio): the canon and classics; then records
    played by two or more DJs or charted top 10; then records played by one DJ or charted at all; then the catalogue
    sample. Within a tier: more DJs, a higher chart position, the newer record."""
    import collections
    djs = collections.defaultdict(set)
    for r in fpc.execute("select dj, track_id from main_db.tracklist_plays where track_id is not null and track_id != ''"):
        djs[r[1]].add(r[0])
    for r in fpc.execute("select mix_url, track_id from main_db.mix_plays where track_id is not null"):
        djs[r[1]].add("mix:" + str(r[0]))
    best = {r[0]: r[1] for r in fpc.execute("select track_id, min(chart_rank) from main_db.track_scenes where chart_rank is not null group by track_id")}
    src, seen = {}, {}
    for r in fpc.execute("select track_id, source, first_seen from main_db.tracks"):
        src[r[0]] = r[1] or ""; seen[r[0]] = str(r[2] or "")
    def key(tid):
        n = len(djs.get(tid, ())); rk = best.get(tid)
        if src.get(tid) in ("canon", "classic"): tier = 0
        elif n >= 2 or (rk is not None and rk <= 10): tier = 1
        elif n == 1 or rk is not None: tier = 2
        else: tier = 3
        newer = -int("".join(ch for ch in seen.get(tid, "")[:10] if ch.isdigit()) or 0)
        return (tier, -n, rk if rk is not None else 999, newer, tid)
    return key


def lasting(e):
    """A failure that belongs to the record (no preview at that address, audio that will not decode) rather than to
    the moment (a dropped connection, a timeout, a server error), which is not held against it."""
    import socket, urllib.error
    if isinstance(e, urllib.error.HTTPError): return 400 <= e.code < 500
    if isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout)): return False
    return True


def cmd_reindex(args):
    store = Store(args.db)
    fpc = fp.open_store(args.fp_db)
    fpc.execute("ATTACH DATABASE ? AS main_db", (args.db,))
    # Most important first. The order was the table's own, so the index filled with whatever had been measured
    # first: on 11 Oct it held 32,149 records sampled from the catalogue while 71,388 records DJs play and 60,951
    # charted records were missing, and the index can address 65,536 at most.
    fpc.execute("create table if not exists fp_failed (track_id text primary key, n integer, last_error text, at text)")
    held = {r[0] for r in fpc.execute("select track_id from fp_failed where n >= 2")}   # twice failed for its own reasons
    todo = [r[0] for r in fpc.execute(
        """SELECT t.track_id FROM main_db.tracks t
           LEFT JOIN fp_tracks f ON f.track_id = t.track_id
           WHERE f.track_id IS NULL AND t.track_id LIKE 'bp:%'""") if r[0] not in held]
    key = importance(fpc)
    todo.sort(key=key)
    tiers = __import__("collections").Counter(key(t)[0] for t in todo)
    print(json.dumps({"not yet fingerprinted": len(todo), "by tier": dict(sorted(tiers.items())), "held back after two failures": len(held)}), flush=True)
    rows = todo[:args.limit]
    if not rows:
        print(json.dumps({"reindex": "nothing to do"}))
        return
    now = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    def note_failure(tid, why):
        fpc.execute("""insert into fp_failed values (?,1,?,?) on conflict(track_id) do update
                       set n = n + 1, last_error = excluded.last_error, at = excluded.at""", (tid, why[:120], now()))
        fpc.commit()
    token = get_token()
    done, failed, t0 = 0, 0, time.time()
    for tid in rows:
        bp_id = tid.split(":", 1)[1]
        try:
            d = _get(f"/catalog/tracks/{bp_id}/", token)
            url = (d.get("sample_url") or
                   (d.get("preview") or {}).get("mp3", {}).get("url") or "")
            if not url:
                failed += 1; note_failure(tid, "no preview address")
                continue
            path = download_preview(url)
            try:
                y = fp.load_audio(path, max_seconds=120)
                fp.index_track(fpc, tid, y)
                done += 1
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except Exception as e:
            failed += 1
            if lasting(e): note_failure(tid, f"{type(e).__name__}: {str(e)[:80]}")
            if failed <= 3:
                print(f"  fail {tid}: {type(e).__name__}: {str(e)[:80]}", flush=True)
        if (done + failed) % 25 == 0:
            rate = (done + failed) / (time.time() - t0)
            print(f"  reindex {done+failed}/{len(rows)} ({rate:.1f}/s, "
                  f"{failed} failed)", flush=True)
    n_idx = fpc.execute("SELECT COUNT(*) c FROM fp_tracks").fetchone()["c"]
    sz = os.path.getsize(args.fp_db) / 1e6 if os.path.exists(args.fp_db) else 0
    print(json.dumps({"reindexed": done, "failed": failed,
                      "index_size_tracks": n_idx, "store_mb": round(sz, 1)}))


def cmd_mixscan(args):
    fpc = fp.open_store(args.fp_db)
    path = args.audio
    cleanup = False
    if path.startswith(("http://", "https://")):
        path = download_preview(path)
        cleanup = True
    try:
        y = fp.load_audio(path, max_seconds=args.max_minutes * 60)
    finally:
        if cleanup:
            try:
                os.unlink(path)
            except OSError:
                pass
    hits = fp.match_mix(fpc, y)
    dur_s = len(y) / fp.SR
    # crude matched-coverage estimate: each hit credited ~180s of mix
    covered = min(dur_s, len(hits) * 180.0)
    out = {
        "mix_seconds": round(dur_s),
        "tracks_identified": len(hits),
        "unmatched_share": round(1 - covered / dur_s, 2) if dur_s else None,
        "hits": [
            {"track_id": h["track_id"],
             "at": f"{int(h['mix_offset_s']//60)}:{int(h['mix_offset_s']%60):02d}",
             "votes": h["votes"], "rate": h["rate"]}
            for h in hits],
        "note": ("unmatched_share is the unreleased-density signal, and recall "
                 "is a floor: heavy tempo-shifted plays are missed by design"),
    }
    print(json.dumps(out, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("reindex")
    p.add_argument("--db", default="sonic.db")
    p.add_argument("--fp-db", default="fingerprints.db")
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(fn=cmd_reindex)
    p = sub.add_parser("mixscan")
    p.add_argument("--db", default="sonic.db")
    p.add_argument("--fp-db", default="fingerprints.db")
    p.add_argument("--audio", required=True, help="file path or URL")
    p.add_argument("--max-minutes", type=int, default=150)
    p.set_defaults(fn=cmd_mixscan)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
