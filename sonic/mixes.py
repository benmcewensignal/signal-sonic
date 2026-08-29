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


def cmd_reindex(args):
    store = Store(args.db)
    fp.ensure_schema(store.conn)
    rows = store.conn.execute(
        """SELECT t.track_id FROM tracks t
           LEFT JOIN fp_tracks f ON f.track_id = t.track_id
           WHERE f.track_id IS NULL AND t.track_id LIKE 'bp:%'
           LIMIT ?""", (args.limit,)).fetchall()
    if not rows:
        print(json.dumps({"reindex": "nothing to do"}))
        return
    token = get_token()
    done, failed, t0 = 0, 0, time.time()
    for r in rows:
        tid = r["track_id"]
        bp_id = tid.split(":", 1)[1]
        try:
            d = _get(f"/catalog/tracks/{bp_id}/", token)
            url = (d.get("sample_url") or
                   (d.get("preview") or {}).get("mp3", {}).get("url") or "")
            if not url:
                failed += 1
                continue
            path = download_preview(url)
            try:
                y = fp.load_audio(path, max_seconds=120)
                fp.index_track(store.conn, tid, y)
                done += 1
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except Exception as e:
            failed += 1
            if failed <= 3:
                print(f"  fail {tid}: {type(e).__name__}: {str(e)[:80]}", flush=True)
        if (done + failed) % 25 == 0:
            rate = (done + failed) / (time.time() - t0)
            print(f"  reindex {done+failed}/{len(rows)} ({rate:.1f}/s, "
                  f"{failed} failed)", flush=True)
    n_idx = store.conn.execute("SELECT COUNT(*) c FROM fp_tracks").fetchone()["c"]
    print(json.dumps({"reindexed": done, "failed": failed,
                      "index_size_tracks": n_idx}))


def cmd_mixscan(args):
    store = Store(args.db)
    fp.ensure_schema(store.conn)
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
    hits = fp.match_mix(store.conn, y)
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
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(fn=cmd_reindex)
    p = sub.add_parser("mixscan")
    p.add_argument("--db", default="sonic.db")
    p.add_argument("--audio", required=True, help="file path or URL")
    p.add_argument("--max-minutes", type=int, default=150)
    p.set_defaults(fn=cmd_mixscan)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
