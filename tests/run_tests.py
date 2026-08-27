"""Harness. Builds a synthetic 30-week, 3-scene pilot with engineered truths:

  - 'stable'   : no drift planted            -> must stay silent
  - 'drifter'  : offset ramps from week 18   -> drift claim must fire, decomposed
  - 'conv-a/b' : offsets converge from wk 14 -> convergence claim must fire

Plus unit assertions: baseline silence, vintage filtering, claim immutability,
resolution + track record, no-resolution-rule rejection.

Run: python tests/run_tests.py   (from repo root)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sonic.store import Store
from sonic.analyser import StubAnalyser, FeatureVector
from sonic.ingest import FixtureSource
from sonic.run_week import run
from sonic.aggregate import build_fingerprint, fingerprint_distance

PASS, FAIL = 0, []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(name)
    print(("  ok  " if cond else "  FAIL") + " " + name)


def week_label(i):
    return f"2026-W{i:02d}"


def build_fixture(path):
    """30 weeks x 4 scenes x 24 tracks. Offsets encode the planted truths."""
    rows = []
    for w in range(1, 31):
        for scene in ("stable", "drifter", "conv-a", "conv-b"):
            for t in range(24):
                off = 0.0
                if scene == "drifter" and w >= 18:
                    off = min(0.5, (w - 17) * 0.06)          # ramping drift
                if scene == "conv-a" and w >= 14:
                    off = min(0.45, (w - 13) * 0.04)          # both walk the same
                if scene == "conv-b" and w >= 14:
                    off = -0.45 + min(0.9, (w - 13) * 0.07)   # direction from opposite sides
                if scene == "conv-b" and w < 14:
                    off = -0.45                               # starts far away
                rows.append({
                    "track_id": f"{scene}-w{w}-t{t}",
                    "audio_ref": f"{scene}-w{w}-t{t}?offset={off:.3f}",
                    "scene": scene,
                    "week": week_label(w),
                    "source": "fixture:test",
                    "chart_rank": (t + 1) if t < 10 else None,
                })
    with open(path, "w") as f:
        json.dump(rows, f)


def main():
    tmp = tempfile.mkdtemp()
    fx = os.path.join(tmp, "fixture.json")
    db = os.path.join(tmp, "sonic.db")
    build_fixture(fx)

    store = Store(db)
    analyser = StubAnalyser()
    scenes = ["stable", "drifter", "conv-a", "conv-b"]
    src = [FixtureSource(fx)]

    logs = []
    for w in range(1, 31):
        logs.append(run(store, analyser, src, scenes, week_label(w), quiet=True))

    # --- baseline silence: nothing may fire in the first MIN_BASELINE weeks
    early_claims = sum(l["claims_emitted"] for l in logs[:9])
    check("silent while baseline seasons (weeks 1-9)", early_claims == 0)

    # --- drift fires for the drifter, decomposed
    drift_rows = store.conn.execute(
        "SELECT * FROM claims WHERE kind='drift' AND scene_a='drifter'").fetchall()
    check("drift claim fired for planted drifter", len(drift_rows) >= 1)
    if drift_rows:
        ev = json.loads(drift_rows[0]["evidence"])
        check("drift claim carries decomposition", "decomposition" in ev
              and set(ev["decomposition"]) >= {"tempo", "bass_weight"})
        check("drift claim carries resolution rule",
              bool(drift_rows[0]["resolution_rule"]))

    # --- the stable scene must never fire drift
    false_drift = store.conn.execute(
        "SELECT COUNT(*) n FROM claims WHERE kind='drift' AND scene_a='stable'"
    ).fetchone()["n"]
    check("no false drift on stable scene", false_drift == 0)

    # --- convergence fires for the planted pair, and only that pair
    conv = store.conn.execute(
        "SELECT scene_a, scene_b FROM claims WHERE kind='convergence'").fetchall()
    pairs = {tuple(sorted((r["scene_a"], r["scene_b"]))) for r in conv}
    check("convergence fired for conv-a/conv-b", ("conv-a", "conv-b") in pairs)
    check("no convergence claimed for stable pairs",
          all("stable" not in p for p in pairs))

    # --- vintage filtering: a second analyser's rows are invisible to the first
    fv = StubAnalyser().analyse("alien-track")
    fv.analyser_id = "other"
    store.upsert_track("alien-track", fv.to_json(), "other", "9", "fixture:test",
                       week_label(30))
    store.assign_scene("alien-track", "stable", week_label(30), "fixture:test")
    rows = store.scene_track_rows("stable", week_label(30), "stub")
    check("aggregates filter analyser vintage",
          all(r["track_id"] != "alien-track" for r in rows))

    # --- claims are immutable and rules are mandatory
    cid = drift_rows[0]["claim_id"] if drift_rows else None
    if cid:
        store.resolve_claim(cid, "hit", "persisted in fixture")
        try:
            store.resolve_claim(cid, "miss")
            check("resolved claim immutable", False)
        except ValueError:
            check("resolved claim immutable", True)
    try:
        store.emit_claim({"claim_id": "x", "emitted_week": "2026-W30",
                          "kind": "drift", "scene_a": "stable",
                          "statement": "s", "evidence": {}, "horizon_weeks": 13,
                          "resolution_rule": ""})
        check("claim without resolution rule rejected", False)
    except ValueError:
        check("claim without resolution rule rejected", True)

    rec = store.track_record()
    check("track record counts the resolution",
          rec["hits"] == 1 and rec["resolved"] == 1)

    # --- fingerprint sanity: distance to self is ~0, weighted!=flat possible
    tracks = [(StubAnalyser().analyse(f"s{i}"), 1.0) for i in range(10)]
    fp = build_fingerprint(tracks)
    check("fingerprint self-distance ~0",
          fingerprint_distance(fp, fp) < 1e-9)

    print(f"\n{PASS} passed, {len(FAIL)} failed" + (f": {FAIL}" if FAIL else ""))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
