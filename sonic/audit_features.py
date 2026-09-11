"""Is every block of the feature vector measuring something?

A block whose dimensions all carry the same spread is not describing records: it is being
scaled by something. Twelve of forty-five dimensions were in that state for the life of
analyser 2, because one median and one spread were computed across the whole vector and
applied to families whose natural ranges differ by a factor of fifty. Every distance we
computed included them as dead weight, and an eighteen per cent change in that dead weight
was published as a finding about harmony.

    python -m sonic.audit_features --db sonic.db
"""
import argparse, collections, json, sqlite3, sys
import numpy as np

BLOCKS = {"timbre": range(0, 13), "timbre variability": range(13, 26),
          "harmony": range(26, 38), "texture": range(38, 45)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--limit", type=int, default=20000)
    a = ap.parse_args()
    c = sqlite3.connect(a.db); c.row_factory = sqlite3.Row
    V = []
    for r in c.execute("select features from tracks where analyser_id='local' limit ?", (a.limit,)):
        try: d = json.loads(r["features"])
        except Exception: continue
        e = d.get("embedding")
        if e and len(e) == 45: V.append(e)
    if len(V) < 100:
        print("not enough records to audit"); return 0
    V = np.array(V, dtype=float)
    print(f"{len(V):,} records\n")
    bad = []
    for b, ix in BLOCKS.items():
        sds = V[:, list(ix)].std(0)
        rel = float(sds.std() / (sds.mean() or 1))
        dead = rel < 0.05
        if dead: bad.append(b)
        print(f"  {b:22}{sds.mean():10.4f}  spread-of-spreads {rel:6.3f}   "
              f"{'DEGENERATE' if dead else 'carries structure'}")
    if bad:
        print(f"\n{len(bad)} block(s) measuring nothing: {bad}")
        return 1
    print("\nevery block carries structure")
    return 0


if __name__ == "__main__":
    sys.exit(main())
