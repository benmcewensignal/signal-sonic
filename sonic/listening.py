"""A blind listening test for the sound axes.

Every claim about how a scene changed rests on an embedding distance. This picks the
records at each end of a scene's own shift axis and resolves their Beatport previews,
so a person can be played two records without labels and asked which is which. If
listeners cannot separate them above chance, the axis is numerology and we should say so.

Selection rules, chosen to be fair rather than flattering:
  - the "new sound" side comes from 2026 records at the 90th percentile of the axis,
    the "old sound" side from the 2024 home window at the 10th, so neither end is an
    outlier and each is drawn from the period it is meant to represent
  - both must be named records with a working preview
  - pairs are emitted with a random side assignment and the answer key held separately

  python -m sonic.listening --db sonic.db --out data/listening-test.json
"""
import argparse, collections, json, random, sqlite3
import numpy as np
from .beatport import get_token, _get

HOME_END = "2025-M05"
NOW_START = "2026-M01"


def preview_url(track_id, token):
    if not str(track_id).startswith("bp:"): return None
    try:
        d = _get(f"/catalog/tracks/{str(track_id).split(':')[-1]}/", token)
        return (d.get("sample_url") or (d.get("preview") or {}).get("mp3", {}).get("url") or "") or None
    except Exception:
        return None


def build(db, per_scene=2, seed=7):
    rng = random.Random(seed)
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    meta = {r["track_id"]: (r["name"], json.loads(r["artists"]) if r["artists"] else [])
            for r in c.execute("select track_id, name, artists from track_meta where name is not null")}
    S = collections.defaultdict(list)
    # the corpus holds two embedding widths while the re-analysis runs: never mix instruments
    n2 = c.execute("select count(*) from tracks where analyser_ver like '2%'").fetchone()[0]
    n1 = c.execute("select count(*) from tracks where analyser_id='local' and coalesce(analyser_ver,'1') not like '2%'").fetchone()[0]
    ver_clause = "like '2%'" if n2 > n1 else "not like '2%'"
    for r in c.execute(f"""select ts.scene, ts.week, ts.track_id, t.features from track_scenes ts
                          join tracks t on t.track_id=ts.track_id and t.analyser_id='local'
                          where ts.week like '____-M__' and coalesce(t.analyser_ver,'1') {ver_clause}"""):
        try: v = np.array(json.loads(r["features"])["embedding"], float)
        except Exception: continue
        S[r["scene"]].append((r["week"], r["track_id"], v / (np.linalg.norm(v) or 1)))
    token = get_token()
    pairs, skipped = [], collections.Counter()
    for scene, rows in sorted(S.items()):
        home = [x for x in rows if x[0] <= HOME_END and x[1] in meta]
        now = [x for x in rows if x[0] >= NOW_START and x[1] in meta]
        if len(home) < 40 or len(now) < 40: skipped["too few named records"] += 1; continue
        H = np.mean([v for _, _, v in home], axis=0); N = np.mean([v for _, _, v in now], axis=0)
        u = (N - H); n = np.linalg.norm(u)
        if n == 0: skipped["no shift"] += 1; continue
        u = u / n
        def pick(pool, pct, want):
            pr = np.array([float(v @ u) for _, _, v in pool])
            target = np.percentile(pr, pct)
            order = np.argsort(np.abs(pr - target))
            out = []
            for i in order[:14]:
                tid = pool[i][1]
                url = preview_url(tid, token)
                if not url: continue
                name, arts = meta[tid]
                out.append({"track_id": tid, "name": name, "artists": arts[:2], "month": pool[i][0],
                            "position": round(float(pr[i]), 3), "preview": url})
                if len(out) >= want: break
            return out
        new_side, old_side = pick(now, 90, per_scene), pick(home, 10, per_scene)
        for idx, (a, b) in enumerate(zip(new_side, old_side)):
            flip = len(pairs) % 2 == 0
            pairs.append({"scene": scene, "A": (b if flip else a), "B": (a if flip else b),
                          "answer": ("B" if flip else "A")})   # which side is the newer sound
        if not new_side or not old_side: skipped["no preview"] += 1
    return {"generated": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
            "protocol": "for each pair, decide which record sounds like the scene's newer sound. "
                        "The axis is audible only if listeners beat chance across scenes.",
            "pairs": pairs, "skipped": dict(skipped)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/listening-test.json")
    ap.add_argument("--per-scene", type=int, default=2)
    a = ap.parse_args()
    out = build(a.db, a.per_scene)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"pairs": len(out["pairs"]), "scenes": len({p["scene"] for p in out["pairs"]}),
                      "skipped": out["skipped"]}, indent=1))


if __name__ == "__main__":
    main()
