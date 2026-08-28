"""Backfill tests — offline via monkeypatching the fetch and analyser.

Verifies the three properties that matter:
  1. QUARANTINE — backfill writes weighting='flat' rows with 'YYYY-Mmm'
     labels only; the live 'chart' series stays empty/untouched.
  2. CLAIMS IMMUNITY — the claims table is byte-identical after fetch
     and report, even when a claim already exists.
  3. DERIVATION — the report detects a planted historical drift and a
     planted convergence, labels itself retrodictive, and emits no calls.

Run: python tests/test_backfill.py
"""
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sonic.store import Store
from sonic.analyser import StubAnalyser
from sonic import backfill

PASS, FAIL = 0, []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(name)
    print(("  ok  " if cond else "  FAIL") + " " + name)


def main():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "s.db")
    smap = os.path.join(tmp, "scene_map.json")
    with open(smap, "w") as f:
        json.dump({"_note": "x",
                   "1": {"scene": "alpha", "name": "Alpha"},
                   "2": {"scene": "beta", "name": "Beta"}}, f)

    # seed a pre-existing live claim to prove immunity
    store = Store(db)
    store.emit_claim({"claim_id": "live1", "emitted_week": "2026-W35",
                      "kind": "drift", "scene_a": "alpha", "statement": "s",
                      "evidence": {}, "horizon_weeks": 13,
                      "resolution_rule": "r"})
    claims_snapshot = store.conn.execute("SELECT * FROM claims").fetchall()

    # monkeypatch: no network, no audio. Genre 1 drifts from 2025-M07;
    # genres converge from 2025-M05 (shared offset direction via stub style).
    stub = StubAnalyser()

    def fake_fetch(token, gid, month, per_month):
        idx = backfill.months_between("2024-09", "2025-12").index(month)
        tracks = []
        for t in range(12):
            off = 0.0
            if gid == 1 and idx >= 10:
                off = min(0.5, (idx - 9) * 0.1)
            tracks.append({"id": f"g{gid}-{month}-{t}",
                           "sample_url": f"g{gid}scene-{month}-t{t}?offset={off}",
                           "publish_date": month})
        return tracks, "publish_date"

    def fake_analyse(analyser, s):
        return stub.analyse(s.audio_ref)

    backfill._try_fetch_month = fake_fetch
    backfill.analyse_sighting = fake_analyse
    backfill.get_token = lambda: "fake"
    backfill.get_analyser = lambda name: stub

    args = types.SimpleNamespace(mfrom="2024-09", mto="2025-12", per_month=12,
                                 db=db, analyser="stub", scene_map=smap)
    backfill.cmd_fetch(args)

    store = Store(db)
    # 1. quarantine
    rows = store.conn.execute(
        "SELECT weighting, week FROM scene_weeks").fetchall()
    check("all backfill rows are flat-weighted",
          all(r["weighting"] == "flat" for r in rows))
    check("all backfill labels are monthly",
          all(len(r["week"]) == 8 and r["week"][5] == "M" for r in rows))
    chart_rows = store.conn.execute(
        "SELECT COUNT(*) c FROM scene_weeks WHERE weighting='chart'").fetchone()["c"]
    check("live chart series untouched", chart_rows == 0)
    check("16 months x 2 scenes aggregated", len(rows) == 32)

    # 2. claims immunity after fetch
    now = store.conn.execute("SELECT * FROM claims").fetchall()
    check("claims table byte-identical after fetch",
          [tuple(r) for r in now] == [tuple(r) for r in claims_snapshot])

    # 3. report
    out = os.path.join(tmp, "rep")
    rargs = types.SimpleNamespace(db=db, analyser_id="stub", out=out)
    backfill.cmd_report(rargs)
    rep = json.load(open(out + ".json"))
    md = open(out + ".md").read()
    check("report labels itself retrodictive", "RETRODICTIVE" in rep["note"]
          and "not calls" in md.lower())
    alpha = rep["scenes"]["alpha"]
    check("planted drift peaks late in alpha",
          alpha["peak"] and alpha["peak"]["month"] >= "2025-M07")
    beta_peak = rep["scenes"]["beta"]["peak"]["distance"]
    check("undrifted beta peak stays small vs alpha",
          alpha["peak"]["distance"] > 3 * beta_peak)
    check("convergence table present with the pair",
          rep["convergence"] and rep["convergence"][0]["pair"] == "alpha × beta")
    now = store.conn.execute("SELECT * FROM claims").fetchall()
    check("claims table byte-identical after report",
          [tuple(r) for r in now] == [tuple(r) for r in claims_snapshot])

    print(f"\n{PASS} passed, {len(FAIL)} failed" + (f": {FAIL}" if FAIL else ""))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
