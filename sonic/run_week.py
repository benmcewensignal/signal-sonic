"""One weekly batch run, end to end.

    python -m sonic.run_week --week 2026-W35 --scenes garage,funky,gqom \
        --fixture tests/fixtures/pilot.json --analyser stub

Order: ingest -> aggregate -> derivatives -> emit claims -> resolve matured
claims -> print the run report. Matches the forecaster's batch shape.
"""
from __future__ import annotations
import argparse
import json
import itertools
from .store import Store
from .analyser import get_analyser, FeatureVector
from .ingest import FixtureSource, ingest_week
from .aggregate import aggregate_scene_week
from . import derivatives as dv


def run(store: Store, analyser, sources, scenes: list[str], week: str,
        booking_momentum: dict[str, float] | None = None,
        quiet: bool = False) -> dict:
    log: dict = {"week": week, "analyser": analyser.analyser_id}

    # 1. ingest
    log["ingest"] = ingest_week(store, analyser, sources, week)

    # 2. aggregate
    log["aggregated"] = {
        s: aggregate_scene_week(store, s, week, analyser.analyser_id)
        for s in scenes}

    # 3. derivatives -> claims
    claims: list[dict] = []
    histories = {s: store.scene_history(s, analyser.analyser_id) for s in scenes}

    for s in scenes:
        c = dv.detect_drift(s, week, histories[s])
        if c:
            claims.append(c)

    for a, b in itertools.combinations(scenes, 2):
        c = dv.detect_convergence(a, b, week, histories[a], histories[b])
        if c:
            claims.append(c)

    for s in scenes:
        hist = histories[s]
        if hist and len(hist) >= dv.MIN_BASELINE:
            rows = store.scene_track_rows(s, week, analyser.analyser_id)
            tracks = [FeatureVector.from_json(r["features"]) for r in rows]
            centroid = hist[-1][1].get("embedding_centroid") if hist[-1][1] else None
            if tracks and centroid:
                c = dv.detect_novelty(s, week, tracks, centroid)
                if c:
                    claims.append(c)

    if booking_momentum:
        sonic_z = _sonic_momentum_z(histories)
        for s in scenes:
            if s in sonic_z and s in booking_momentum:
                c = dv.detect_divergence(s, week, sonic_z[s], booking_momentum[s])
                if c:
                    claims.append(c)

    emitted = 0
    for c in claims:
        try:
            store.emit_claim(c)
            emitted += 1
        except Exception:
            pass  # duplicate claim_id for same (kind, week, scene): already emitted
    log["claims_emitted"] = emitted
    log["claims_open"] = len(store.open_claims())
    log["track_record"] = store.track_record()

    if not quiet:
        print(json.dumps(log, indent=2))
    return log


def _sonic_momentum_z(histories: dict[str, list]) -> dict[str, float]:
    """Sonic momentum per scene = distance moved over the last 4 weeks,
    z-scored across scenes."""
    from .aggregate import fingerprint_distance
    raw = {}
    for s, h in histories.items():
        if len(h) >= 5:
            raw[s] = fingerprint_distance(h[-5][1], h[-1][1])
    if len(raw) < 2:
        return {}
    vals = list(raw.values())
    mean = sum(vals) / len(vals)
    sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
    return {s: (v - mean) / sd for s, v in raw.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", required=True)
    ap.add_argument("--scenes", required=True)
    ap.add_argument("--source", default="fixture", choices=["fixture", "beatport"])
    ap.add_argument("--fixture")
    ap.add_argument("--scene-map", default="scene_map.json")
    ap.add_argument("--analyser", default="stub")
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--bookings", help="json file: {scene: momentum_z}")
    args = ap.parse_args()

    booking = None
    if args.bookings:
        with open(args.bookings) as f:
            booking = json.load(f)

    if args.source == "beatport":
        from .beatport import BeatportChartSource
        sources = [BeatportChartSource(args.scene_map)]
    else:
        if not args.fixture:
            raise SystemExit("--fixture required with --source fixture")
        sources = [FixtureSource(args.fixture)]
    run(Store(args.db), get_analyser(args.analyser),
        sources, args.scenes.split(","), args.week,
        booking_momentum=booking)


if __name__ == "__main__":
    main()
