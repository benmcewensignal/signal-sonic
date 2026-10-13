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
    the moment (a dropped connection, a timeout, a server error), which is not held against it. Of the 4xx answers,
    an expired token (401), a request timeout (408) and being asked to slow down (429) are the moment's too: two runs
    meeting any of them would otherwise hold back every record they reached, for good."""
    import socket, urllib.error
    if isinstance(e, urllib.error.HTTPError): return 400 <= e.code < 500 and e.code not in (401, 408, 429)
    if isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout)): return False
    return True


def reason(e):
    """A failure in a few words, for counting: the HTTP status, or the kind of error."""
    import urllib.error
    return f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else type(e).__name__


# The index build (audio/build_index.py in signal-sonic-audio) names a record from track_meta and leaves out any record
# it cannot name. The crawl wrote names for the records DJs play as it matched them, so while fingerprinting worked
# through those every record it added reached the index. From 13 Oct it reached the charted catalogue records from the
# deepening, 80 per cent of which had no name: two runs fingerprinted 1,840 and 1,897 records, and the index gained
# 129 and 401. The catalogue entry fetched to find a record's preview carries its name, so it is written here, in the
# row sonic.metadata writes; `names` below names the records fingerprinted before this.
META_DDL = """create table if not exists main_db.track_meta(
  track_id text primary key, name text, mix text, artists text, artist_ids text,
  label text, label_id integer, released text, fetched_at text)"""


def named_ids(fpc):
    """Records with a real name (sonic.metadata wrote 'ERROR: ...' as the name of a record it could not fetch)."""
    return {r[0] for r in fpc.execute(
        "select track_id from main_db.track_meta where name is not null and name not like 'ERROR:%'")}


def write_meta(fpc, tid, t):
    """The record's name, mix, artists and label from its catalogue entry, as sonic.metadata stores them."""
    if not isinstance(t, dict) or not t.get("name"):
        return False
    rel = t.get("release") or {}
    fpc.execute("insert or replace into main_db.track_meta values(?,?,?,?,?,?,?,?,?)", (
        tid, t.get("name"), t.get("mix_name"),
        json.dumps([x.get("name") for x in t.get("artists") or []], ensure_ascii=False),
        json.dumps([x.get("id") for x in t.get("artists") or []]),
        (rel.get("label") or {}).get("name"), (rel.get("label") or {}).get("id"),
        t.get("new_release_date") or rel.get("new_release_date"), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
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
    import collections
    tiers = collections.Counter(key(t)[0] for t in todo)
    print(json.dumps({"not yet fingerprinted": len(todo), "by tier": dict(sorted(tiers.items())), "held back after two failures": len(held)}), flush=True)
    # as notices: a run's annotations can be read where its log cannot
    print(f"::notice title=fingerprinting queue::{len(todo):,} records not yet fingerprinted, by tier "
          f"{', '.join(f'{k}: {v:,}' for k, v in sorted(tiers.items()))} (0 canon, 1 two or more DJs or top 10, 2 one DJ or "
          f"charted, 3 catalogue); {len(held):,} held back after two failures", flush=True)
    rows = todo[:args.limit]
    if not rows:
        print(json.dumps({"reindex": "nothing to do"}))
        return
    fpc.execute(META_DDL)
    named = named_ids(fpc)
    now = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    def note_failure(tid, why):
        fpc.execute("""insert into fp_failed values (?,1,?,?) on conflict(track_id) do update
                       set n = n + 1, last_error = excluded.last_error, at = excluded.at""", (tid, why[:120], now()))
        fpc.commit()
    token = get_token()
    done, failed, named_now, t0 = 0, 0, 0, time.time()
    why = collections.Counter()
    for tid in rows:
        bp_id = tid.split(":", 1)[1]
        try:
            d = _get(f"/catalog/tracks/{bp_id}/", token)
            if tid not in named and write_meta(fpc, tid, d):
                named.add(tid); named_now += 1     # committed with the fingerprints, or with the run's last commit
            url = (d.get("sample_url") or
                   (d.get("preview") or {}).get("mp3", {}).get("url") or "")
            if not url:
                failed += 1; why["no preview address"] += 1; note_failure(tid, "no preview address")
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
            failed += 1; why[reason(e)] += 1
            if lasting(e): note_failure(tid, f"{type(e).__name__}: {str(e)[:80]}")
            if failed <= 3:
                print(f"  fail {tid}: {type(e).__name__}: {str(e)[:80]}", flush=True)
        if (done + failed) % 25 == 0:
            rate = (done + failed) / (time.time() - t0)
            print(f"  reindex {done+failed}/{len(rows)} ({rate:.1f}/s, "
                  f"{failed} failed)", flush=True)
    fpc.commit()
    n_idx = fpc.execute("SELECT COUNT(*) c FROM fp_tracks").fetchone()["c"]
    sz = os.path.getsize(args.fp_db) / 1e6 if os.path.exists(args.fp_db) else 0
    print(json.dumps({"reindexed": done, "failed": failed, "named": named_now,
                      "index_size_tracks": n_idx, "store_mb": round(sz, 1)}))
    print(f"::notice title=fingerprinting run::{done:,} fingerprinted, {failed:,} failed"
          f"{' (' + ', '.join(f'{k} {v}' for k, v in why.most_common(4)) + ')' if why else ''}; {named_now:,} records named "
          f"from the same catalogue entries; the store holds {n_idx:,}", flush=True)


def cmd_names(args):
    """Name the fingerprinted records that have no name, most important first, so the index can include them.

    The fingerprinting names what it adds from 13 Oct (write_meta above); this names what it added before, about
    30,000 records, the catalogue entry for each. Within a time budget, so it shares the run with the fingerprinting.
    A record whose entry is refused for its own reasons (a 403 or 404, say: no longer in the catalogue) is marked the way
    sonic.metadata marks one, a name beginning 'ERROR:', which the index build leaves out, so no later run asks again;
    the fingerprinting replaces the mark if the catalogue ever answers for it."""
    fpc = fp.open_store(args.fp_db)
    fpc.execute("ATTACH DATABASE ? AS main_db", (args.db,))
    fpc.execute(META_DDL)
    tried = {r[0] for r in fpc.execute("select track_id from main_db.track_meta where name is not null")}   # named, or marked
    todo = [r[0] for r in fpc.execute("select track_id from fp_tracks where track_id like 'bp:%'") if r[0] not in tried]
    key = importance(fpc)
    todo.sort(key=key)
    import collections, urllib.error
    tiers = collections.Counter(key(t)[0] for t in todo)
    print(f"::notice title=names::{len(todo):,} fingerprinted records have no name, by tier "
          f"{', '.join(f'{k}: {v:,}' for k, v in sorted(tiers.items()))}", flush=True)
    if not todo:
        return
    token, t0, ok, failed, marked = get_token(), time.time(), 0, 0, 0
    why = collections.Counter()
    for i, tid in enumerate(todo[:args.limit], 1):
        if time.time() - t0 > args.budget_minutes * 60:
            break
        for attempt in (1, 2):
            try:
                d = _get(f"/catalog/tracks/{tid.split(':', 1)[1]}/", token)
                if write_meta(fpc, tid, d): ok += 1
                else: failed += 1; why["no name in the entry"] += 1
                break
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 1:      # the token ran out in a long run: one more, with a new one
                    token = get_token(); continue
                failed += 1; why[reason(e)] += 1
                if lasting(e):
                    fpc.execute("""insert into main_db.track_meta (track_id, name, fetched_at) values (?,?,?)
                                   on conflict(track_id) do update set name = excluded.name, fetched_at = excluded.fetched_at""",
                                (tid, f"ERROR: {str(e)[:120]}", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
                    marked += 1
                if e.code == 429: time.sleep(30)
                break
            except Exception as e:
                failed += 1; why[reason(e)] += 1
                break
        if i % 200 == 0:
            fpc.commit()
            print(f"  names {i}: {ok} named, {failed} failed, {time.time() - t0:.0f}s", flush=True)
        time.sleep(args.sleep)
    fpc.commit()
    left = len(todo) - ok - marked
    print(f"::notice title=names::{ok:,} named in {(time.time() - t0) / 60:.0f} minutes, {failed:,} could not be"
          f"{' (' + ', '.join(f'{k} {v}' for k, v in why.most_common(4)) + ')' if why else ''}, {marked:,} of them gone from the "
          f"catalogue; {left:,} fingerprinted records still to name", flush=True)


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
    p = sub.add_parser("names")
    p.add_argument("--db", default="sonic.db")
    p.add_argument("--fp-db", default="fingerprints.db")
    p.add_argument("--limit", type=int, default=6000)
    p.add_argument("--budget-minutes", type=float, default=25)
    p.add_argument("--sleep", type=float, default=0.35, help="between catalogue requests, as sonic.metadata paces them")
    p.set_defaults(fn=cmd_names)
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
