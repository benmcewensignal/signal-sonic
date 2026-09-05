"""Naming the texture residual.

Scene movement is mostly not in the named ingredients (tempo, drums, bass, vocals):
across the field, roughly nine tenths of it sits in a residual we could only call
"production texture". This module gives that residual names, using quantities we can
already derive from stored features — no re-analysis needed.

For each scene it takes the direction of the shift from the 2024 sound to now, projects
every record onto it, and correlates that position against a set of named production
measures. A measure that correlates is a name for what changed.

  python -m sonic.texture --db sonic.db --out data/texture.json
"""
import argparse, collections, json, sqlite3, statistics as st
import numpy as np

# each entry: label, how to compute it from a track's features, and which way to read it
MEASURES = [
    ("dynamic range",   "how far the loudest and quietest parts of the record sit apart",
     lambda f, ec: (ec.max() - ec.min()) if ec is not None else np.nan,
     ("flatter, more uniformly loud", "wider, more light and shade")),
    ("level drift",     "whether the record gets louder or quieter across its length",
     lambda f, ec: (ec[-2:].mean() - ec[:2].mean()) if ec is not None else np.nan,
     ("front-loaded", "building")),
    ("peak position",   "where in the record the loudest moment falls",
     lambda f, ec: (float(ec.argmax()) / (len(ec) - 1)) if ec is not None else np.nan,
     ("peaks earlier", "peaks later")),
    ("step-to-step jolt", "how abruptly the level changes from one section to the next",
     lambda f, ec: float(np.mean(np.abs(np.diff(ec)))) if ec is not None else np.nan,
     ("smoother", "choppier")),
    ("pumping",         "regular rise and fall of level, the mark of heavy sidechain",
     lambda f, ec: float(np.std(np.diff(ec))) if ec is not None else np.nan,
     ("steadier", "more pumped")),
    ("vocal presence",  "how much voice is in the record",
     lambda f, ec: f.get("vocal_presence", np.nan),
     ("less voice", "more voice")),
    ("drum density",    "how busy the drums are",
     lambda f, ec: f.get("drum_density", np.nan), ("sparser drums", "busier drums")),
    ("swing",           "how far off the grid the groove sits",
     lambda f, ec: f.get("drum_swing", np.nan), ("straighter", "swung")),
    ("bass weight",     "how much low end the record carries",
     lambda f, ec: f.get("bass_weight", np.nan), ("lighter low end", "heavier low end")),
    ("tempo",           "beats per minute",
     lambda f, ec: f.get("tempo", np.nan), ("slower", "faster")),
]
MIN_R = 0.20          # below this a correlation is not worth naming
HOME_END = "2025-M05"
NOW_START = "2026-M01"


def corr(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    m = ~(np.isnan(a) | np.isnan(b))
    a, b = a[m], b[m]
    if len(a) < 30 or a.std() == 0 or b.std() == 0: return float("nan"), len(a)
    return float(np.corrcoef(a, b)[0, 1]), len(a)


def build(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    S = collections.defaultdict(list)
    for r in c.execute("""select ts.scene, ts.week, ts.track_id, t.features from track_scenes ts
                          join tracks t on t.track_id=ts.track_id and t.analyser_id='local'
                          where ts.week like '____-M__'"""):
        f = json.loads(r["features"]); v = np.array(f["embedding"])
        S[r["scene"]].append((r["week"], r["track_id"], v / (np.linalg.norm(v) or 1), f))
    out = {}
    for sc, rows in S.items():
        home = [x for x in rows if x[0] <= HOME_END]
        now = [x for x in rows if x[0] >= NOW_START]
        if len(home) < 60 or len(now) < 60: continue
        H = np.mean([v for _, _, v, _ in home], axis=0)
        N = np.mean([v for _, _, v, _ in now], axis=0)
        u = (N - H); nrm = np.linalg.norm(u) or 1e-9; u = u / nrm
        proj = np.array([float(v @ u) for _, _, v, _ in rows])
        named = []
        for label, blurb, fn, (lo, hi) in MEASURES:
            vals = []
            for _, _, _, f in rows:
                ec = f.get("energy_curve")
                ec = np.array(ec, float) if ec and len(ec) == 8 else None
                vals.append(fn(f, ec))
            r_, n = corr(proj, vals)
            if np.isnan(r_) or abs(r_) < MIN_R: continue
            named.append({"measure": label, "what": blurb, "r": round(r_, 2), "n": n,
                          "reading": hi if r_ > 0 else lo})
        named.sort(key=lambda x: -abs(x["r"]))
        # exemplars: the records furthest along the shift in each direction, so a label can be checked by ear
        idx = np.argsort(proj)
        ex_lo = [rows[i][1] for i in idx[:3]]
        ex_hi = [rows[i][1] for i in idx[-3:]][::-1]
        out[sc] = {"named": named, "unnamed": not named,
                   "exemplars": {"toward_the_new_sound": ex_hi, "away_from_it": ex_lo},
                   "records": len(rows)}
    return {"generated": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
            "note": "each measure is correlated with position along the scene's own shift; "
                    "a correlation past 0.2 is a name for part of what changed",
            "scenes": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/texture.json")
    a = ap.parse_args()
    res = build(a.db)
    json.dump(res, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    for sc, v in sorted(res["scenes"].items()):
        top = ", ".join(f"{m['reading']} ({m['r']:+.2f})" for m in v["named"][:3]) or "nothing names it yet"
        print(f"  {sc[:26]:26} {top}")


if __name__ == "__main__":
    main()
