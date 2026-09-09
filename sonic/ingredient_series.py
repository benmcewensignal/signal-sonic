"""Monthly series for each named ingredient, per scene.

The app holds each scene's displacement month by month but only current values for the
ingredients, so "what have vocals done this year" can be stated as a change and not
drawn. This publishes the series the picker needs, on one analyser version only, since
mixing instruments across a series is meaningless.

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
        rec = {"months": ms, "n": [len(months[m]) for m in ms]}
        for k, lab in KEYS:
            vals = []
            for m in ms:
                v = [f.get(k) for f in months[m] if f.get(k) is not None]
                vals.append(round(st.mean(v), 4) if v else None)
            rec[lab] = vals
        inst = []
        for m in ms:
            v = [f.get("vocal_presence") for f in months[m] if f.get("vocal_presence") is not None]
            inst.append(round(sum(1 for x in v if x < 0.15) / len(v), 4) if v else None)
        rec["instrumental_share"] = inst
        out[sc] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/ingredient-series.json")
    a = ap.parse_args()
    out = build(a.db)
    json.dump(out, open(a.out, "w"), separators=(",", ":"))
    print(json.dumps({"scenes": len(out), "months": len(next(iter(out.values()))["months"]) if out else 0}, indent=1))


if __name__ == "__main__":
    main()
