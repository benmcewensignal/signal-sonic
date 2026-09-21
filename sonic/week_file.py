"""A chart week as its own file, so it survives whatever happens to sonic.db.

The weekly chart is a snapshot: Beatport serves the top 100 as it stands today, so a week
not captured in its own week is gone. Two things lost weeks. The only trigger was a Sunday
03:00 cron that GitHub did not fire on any of the Sundays checked (6, 13, 20 September), and
every run saved by copying its whole database over main, so a weekly batch running beside
the queue chain would have had its rows written over by the chain's next save, and would
itself have written over the chain's.

So the weekly batch exports the week here and stops saving the database, and every run
imports the week files before it works. A file per week is a new path each time, so no
concurrent save can overwrite it.

    python -m sonic.week_file export --db sonic.db --week 2026-W39 --dir data/weeks
    python -m sonic.week_file import --db sonic.db --dir data/weeks
"""
import argparse, glob, gzip, json, os, sqlite3


def _rows(c, sql, args=()):
    cur = c.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def export(db, week, out_dir):
    c = sqlite3.connect(db)
    ts = _rows(c, "SELECT * FROM track_scenes WHERE week=?", (week,))
    sw = _rows(c, "SELECT * FROM scene_weeks WHERE week=?", (week,))
    ids = sorted({r["track_id"] for r in ts})
    tr = []
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        tr += _rows(c, f"SELECT * FROM tracks WHERE track_id IN ({','.join('?' * len(part))})", part)
    if not ts or not sw:
        raise SystemExit(f"{week}: {len(ts)} chart rows and {len(sw)} scene weeks in the database; "
                         "refusing to write an empty week file")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{week}.json.gz")
    with gzip.open(path, "wt") as f:
        json.dump({"week": week, "track_scenes": ts, "scene_weeks": sw, "tracks": tr}, f)
    print(f"{week}: wrote {len(ts)} chart rows, {len(sw)} scene weeks, {len(tr)} tracks to {path}")
    return path


def _insert(c, table, rows, verb):
    if not rows:
        return 0
    have = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    cols = [k for k in rows[0] if k in have]
    sql = f"INSERT OR {verb} INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    before = c.total_changes
    c.executemany(sql, [[r.get(k) for k in cols] for r in rows])
    return c.total_changes - before


def import_dir(db, in_dir):
    files = sorted(glob.glob(os.path.join(in_dir, "*.json.gz")))
    if not files:
        print("no week files")
        return
    c = sqlite3.connect(db)
    for p in files:
        try:
            with gzip.open(p, "rt") as f:
                d = json.load(f)
        except Exception as e:
            print(f"{os.path.basename(p)}: unreadable ({e}); skipped")
            continue
        with c:
            # tracks: keep what is already there, since a later run may have re-measured it
            nt = _insert(c, "tracks", d.get("tracks", []), "IGNORE")
            nts = _insert(c, "track_scenes", d.get("track_scenes", []), "REPLACE")
            nsw = _insert(c, "scene_weeks", d.get("scene_weeks", []), "REPLACE")
        print(f"{d.get('week')}: {nts} chart rows, {nsw} scene weeks, {nt} new tracks")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["export", "import"])
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--week")
    ap.add_argument("--dir", default="data/weeks")
    a = ap.parse_args()
    if a.cmd == "export":
        if not a.week:
            raise SystemExit("--week is required to export")
        export(a.db, a.week, a.dir)
    else:
        import_dir(a.db, a.dir)


if __name__ == "__main__":
    main()
