"""Fold the shards' work into the database, in order, once.

Parallel shards measure; this writes. Keeping the two apart is what makes the database
safe to shard against: there is exactly one writer, and it runs when every shard is done.
"""
import argparse, glob, json, sys

from .analyser import get_analyser
from .aggregate import build_fingerprint
from .analyser import FeatureVector
from .store import Store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--glob", default="shards/**/deepen-shard-*.jsonl")
    ap.add_argument("--report", default="", help="write the machine-readable result here, separate from the log")
    a = ap.parse_args()
    files = sorted(glob.glob(a.glob, recursive=True))
    print(f"merging {len(files)} shard files", flush=True)
    store = Store(a.db)
    analyser = get_analyser("local")
    added = seen = 0
    touched = set()
    for f in files:
        for line in open(f):
            try: d = json.loads(line)
            except Exception: continue
            seen += 1
            tid = d["track_id"]
            row = store.conn.execute(
                "SELECT 1 FROM tracks WHERE track_id=? AND analyser_id=?",
                (tid, analyser.analyser_id)).fetchone()
            if row is None:
                store.upsert_track(tid, json.dumps(d["features"]), analyser.analyser_id,
                                   d.get("analyser_ver") or analyser.version, d["source"], d["week"])
                added += 1
            store.assign_scene(tid, d["scene"], d["week"], d["source"])
            touched.add((d["scene"], d["week"]))
    # every month a shard touched needs its flat fingerprint rebuilt from the deeper sample
    rebuilt = 0
    for scene, week in sorted(touched):
        rows = store.scene_track_rows(scene, week, analyser.analyser_id)
        if not rows: continue
        parsed = [(FeatureVector.from_json(r["features"]), r["weight"]) for r in rows]
        store.save_scene_week(scene, week, analyser.analyser_id, "flat", len(parsed),
                              json.dumps(build_fingerprint(parsed)))
        rebuilt += 1
    store.conn.commit()
    result = {"records_seen": seen, "records_added": added, "scene_months_rebuilt": rebuilt}
    # the report goes to its own file: piping it through tee mixed it with the progress
    # lines above, and the step that read it back could not parse its own input. Six shards
    # of correct work were discarded because of that.
    if a.report:
        json.dump(result, open(a.report, "w"), indent=1)
    print(json.dumps(result, indent=1), flush=True)


if __name__ == "__main__":
    sys.exit(main())
