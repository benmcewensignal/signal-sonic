"""Bring the records DJ sets play into the corpus database.

From data/tracklists/measured.jsonl (records the corpus lacked, measured on the corpus analyser)
and data/tracklists/<dj>.json (who played them, in which set, at which minute):
  tracks            the measure, source 'tracklist'
  track_meta        name, mix, artists and label as Beatport gave them, where the corpus lacks it
  preview_cache     the preview address, so a future remeasure can reach the record
  tracklist_plays   dj, set, minute, running position, record
Existing rows are never overwritten: a record the corpus already measured keeps its measure.
"""
import argparse, glob, json, os, sqlite3, time


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--db", default="sonic.db"); a = ap.parse_args()
    c = sqlite3.connect(a.db)
    c.execute("""create table if not exists tracklist_plays (dj text, set_title text, minute integer, position integer,
                 track_id text, entry text, primary key (set_title, position))""")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meas = {}
    if os.path.exists("data/tracklists/measured.jsonl"):
        for ln in open("data/tracklists/measured.jsonl"):
            r = json.loads(ln); meas[r["track_id"]] = r
    n_t = n_m = n_p = n_plays = 0
    for f in sorted(glob.glob("data/tracklists/*.json")):
        if f.endswith("curves.json"): continue
        D = json.load(open(f))
        for s in D.get("sets", []):
            c.execute("delete from tracklist_plays where set_title=?", (s["title"],))   # a re-import replaces the set's plays
            for i, r in enumerate(s["records"]):
                tid = r.get("bp")
                entry = f'{r.get("artist","")} - {r.get("title","")}'.strip(" -") or r.get("raw", "")
                c.execute("insert or ignore into tracklist_plays values (?,?,?,?,?,?)", (D["dj"], s["title"], r.get("minute"), i, tid, entry[:200])); n_plays += c.execute("select changes()").fetchone()[0]
                if not tid: continue
                m = meas.get(tid)
                if m:
                    c.execute("insert or ignore into tracks (track_id, analyser_id, analyser_ver, features, source, first_seen, created_at) values (?,?,?,?,?,?,?)",
                              (tid, "local", m["analyser_ver"], json.dumps(m["features"], default=float), "tracklist", now, time.time()))
                    n_t += c.execute("select changes()").fetchone()[0]
                c.execute("insert or ignore into track_meta (track_id, name, mix, artists, label, released, fetched_at) values (?,?,?,?,?,?,?)",
                          (tid, r.get("name"), r.get("mix"), json.dumps([r.get("artist")]), r.get("label"), r.get("released"), now))
                if r.get("released"): c.execute("update track_meta set released=? where track_id=? and (released is null or released='')", (r["released"], tid))
                n_m += c.execute("select changes()").fetchone()[0]
                if r.get("preview"):
                    c.execute("insert or ignore into preview_cache (track_id, url, resolved_at) values (?,?,?)", (tid, r["preview"], now))
                    n_p += c.execute("select changes()").fetchone()[0]
    c.commit()
    msg = f"imported {n_t} measured records, {n_m} names, {n_p} preview addresses and {n_plays} set plays"
    print(msg); print(f"::notice title=tracklist import::{msg}")


if __name__ == "__main__":
    main()
