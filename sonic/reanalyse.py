"""Re-analyse tracks whose features came from an older analyser version.

v1 stored a 32-dim embedding that truncated spectral contrast away. v2 keeps all 45
dimensions. Old and new vectors are not comparable, so this rewrites the corpus in
batches, newest first, and never mixes versions in one scene-month.

  python -m sonic.reanalyse --db sonic.db --limit 3000
"""
import argparse, json, sqlite3, time
from .analyser_local import LocalAnalyser
from .store import Store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--limit", type=int, default=3000)
    ap.add_argument("--budget-minutes", type=int, default=200)
    a = ap.parse_args()
    an = LocalAnalyser()
    want = an.version
    c = sqlite3.connect(a.db); c.row_factory = sqlite3.Row
    todo = [r["track_id"] for r in c.execute(
        "select track_id from tracks where analyser_id='local' and analyser_version<>? order by rowid desc limit ?",
        (want, a.limit))]
    print(f"reanalyse: {len(todo)} tracks on an older version (target {want})", flush=True)
    t0 = time.time(); done = err = 0
    for tid in todo:
        if (time.time() - t0) / 60 > a.budget_minutes:
            print("budget reached; dispatch again to continue", flush=True); break
        try:
            row = c.execute("select audio_ref from tracks where track_id=?", (tid,)).fetchone()
            if not row or not row["audio_ref"]: err += 1; continue
            fv = an.analyse(row["audio_ref"])
            c.execute("update tracks set features=?, analyser_version=? where track_id=?",
                      (json.dumps(fv.__dict__ if hasattr(fv, "__dict__") else fv), want, tid))
            done += 1
            if done % 200 == 0: c.commit(); print(f"  {done}/{len(todo)} re-analysed, {err} failed", flush=True)
        except Exception as e:
            err += 1
    c.commit()
    print(f"reanalyse: {done} done, {err} failed, {max(0, len(todo)-done-err)} left", flush=True)


if __name__ == "__main__":
    main()
