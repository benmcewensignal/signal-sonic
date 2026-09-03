"""Calibrate the set-layer matcher against the chronological null: a record
cannot be played in a mix published before its release. Sweeps the rarity-
weighted vote threshold (mix_plays.wvotes) and reports, per threshold, the
share of dated plays that are impossible and the share of plays retained.
Recommends the lowest threshold whose impossible share is under TARGET.

  python -m sonic.calibrate_plays --db sonic.db [--target 0.03]
Writes data/set-calibration.json; artists.py reads the chosen threshold.
"""
import argparse, json, sqlite3, datetime as dt, statistics

def to_date(x):
    try:
        if isinstance(x, (int, float)) or str(x).isdigit():
            return dt.datetime.utcfromtimestamp(float(x)).date()
        return dt.date.fromisoformat(str(x)[:10])
    except Exception:
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--target", type=float, default=0.03)
    ap.add_argument("--out", default="data/set-calibration.json")
    a = ap.parse_args()
    c = sqlite3.connect(a.db); c.row_factory = sqlite3.Row
    cols = [r[1] for r in c.execute("pragma table_info(mix_plays)")]
    if "wvotes" not in cols:
        print("no wvotes column yet: run a mixrescan with the weighted matcher first"); return
    pub = {r["mix_url"]: to_date(r["published"]) for r in c.execute("select mix_url, published from mixes")}
    rel = {r["track_id"]: to_date(r["released"]) for r in c.execute("select track_id, released from track_meta where released is not null")}
    plays = [dict(r) for r in c.execute("select mix_url, track_id, votes, wvotes from mix_plays where wvotes is not null")]
    dated = [(p, (rel[p["track_id"]] - pub[p["mix_url"]]).days) for p in plays if pub.get(p["mix_url"]) and rel.get(p["track_id"])]
    if not dated:
        print("no dated plays"); return
    sweep = []
    ths = sorted(set([0] + [round(x, 1) for x in [q * 0.5 for q in range(0, 80)]]))
    for th in ths:
        kept = [(p, g) for p, g in dated if p["wvotes"] >= th]
        if len(kept) < 20: break
        imp = sum(1 for p, g in kept if g > 7)
        sweep.append({"threshold": th, "retained": len([p for p in plays if p["wvotes"] >= th]), "dated": len(kept),
                      "impossible": imp, "impossible_share": round(imp / len(kept), 4)})
    rec = next((s for s in sweep if s["impossible_share"] <= a.target), None) or sweep[-1]
    out = {"plays": len(plays), "dated_plays": len(dated), "target_impossible_share": a.target,
           "baseline_impossible_share": sweep[0]["impossible_share"] if sweep else None,
           "recommended_wvotes": rec["threshold"], "recommended": rec, "sweep": sweep}
    json.dump(out, open(a.out, "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "sweep"}, indent=1))
    print("threshold  retained  impossible%")
    for s in sweep[::4]: print(f"  {s['threshold']:6.1f}  {s['retained']:8}  {s['impossible_share']*100:6.1f}")

if __name__ == "__main__":
    main()
