"""Does the sound still say what charts, on a week the model has never seen?

We found that a record's timbre separates charted from uncharted about seven times in ten, and
that it survives being asked about a scene it was never trained on. The obvious next question
is whether it survives a different week, and we could not ask it: we held one usable week.

So this asks it automatically whenever another week arrives. Train on every chart week but the
newest, test on the newest, and write the answer down. If the number holds the finding is real.
If it collapses, we published something that was true of one Sunday.

    python -m sonic.chart_test --db sonic.db
"""
import argparse, json, os, sqlite3, sys
import numpy as np


def _emb(f):
    try:
        e = json.loads(f).get("embedding")
        return np.array(e, float) if e and len(e) == 45 else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--out", default="out/chart-test.json")
    ap.add_argument("--min-entries", type=int, default=300)
    a = ap.parse_args()

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    c = sqlite3.connect(a.db)
    c.row_factory = sqlite3.Row
    weeks = [r[0] for r in c.execute(
        "select week from track_scenes where chart_rank is not null "
        "group by week having count(*) >= ? order by week", (a.min_entries,))]
    if len(weeks) < 2:
        print(f"only {len(weeks)} chart week(s) with {a.min_entries}+ entries: "
              f"the out-of-sample test needs two. Nothing to do yet.", flush=True)
        json.dump({"weeks": weeks, "status": "waiting"}, open(a.out, "w"))
        return 0

    test_week, train_weeks = weeks[-1], weeks[:-1]
    print(f"train on {train_weeks}, test on {test_week}", flush=True)

    charted = {r[0] for r in c.execute("select track_id from track_scenes where chart_rank is not null")}
    months = [r[0] for r in c.execute(
        "select week from track_scenes where week like '____-M__' group by week order by week desc limit 2")]

    def rows_for(weeks_sel):
        out = []
        q = ",".join("?" * len(weeks_sel))
        for r in c.execute(f"""select ts.scene, t.features from track_scenes ts
                               join tracks t on t.track_id = ts.track_id and t.analyser_id='local'
                               where ts.week in ({q}) and ts.chart_rank is not null""", weeks_sel):
            e = _emb(r["features"])
            if e is not None:
                out.append((r["scene"], e, 1))
        return out

    neg = []
    if months:
        q = ",".join("?" * len(months))
        for r in c.execute(f"""select ts.scene, ts.track_id, t.features from track_scenes ts
                               join tracks t on t.track_id = ts.track_id and t.analyser_id='local'
                               where ts.week in ({q})""", months):
            if r["track_id"] in charted:
                continue
            e = _emb(r["features"])
            if e is not None:
                neg.append((r["scene"], e, 0))
    if len(neg) < 200:
        print(f"only {len(neg)} uncharted records to compare against: not enough", flush=True)
        json.dump({"status": "too few negatives", "n": len(neg)}, open(a.out, "w"))
        return 1

    tr = rows_for(train_weeks) + neg
    te = rows_for([test_week])
    if len(te) < 100:
        print(f"only {len(te)} charted records in {test_week}", flush=True)
        json.dump({"status": "test week too small", "n": len(te)}, open(a.out, "w"))
        return 1

    # the negatives are shared, so hold half of them out for the test rather than reusing them
    rng = np.random.default_rng(0)
    idx = rng.permutation(len(neg))
    half = len(neg) // 2
    neg_tr = [neg[i] for i in idx[:half]]
    neg_te = [neg[i] for i in idx[half:]]
    tr = rows_for(train_weeks) + neg_tr
    te = te + neg_te

    Xtr = np.array([r[1] for r in tr]); Ytr = np.array([r[2] for r in tr])
    Xte = np.array([r[1] for r in te]); Yte = np.array([r[2] for r in te])
    m_, s_ = Xtr.mean(0), Xtr.std(0) + 1e-9
    clf = LogisticRegression(max_iter=3000, C=0.2, class_weight="balanced").fit((Xtr - m_) / s_, Ytr)
    auc = float(roc_auc_score(Yte, clf.decision_function((Xte - m_) / s_)))
    # what nothing looks like, through the same procedure
    sh = float(roc_auc_score(rng.permutation(Yte), clf.decision_function((Xte - m_) / s_)))

    res = {"trained_on": train_weeks, "tested_on": test_week,
           "charted_in_test": int(Yte.sum()), "uncharted_in_test": int((Yte == 0).sum()),
           "auc": round(auc, 4), "shuffled": round(sh, 4),
           "verdict": ("holds" if auc > 0.60 else ("weakened" if auc > 0.55 else "does not hold"))}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(res, open(a.out, "w"), indent=1)
    print(f"\n  tested on {test_week}: AUC {auc:.3f} against {sh:.3f} shuffled")
    print(f"  {res['charted_in_test']} charted, {res['uncharted_in_test']} not")
    print(f"  VERDICT: {res['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
