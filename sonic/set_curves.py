"""How a DJ's sets move over time: each measure by position in the set, across a DJ's sets.

Reads data/tracklists/<dj>.json (records at their minute) and each matched record's measures,
from the corpus database or data/tracklists/measured.jsonl, on ONE analyser version (records on
another version are counted and left out). Position is minute over the set's length (the last
stamp plus five minutes); sets without stamps use running order. Uncertainty resamples whole
sets, since records inside a set are not independent: the unit is the DJ's set, not the record.
  python -m sonic.set_curves --db sonic.db --ver 3.0 --djs carl-cox,andy-c --reader site/data/reader.json
"""
import argparse, json, os, sqlite3
import numpy as np

MEASURES = ("tempo", "bass_weight", "loudness", "drum_density", "vocal_presence", "driving", "defined")
BINS = 10


def features(db, ver, want):
    F = {}
    if os.path.exists(db):
        q = ",".join("?" * len(want))
        for tid, v, f in sqlite3.connect(db).execute(f"select track_id, analyser_ver, features from tracks where analyser_id='local' and track_id in ({q})", list(want)):
            if str(v).startswith(ver):
                try: F[tid] = json.loads(f)
                except Exception: pass
    p = "data/tracklists/measured.jsonl"
    if os.path.exists(p):
        for ln in open(p):
            r = json.loads(ln)
            if r["track_id"] in want and str(r.get("analyser_ver", "")).startswith(ver): F.setdefault(r["track_id"], r["features"])
    return F


def values(f, ax):
    tp = f.get("tempo") or 0; tp = tp * 2 if 0 < tp < 100 else tp
    out = {"tempo": tp or None, "bass_weight": f.get("bass_weight"), "loudness": f.get("loudness"),
           "drum_density": f.get("drum_density"), "vocal_presence": f.get("vocal_presence")}
    e = f.get("embedding")
    if ax and e and len(e) == 45:
        r = np.array(e, float) - ax["mu_raw"]; out["driving"] = float(r @ ax["ax1"]); out["defined"] = float(r @ ax["ax2"])
    return out


def curves(dj_file, db, ver, ax, rng):
    D = json.load(open(dj_file)); sets = D["sets"]
    want = {r["bp"] for s in sets for r in s["records"] if r.get("bp")}
    F = features(db, ver, want)
    per_set = []; other_ver = len(want - set(F)); stamped = 0
    for s in sets:
        recs = s["records"]; mins = [r["minute"] for r in recs if r.get("minute") is not None]
        use_min = len(mins) >= max(3, 0.6 * len(recs)); stamped += use_min
        length = (max(mins) + 5) if use_min else len(recs)
        pts = []
        for i, r in enumerate(recs):
            if not r.get("bp") or r["bp"] not in F: continue
            pos = (r["minute"] / length) if (use_min and r.get("minute") is not None) else (i + 0.5) / len(recs)
            pts.append((min(pos, 0.999), values(F[r["bp"]], ax)))
        if len(pts) >= 6: per_set.append((s["title"], pts))
    res = {"dj": D["dj"], "sets_used": len(per_set), "sets_total": len(sets), "sets_timed": stamped,
           "records_measured": sum(len(p) for _, p in per_set), "records_other_version": other_ver, "curves": {}, "slopes": {}}
    for m in MEASURES:
        # the set-level curve: each set's mean in each tenth, then the mean over sets; bands by resampling sets
        grid = np.full((len(per_set), BINS), np.nan); slopes = []
        for k, (_, pts) in enumerate(per_set):
            xs = np.array([p for p, v in pts if v.get(m) is not None]); ys = np.array([v[m] for p, v in pts if v.get(m) is not None], float)
            if len(xs) < 6: continue
            for b in range(BINS):
                sel = (xs >= b / BINS) & (xs < (b + 1) / BINS)
                if sel.any(): grid[k, b] = ys[sel].mean()
            if np.ptp(xs) > 0: slopes.append(float(np.polyfit(xs, ys, 1)[0]))
        if not np.isfinite(grid).any(): continue
        # 90% bands from the t distribution over sets: resampling twenty sets ran 14% false alarms
        # against a nominal 10% on orderless test sets; t intervals hold the rate
        from scipy.stats import t as T
        mean = np.nanmean(grid, 0); n = np.sum(np.isfinite(grid), 0)
        se = np.nanstd(grid, 0, ddof=1) / np.sqrt(np.maximum(n, 1)); tq = T.ppf(0.95, np.maximum(n - 1, 1))
        lo, hi = mean - tq * se, mean + tq * se
        res["curves"][m] = {"mean": [round(float(x), 4) for x in mean], "lo": [round(float(x), 4) for x in lo], "hi": [round(float(x), 4) for x in hi]}
        if slopes:
            sl = np.array(slopes); se = sl.std(ddof=1) / np.sqrt(len(sl)); tq = T.ppf(0.95, len(sl) - 1)
            res["slopes"][m] = {"start_to_end": round(float(sl.mean()), 4), "lo": round(float(sl.mean() - tq * se), 4),
                                "hi": round(float(sl.mean() + tq * se), 4), "sets": len(sl)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--ver", default="3.0")
    ap.add_argument("--djs", default="carl-cox,andy-c"); ap.add_argument("--reader", default="")
    ap.add_argument("--out", default="data/tracklists/curves.json")
    a = ap.parse_args(); rng = np.random.default_rng(0)
    ax = None
    if a.reader and os.path.exists(a.reader):
        d = json.load(open(a.reader))
        if str(d.get("instrument", {}).get("analyser", "")).startswith(a.ver):
            ax = {k: np.array(d[k]) for k in ("mu_raw", "ax1", "ax2")}
    out = {"analyser": a.ver, "axes": bool(ax), "djs": []}
    for slug in [s.strip() for s in a.djs.split(",") if s.strip()]:
        f = f"data/tracklists/{slug}.json"
        if not os.path.exists(f): print(f"no tracklists for {slug}"); continue
        r = curves(f, a.db, a.ver, ax, rng); out["djs"].append(r)
        sl = "; ".join(f"{m} {v['start_to_end']:+.3g} [{v['lo']:+.3g}, {v['hi']:+.3g}]" for m, v in r["slopes"].items())
        msg = f"{r['dj']}: {r['sets_used']} of {r['sets_total']} sets usable ({r['sets_timed']} timed), {r['records_measured']} records on {a.ver} ({r['records_other_version']} on another version left out). Start to end: {sl}"
        print(msg); print(f"::notice title=set curves {r['dj']}::{msg}")
    json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
