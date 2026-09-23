"""Which two dimensions actually separate two genres?

A map with arbitrary axes is decoration. The useful question is: of every pair of measured
dimensions, which plane separates these two scenes best, and is that separation real or the
best of a thousand coincidences?

With forty-five dimensions there are about a thousand pairs. Search them all, score each by
how well a boundary can be drawn in that plane, and test the winner against labels shuffled
at random. If the best real pair does not beat the best shuffled pair, there is no plane in
which these two genres are two things -- which is the strongest form of the overspecification
finding, and a stronger claim than any clustering can make.

    python -m sonic.axis_search --db sonic.db --a tech-house --b bass-house

Nothing here writes to the database. It reports.
"""
import argparse, itertools, json, sqlite3, sys
import numpy as np

NAMED = ["tempo", "drum_density", "drum_swing", "bass_weight", "vocal_presence"]
BLOCK = {"timbre": range(0, 13), "timbre_var": range(13, 26),
         "harmony": range(26, 38), "texture": range(38, 45)}


def label_for(i):
    for name, r in BLOCK.items():
        if i in r:
            return f"{name}[{i - r.start}]"
    return f"dim{i}"


def load(db, scenes, limit=40000):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    X, y = [], []
    q = """select ts.scene, t.features from track_scenes ts
           join tracks t on t.track_id=ts.track_id and t.analyser_id='local'
           where ts.week like '____-M__' and ts.scene in (%s) limit ?""" % ",".join("?" * len(scenes))
    for r in c.execute(q, (*scenes, limit)):
        try: d = json.loads(r["features"])
        except Exception: continue
        e = d.get("embedding")
        if not e or len(e) != 45: continue
        row = list(e) + [d.get(k) if isinstance(d.get(k), (int, float)) else np.nan for k in NAMED]
        X.append(row); y.append(r["scene"])
    return np.array(X, dtype=float), np.array(y)


def separability(X2, y, rng):
    """How well a boundary can be drawn in this plane. A small tree, cross-checked."""
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.model_selection import cross_val_score
    ok = np.isfinite(X2).all(1)
    if ok.sum() < 200: return 0.0
    return float(cross_val_score(DecisionTreeClassifier(max_depth=3, random_state=0),
                                 X2[ok], y[ok], cv=3).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--a", required=True); ap.add_argument("--b", required=True)
    ap.add_argument("--nulls", type=int, default=30)
    ap.add_argument("--top", type=int, default=6)
    a = ap.parse_args()
    rng = np.random.default_rng(0)
    X, y = load(a.db, [a.a, a.b])
    if len(set(y)) < 2:
        print("need both scenes"); return 1
    n_dims = X.shape[1]
    names = [label_for(i) for i in range(45)] + NAMED
    base = max(np.mean(y == a.a), np.mean(y == a.b))
    print(f"{len(y):,} records, {np.mean(y==a.a)*100:.0f}/{np.mean(y==a.b)*100:.0f} split")
    print(f"guessing the commoner gives {base*100:.1f}%\n")

    scores = []
    for i, j in itertools.combinations(range(n_dims), 2):
        s = separability(X[:, [i, j]], y, rng)
        if s > 0: scores.append((s, i, j))
    scores.sort(reverse=True)

    # the null: the best pair out of a thousand, on labels that mean nothing
    best_null = []
    for _ in range(a.nulls):
        yp = rng.permutation(y)
        sub = [tuple(x) for x in rng.choice(len(scores), min(120, len(scores)), replace=False)[:, None]]
        b = max(separability(X[:, [scores[k[0]][1], scores[k[0]][2]]], yp, rng) for k in sub)
        best_null.append(b)
    null95 = float(np.percentile(best_null, 95))

    print(f"  {'plane':34}{'separates':>11}")
    for s, i, j in scores[:a.top]:
        print(f"  {names[i]+' x '+names[j]:34}{s*100:10.1f}%")
    top = scores[0][0]
    print(f"\n  best plane: {top*100:.1f}%")
    print(f"  best plane on shuffled labels: {null95*100:.1f}% (95th of {a.nulls})")
    if top > null95:
        print(f"  SEPARABLE -- {names[scores[0][1]]} against {names[scores[0][2]]}")
    else:
        print("  NO PLANE SEPARATES THESE TWO: on this feature set they are one thing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
