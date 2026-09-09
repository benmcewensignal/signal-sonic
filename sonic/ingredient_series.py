"""Monthly series for every named ingredient, per scene.

The app holds each scene's displacement month by month but only current values for the
ingredients, so a question like "what have vocals done this year" can be stated but not
drawn. This publishes the monthly mean of each ingredient, and the near-instrumental
share, which is where the vocal finding actually lives.

  python -m sonic.ingredient_series --db sonic.db --out data/ingredient-series.json
"""
import argparse, collections, json, sqlite3, statistics as st

KEYS = [("vocal_presence", "vocal"), ("drum_density", "drums"), ("drum_swing", "swing"),
        ("bass_weight", "bass"), ("tempo", "tempo")]


def build(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    n2 = c.execute("select count(*) from tracks where analyser_ver like '2%'").fetchone()[0]
    n1 = c.execute("select count(*) from tracks where analyser_id='local' and coalesce(analyser_ver,'1') not like '2%'").fetchone()[0]
    clause = "like '2%'" if n2 > n1 else "not like '2%'"
    M = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in c.execute(f"""select ts.scene, ts.week, t.features from track_scenes ts
                           join tracks t on t.track_id=ts.track_id and t.analyser_id='local'
                           and coalesce(t.analyser_ver,'1') {clause} where ts.week like '____-M__'"""):
        try: M[r["scene"]][r["week"]].append(json.loads(r["features"]))
        except Exception: pass
    out = {}
    for sc, months in M.items():
        ms = [m for m in sorted(months) if len(months[m]) >= 5]
        if len(ms) < 12: continue
        rec = {"months": ms}
        for k, lab in KEYS:
            rec[lab] = [round(st.mean(v), 4) if (v := [f.get(k) for f in months[m] if f.get(k) is not None]) else None for m in ms]
        rec["instrumental_share"] = [
            round(sum(1 for x in v if x < 0.15) / len(v), 4) if (v := [f.get("vocal_presence") for f in months[m] if f.get("vocal_presence") is not None]) else None
            for m in ms]
        rec["n_per_month"] = [len(months[m]) for m in ms]
        out[sc] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/ingredient-series.json")
    a = ap.parse_args()
    d = build(a.db)
    json.dump(d, open(a.out, "w"), separators=(",", ":"))
    print(json.dumps({"scenes": len(d), "months": len(d[list(d)[0]]["months"]) if d else 0}, indent=1))


if __name__ == "__main__":
    main()
