"""Import converted features from the worker repo.

The feature worker re-analyses records on its own runner and publishes jsonl. This takes
those files and applies them to the database, archiving the old vector first so the
chain-link keeps its overlap. It is the only place worker output touches sonic.db.

It refuses to import anything it cannot verify: a row must name its analyser version,
carry an embedding of the width that version produces, and belong to a record we hold.

  python -m sonic.import_features --db sonic.db
"""
import argparse, json, os, sqlite3, time, urllib.request

RAW = "https://raw.githubusercontent.com/benmcewensignal/signal-sonic-features/main/"
INDEX = "https://api.github.com/repos/benmcewensignal/signal-sonic-features/contents/out"
EXPECTED_DIM = 45          # analyser v2


def fetch_index():
    req = urllib.request.Request(INDEX, headers={"User-Agent": "signal-sonic/import",
                                                 "Accept": "application/vnd.github+json"})
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok: req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return [f["name"] for f in json.loads(r.read()) if f["name"].startswith("features-") and f["name"].endswith(".jsonl")]


def fetch(name):
    req = urllib.request.Request(RAW + "out/" + name, headers={"User-Agent": "signal-sonic/import"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    a = ap.parse_args()
    c = sqlite3.connect(a.db); c.row_factory = sqlite3.Row
    c.execute("""create table if not exists features_archive(
        track_id text, analyser_ver text, features text, archived_at real,
        primary key (track_id, analyser_ver))""")
    c.execute("""create table if not exists imported_files(name text primary key, rows integer, at real)""")
    c.commit()
    seen = {r[0] for r in c.execute("select name from imported_files")}
    try:
        names = [n for n in fetch_index() if n not in seen]
    except Exception as e:
        print(f"import: could not list the worker repo ({type(e).__name__}); nothing to do"); return
    if not names:
        print("import: no new files"); return
    total = bad = 0
    for name in names:
        try: text = fetch(name)
        except Exception as e:
            print(f"  {name}: {type(e).__name__}"); continue
        n = 0
        for line in text.splitlines():
            if not line.strip(): continue
            try:
                rec = json.loads(line)
                tid, ver, feat = rec["track_id"], rec["analyser_version"], rec["features"]
                emb = feat.get("embedding")
                if not isinstance(emb, list) or len(emb) != EXPECTED_DIM:
                    bad += 1; continue                       # never import a vector of the wrong width
                old = c.execute("select features, analyser_ver from tracks where track_id=?", (tid,)).fetchone()
                if not old:
                    bad += 1; continue                        # a record we do not hold
                if old["features"]:
                    c.execute("insert or ignore into features_archive values(?,?,?,?)",
                              (tid, old["analyser_ver"] or "1", old["features"], time.time()))
                c.execute("update tracks set features=?, analyser_ver=? where track_id=?",
                          (json.dumps(feat), ver, tid))
                n += 1
            except Exception:
                bad += 1
        c.execute("insert or replace into imported_files values(?,?,?)", (name, n, time.time()))
        c.commit(); total += n
        print(f"  {name}: {n} applied", flush=True)
    v2 = c.execute("select count(*) from tracks where analyser_ver like '2%'").fetchone()[0]
    print(json.dumps({"files": len(names), "applied": total, "rejected": bad, "records_on_v2": v2}, indent=1))


if __name__ == "__main__":
    main()
