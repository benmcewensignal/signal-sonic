"""Release-side artist identity: fetch title, mix, artists (names AND ids), label
and release date for every analysed track lacking metadata, in bounded chunks,
into sonic.db table track_meta. This is the foundation of the artist join
(releases <-> sets <-> bookings); the RA depth snapshot supplies the booking
side with RA artist ids, and track_meta supplies the release side with
Beatport artist ids."""
import argparse, json, sqlite3, time
from .beatport import get_token, _get

DDL = """create table if not exists track_meta(
  track_id text primary key, name text, mix text, artists text, artist_ids text,
  label text, label_id integer, released text, fetched_at text)"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--limit", type=int, default=3000, help="tracks per run (rate-limited fetch)")
    ap.add_argument("--sleep", type=float, default=0.35)
    a = ap.parse_args()
    c = sqlite3.connect(a.db); c.execute(DDL); c.commit()
    # priority: tracks in atomic/commons clusters first (they become nameable findings), then newest first
    prio = set()
    for f in ("data/atomic-pass-1.json", "data/commons-pass-2.json"):
        try:
            d = json.load(open(f))
            for p in d.get("pairs", []):
                prio.update([p["a"], p["b"]] if isinstance(p, dict) else p[:2])
        except Exception:
            pass
    lacking = [r[0] for r in c.execute("""select t.track_id from tracks t left join track_meta m on m.track_id=t.track_id
                                           where m.track_id is null and t.track_id like 'bp:%' order by t.rowid desc""")]
    todo = [t for t in lacking if t in prio] + [t for t in lacking if t not in prio]
    todo = todo[:a.limit]
    remaining = c.execute("""select count(*) from tracks t left join track_meta m on m.track_id=t.track_id
                             where m.track_id is null and t.track_id like 'bp:%'""").fetchone()[0]
    print(f"metadata: {remaining} tracks lack metadata; fetching {len(todo)} this run", flush=True)
    if not todo:
        return
    tok = get_token(); ok = err = 0; t0 = time.time()
    for i, tid in enumerate(todo, 1):
        bp = tid.split(":")[1]
        try:
            t = _get(f"/catalog/tracks/{bp}/", tok, {})
            rel = t.get("release") or {}
            c.execute("insert or replace into track_meta values(?,?,?,?,?,?,?,?,?)", (
                tid, t.get("name"), t.get("mix_name"),
                json.dumps([x.get("name") for x in t.get("artists", [])], ensure_ascii=False),
                json.dumps([x.get("id") for x in t.get("artists", [])]),
                (rel.get("label") or {}).get("name"), (rel.get("label") or {}).get("id"),
                t.get("new_release_date") or rel.get("new_release_date"), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
            ok += 1
        except Exception as e:
            err += 1
            c.execute("insert or replace into track_meta(track_id, name, fetched_at) values(?,?,?)", (tid, f"ERROR: {str(e)[:120]}", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        if i % 100 == 0:
            c.commit(); print(f"  {i}/{len(todo)} ok={ok} err={err} {time.time()-t0:.0f}s", flush=True)
        time.sleep(a.sleep)
    c.commit()
    print(f"metadata: done ok={ok} err={err}; {remaining-len(todo)} still lacking (dispatch again)", flush=True)

if __name__ == "__main__":
    main()
