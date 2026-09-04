"""How much each scene actually releases, month by month, and what moves first.

Our corpus samples 40-65 records per scene-month, so counting it measures our
sampling. Beatport reports the true size of every genre-month in the same
response, and the backfill now stores it in scene_supply. This module turns that
into a popularity series and runs the one causal-shaped test the data supports:
does a scene's sound move before its output grows, or after?

  python -m sonic.supply --db sonic.db --out data/supply.json
"""
import argparse, json, math, sqlite3, statistics as st, collections


def load_scene_map(path="scene_map.json"):
    m = json.load(open(path))
    return {str(k): v["scene"] for k, v in m.items() if not k.startswith("_")}


def displacement_series(db):
    """Per scene, monthly distance from its own 2024 home, in its own wobble units."""
    import numpy as np
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    E = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in c.execute("""select ts.scene, ts.week, t.features from track_scenes ts
                          join tracks t on t.track_id=ts.track_id and t.analyser_id='local'
                          where ts.week like '____-M__'"""):
        try: v = np.array(json.loads(r["features"])["embedding"])
        except Exception: continue
        E[r["scene"]][r["week"]].append(v / (np.linalg.norm(v) or 1))
    out = {}
    for sc, months in E.items():
        ms = sorted(months)
        if len(ms) < 12: continue
        cent = {m: np.mean(months[m], axis=0) for m in ms}
        home = np.mean([cent[m] for m in ms[:9]], axis=0)
        ser = [1 - float(cent[m] @ home / ((np.linalg.norm(cent[m]) or 1) * (np.linalg.norm(home) or 1))) for m in ms]
        diffs = [b - a for a, b in zip(ser, ser[1:])]
        sd = st.stdev(diffs) if len(diffs) > 2 else 1e-9
        base = st.mean(ser[:9])
        out[sc] = {m: (v - base) / (sd or 1e-9) for m, v in zip(ms, ser)}
    return out


def supply_series(db, gmap):
    c = sqlite3.connect(db)
    try:
        rows = c.execute("select genre_id, month, total from scene_supply").fetchall()
    except sqlite3.OperationalError:
        return {}
    out = collections.defaultdict(dict)
    for gid, month, total in rows:
        sc = gmap.get(str(gid))
        if sc and total: out[sc][month] = int(total)
    return out


def corr(a, b):
    if len(a) < 6: return None
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return round(num / den, 2) if den else None


def lead_lag(disp, supply):
    """Pooled across scenes: correlate monthly change in sound with monthly change in
    output at lags -3..+3. A positive correlation at a negative lag means the sound
    moved first."""
    out = {}
    for lag in range(-3, 4):
        xs, ys = [], []
        for sc in disp:
            if sc not in supply: continue
            ms = sorted(set(disp[sc]) & set(supply[sc]))
            if len(ms) < 8: continue
            d = [disp[sc][m] for m in ms]; s = [supply[sc][m] for m in ms]
            dd = [b - a for a, b in zip(d, d[1:])]
            ds = [(b - a) / max(1, a) for a, b in zip(s, s[1:])]
            for i in range(len(dd)):
                j = i + lag
                if 0 <= j < len(ds): xs.append(dd[i]); ys.append(ds[j])
        if len(xs) >= 20: out[lag] = {"n": len(xs), "r": corr(xs, ys)}
    return out


def build(db, gmap_path="scene_map.json"):
    gmap = load_scene_map(gmap_path)
    supply = supply_series(db, gmap)
    disp = displacement_series(db)
    scenes = {}
    all_months = sorted({m for d in supply.values() for m in d})
    totals = {m: sum(d.get(m, 0) for d in supply.values()) for m in all_months}
    for sc, ser in supply.items():
        ms = sorted(ser)
        if len(ms) < 6: continue
        share = {m: (ser[m] / totals[m] if totals.get(m) else None) for m in ms}
        recent = [ser[m] for m in ms[-3:]]; prior = [ser[m] for m in ms[-15:-3]] or recent
        sh_recent = [share[m] for m in ms[-3:] if share[m] is not None]
        sh_prior = [share[m] for m in ms[-15:-3] if share[m] is not None] or sh_recent
        scenes[sc] = {
            "months": ms, "total": [ser[m] for m in ms],
            "share": [None if share[m] is None else round(share[m], 4) for m in ms],
            "growth_pct": round((st.mean(recent) / st.mean(prior) - 1) * 100, 1) if prior and st.mean(prior) else None,
            "share_change_pp": round((st.mean(sh_recent) - st.mean(sh_prior)) * 100, 2) if sh_prior else None,
            "latest_total": ser[ms[-1]], "latest_share": round(share[ms[-1]], 4) if share[ms[-1]] is not None else None,
        }
    ranked = sorted(scenes.items(), key=lambda kv: -(kv[1]["share_change_pp"] or -99))
    return {"generated": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
            "note": "totals are Beatport's own count of records released per genre-month; our corpus only samples them",
            "months": all_months, "scenes": scenes,
            "growing": [{"scene": k, "share_change_pp": v["share_change_pp"], "growth_pct": v["growth_pct"]} for k, v in ranked[:6]],
            "shrinking": [{"scene": k, "share_change_pp": v["share_change_pp"], "growth_pct": v["growth_pct"]} for k, v in ranked[-6:]],
            "lead_lag": lead_lag(disp, supply),
            "lead_lag_note": "monthly change in sound against monthly change in output, pooled; negative lag = sound moved first"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/supply.json")
    a = ap.parse_args()
    out = build(a.db)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"scenes": len(out["scenes"]), "months": len(out["months"]),
                      "growing": out["growing"][:3], "lead_lag": out["lead_lag"]}, indent=1))


if __name__ == "__main__":
    main()
