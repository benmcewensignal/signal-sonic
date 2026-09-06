"""Chain-linking across an instrument change.

Analyser v2 keeps 45 embedding dimensions where v1 kept 32, so a v2 vector is not
comparable to a v1 vector and a displacement series computed across both is
meaningless. This module does what a rebased price index does:

  1. builds each scene's series under v1 (from the archive plus any un-re-analysed
     records) and under v2 (from re-analysed records only), each against its own home
     window and its own wobble;
  2. on the months where both versions have enough records, fits v2 = a + b * v1;
  3. publishes, per scene, the link coefficients, the overlap size, and a spliced
     series on the v2 scale — v2 where it exists, linked v1 before that.

It also reports which version every analysis should use right now: until the
re-analysis is complete, that is the majority version, and anything mixing the two
is wrong by construction.

  python -m sonic.chainlink --db sonic.db --out data/chainlink.json
"""
import argparse, collections, json, sqlite3, statistics as st
import numpy as np

HOME_MONTHS = 9
MIN_OVERLAP_RECORDS = 20


def _major(v):
    return "2" if str(v or "1").startswith("2") else "1"


def series_for(rows):
    """rows: list of (month, unit vector). Returns (months, z) against the first nine months."""
    by = collections.defaultdict(list)
    for m, v in rows: by[m].append(v)
    ms = sorted(m for m in by if len(by[m]) >= 5)
    if len(ms) < HOME_MONTHS + 1: return None
    cent = {m: np.mean(by[m], axis=0) for m in ms}
    H = np.mean([cent[m] for m in ms[:HOME_MONTHS]], axis=0)
    d = [1 - float(cent[m] @ H / (np.linalg.norm(cent[m]) * np.linalg.norm(H) or 1e-9)) for m in ms]
    base = st.mean(d[:HOME_MONTHS]); sd = st.stdev(np.diff(d[:HOME_MONTHS])) or 1e-9
    return ms, [(x - base) / sd for x in d], {m: len(by[m]) for m in ms}


def build(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    v1 = collections.defaultdict(list); v2 = collections.defaultdict(list)
    counts = collections.Counter()
    for r in c.execute("""select ts.scene, ts.week, t.features, t.analyser_ver from track_scenes ts
                          join tracks t on t.track_id=ts.track_id and t.analyser_id='local'
                          where ts.week like '____-M__'"""):
        try: v = np.array(json.loads(r["features"])["embedding"], float)
        except Exception: continue
        v = v / (np.linalg.norm(v) or 1)
        (v2 if _major(r["analyser_ver"]) == "2" else v1)[r["scene"]].append((r["week"], v))
        counts[_major(r["analyser_ver"])] += 1
    # v1 vectors that were archived when a record was re-analysed
    try:
        arch = 0
        for r in c.execute("""select ts.scene, ts.week, a.features from features_archive a
                              join track_scenes ts on ts.track_id=a.track_id where ts.week like '____-M__'"""):
            try: v = np.array(json.loads(r["features"])["embedding"], float)
            except Exception: continue
            v1[r["scene"]].append((r["week"], v / (np.linalg.norm(v) or 1))); arch += 1
    except sqlite3.OperationalError:
        arch = 0
    out = {"versions": dict(counts), "archived_v1": arch,
           "use_version": max(counts, key=counts.get) if counts else "1",
           "note": "series are comparable only within a version; use_version is what analyses should filter to until the re-analysis completes",
           "scenes": {}}
    for sc in sorted(set(v1) | set(v2)):
        s1 = series_for(v1.get(sc, [])); s2 = series_for(v2.get(sc, []))
        rec = {"v1_months": len(s1[0]) if s1 else 0, "v2_months": len(s2[0]) if s2 else 0}
        if s1 and s2:
            m1, z1, n1 = s1; m2, z2, n2 = s2
            ov = [m for m in m1 if m in m2 and n1[m] >= MIN_OVERLAP_RECORDS and n2[m] >= MIN_OVERLAP_RECORDS]
            rec["overlap_months"] = len(ov)
            if len(ov) >= 6:
                x = np.array([z1[m1.index(m)] for m in ov]); y = np.array([z2[m2.index(m)] for m in ov])
                b, a = np.polyfit(x, y, 1)
                r = float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else 0.0
                rec["link"] = {"a": round(float(a), 3), "b": round(float(b), 3), "r": round(r, 2)}
                # spliced series on the v2 scale: v2 where it exists, linked v1 before
                spliced = {}
                for m, z in zip(m1, z1): spliced[m] = round(float(a + b * z), 2)
                for m, z in zip(m2, z2): spliced[m] = round(float(z), 2)
                rec["spliced"] = spliced
                rec["quality"] = "good" if r >= 0.8 else ("usable" if r >= 0.5 else "poor: the two instruments disagree")
            else:
                rec["link"] = None; rec["quality"] = "not yet: too few overlap months"
        out["scenes"][sc] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/chainlink.json")
    a = ap.parse_args()
    out = build(a.db)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"versions": out["versions"], "use_version": out["use_version"], "archived_v1": out["archived_v1"],
                      "linked": sum(1 for s in out["scenes"].values() if s.get("link")),
                      "scenes": len(out["scenes"])}, indent=1))


if __name__ == "__main__":
    main()
