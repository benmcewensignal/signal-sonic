"""One shard of a deepening pass: fetch and analyse a slice of the scenes, write a file.

The pipeline analyses records one at a time on one runner, which is fine for a weekly
top-up and hopeless for taking two years from 40 records a scene-month to 150: about
26,000 records, or three days.

The work splits perfectly by scene, and GitHub gives a public repository twenty
concurrent jobs. What does not split is the database: it is one SQLite file committed to
the repository, so parallel writers would clobber each other. So each shard writes a
jsonl of what it measured and a single merge step folds them in afterwards, in order.
That is the same shape the features worker already uses, which is the one part of this
pipeline that has never lost data.

  python -m sonic.deepen_shard --shard 0 --of 6 --from 2026-05 --to 2026-08 --per-month 150
"""
import argparse, json, os, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor

from .analyser import get_analyser
from .backfill import _try_fetch_month, _fetch_preview
from .beatport import get_token, analyse_sighting
from .ingest import TrackSighting
from .store import Store


def months_between(mf, mt):
    y, m = int(mf[:4]), int(mf[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= mt:
        out.append(f"{y:04d}-M{m:02d}")
        m += 1
        if m > 12: m, y = 1, y + 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--of", type=int, required=True)
    ap.add_argument("--from", dest="mfrom", required=True)
    ap.add_argument("--to", dest="mto", required=True)
    ap.add_argument("--per-month", type=int, default=150)
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--out", default="out")
    ap.add_argument("--budget-minutes", type=int, default=100)
    a = ap.parse_args()

    with open("scene_map.json") as f:
        scene_map = json.load(f)
    genres = {int(k): v for k, v in scene_map.items() if not k.startswith("_")}
    items = sorted(genres.items(), key=lambda kv: kv[1]["scene"])
    mine = [(gid, cfg) for i, (gid, cfg) in enumerate(items) if i % a.of == a.shard]
    print(f"shard {a.shard} of {a.of}: {[c['scene'] for _, c in mine]}", flush=True)

    store = Store(a.db)                      # read only: what we already hold
    have = {r[0] for r in store.conn.execute(
        "SELECT track_id FROM tracks WHERE analyser_id='local'")}
    analyser = get_analyser("local")
    token = get_token()
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"deepen-shard-{a.shard}.jsonl")
    t0 = time.time(); wrote = skipped = 0

    with open(path, "w") as out:
        for month in months_between(a.mfrom, a.mto):
            for gid, cfg in mine:
                if (time.time() - t0) / 60 > a.budget_minutes:
                    print("budget reached", flush=True); break
                depth = store.conn.execute(
                    "SELECT COUNT(*) FROM track_scenes WHERE week=? AND scene=?",
                    (month, cfg["scene"])).fetchone()[0]
                if depth >= int(a.per_month * 0.9):
                    continue
                try:
                    tracks, _ = _try_fetch_month(token, gid, month, a.per_month)
                except Exception as e:
                    print(f"  {month} {cfg['scene']}: fetch failed: {type(e).__name__}", flush=True)
                    continue
                todo = [t for t in tracks if f"bp:{t['id']}" not in have]
                # fetch the next previews while this one is analysed
                pool = ThreadPoolExecutor(max_workers=4)
                pending = {}
                for t in todo:
                    u = (t.get("sample_url") or (t.get("preview") or {}).get("mp3", {}).get("url") or "")
                    if u: pending[t["id"]] = pool.submit(_fetch_preview, u)
                n = 0
                for t in todo:
                    if (time.time() - t0) / 60 > a.budget_minutes:
                        print("  budget reached mid-month", flush=True); break
                    tid = f"bp:{t['id']}"
                    fut = pending.get(t["id"])
                    local = None
                    if fut is not None:
                        try: local = fut.result(timeout=90)
                        except Exception: local = None
                    ref = local or (t.get("sample_url") or (t.get("preview") or {}).get("mp3", {}).get("url") or "")
                    if not ref:
                        skipped += 1; continue
                    s = TrackSighting(track_id=tid, audio_ref=ref, scene=cfg["scene"], week=month,
                                      source=f"beatport:deepen:genre{gid}", chart_rank=None)
                    try:
                        fv = analyse_sighting(analyser, s)
                        out.write(json.dumps({"track_id": tid, "scene": cfg["scene"], "week": month,
                                              "source": s.source, "analyser_ver": analyser.version,
                                              "features": json.loads(fv.to_json())}) + "\n")
                        wrote += 1; n += 1; have.add(tid)
                    except Exception as e:
                        skipped += 1
                        if skipped <= 3:
                            print(f"    skip {tid}: {type(e).__name__}: {str(e)[:70]}", flush=True)
                    finally:
                        if local:
                            try: os.unlink(local)
                            except OSError: pass
                pool.shutdown(wait=False)
                out.flush()
                print(f"  {month} {cfg['scene']}: {n} analysed (had {depth})", flush=True)
    print(json.dumps({"shard": a.shard, "written": wrote, "skipped": skipped,
                      "minutes": round((time.time() - t0) / 60, 1), "file": path}), flush=True)


if __name__ == "__main__":
    sys.exit(main())
