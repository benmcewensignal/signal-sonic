"""Derivative engine. Four readings per scene per week, each emitted as a
claim object with horizon + resolution rule, or not emitted at all.

Baselines: trailing window of scene_weeks (default 26, min 8 to speak).
The first quarter therefore emits nothing — silence while the baseline
seasons is by design.
"""
from __future__ import annotations
import hashlib
import json
import math
from .aggregate import fingerprint_distance, decompose_drift, SCALARS
from .analyser import FeatureVector

BASELINE_WEEKS = 26
MIN_BASELINE = 8
DRIFT_THRESHOLD = 0.05          # fingerprint distance vs own baseline
CONVERGENCE_DROP = 0.20         # fractional shrink in pairwise distance
NOVELTY_MIN_CLUSTER = 5         # tracks forming a persistent new cluster
NOVELTY_MIN_DIST = 0.35         # how far from centroid a cluster must sit
DIVERGENCE_GAP = 0.5            # |sonic momentum z - booking momentum z|


def _claim_id(kind: str, week: str, a: str, b: str | None = None) -> str:
    raw = f"{kind}|{week}|{a}|{b or ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _pooled_fp(history: list[tuple[str, dict]]) -> dict | None:
    """Baseline = pooled fingerprint over the window: scalar means averaged,
    embedding centroids averaged then re-normalised. Pooling divides noise
    by sqrt(weeks); a single reference week does not."""
    if len(history) < MIN_BASELINE:
        return None
    fps = [fp for _, fp in history if fp]
    if not fps:
        return None
    n = len(fps)
    out: dict = {"n": sum(f.get("n", 0) for f in fps)}
    for name in SCALARS:
        out[name] = {"mean": sum(f[name]["mean"] for f in fps) / n,
                     "p10": 0.0, "p50": 0.0, "p90": 0.0}
    dim = len(fps[0]["embedding_centroid"])
    cen = [sum(f["embedding_centroid"][i] for f in fps) / n for i in range(dim)]
    norm = math.sqrt(sum(c * c for c in cen)) or 1.0
    out["embedding_centroid"] = [c / norm for c in cen]
    out["embedding_dispersion"] = sum(f.get("embedding_dispersion", 0.0) for f in fps) / n
    out["energy_curve"] = [sum(f["energy_curve"][i] for f in fps) / n for i in range(8)]
    return out


# -- DRIFT ------------------------------------------------------------------

def detect_drift(scene: str, week: str, history: list[tuple[str, dict]]) -> dict | None:
    """history: trailing scene_weeks INCLUDING current week last.
    Persistence rule: the current AND previous week must both clear the
    threshold against the same baseline — one noisy week never ships."""
    if len(history) < MIN_BASELINE + 2:
        return None
    baseline = _pooled_fp(history[:-2][-BASELINE_WEEKS:])
    if baseline is None:
        return None
    current = history[-1][1]
    previous = history[-2][1]
    d = fingerprint_distance(baseline, current)
    d_prev = fingerprint_distance(baseline, previous)
    if not (d > DRIFT_THRESHOLD and d_prev > DRIFT_THRESHOLD):
        return None
    decomp = decompose_drift(baseline, current)
    movers = sorted(decomp.items(), key=lambda kv: -abs(kv[1]))[:3]
    desc = ", ".join(f"{k} {v:+.0%}" for k, v in movers if abs(v) > 0.02)
    return {
        "claim_id": _claim_id("drift", week, scene),
        "emitted_week": week,
        "kind": "drift",
        "scene_a": scene,
        "statement": f"{scene}'s sound is moving away from its own baseline ({desc})",
        "evidence": {"distance": d, "decomposition": decomp},
        "horizon_weeks": 13,
        "resolution_rule": (
            f"HIT if the {movers[0][0]} shift persists in the same direction in the "
            f"chart-weighted fingerprint 13 weeks out; MISS if it reverts to baseline."),
    }


# -- CONVERGENCE ------------------------------------------------------------

def detect_convergence(scene_a: str, scene_b: str, week: str,
                       hist_a: list[tuple[str, dict]],
                       hist_b: list[tuple[str, dict]]) -> dict | None:
    if len(hist_a) < MIN_BASELINE + 1 or len(hist_b) < MIN_BASELINE + 1:
        return None
    weeks = min(len(hist_a), len(hist_b))
    ha, hb = hist_a[-weeks:], hist_b[-weeks:]
    # smooth both endpoints over 4-week windows — single-week endpoints
    # turn one noisy centroid into a fake trend
    win = min(4, weeks // 2)
    early_ds = [fingerprint_distance(ha[i][1], hb[i][1]) for i in range(win)]
    late_ds = [fingerprint_distance(ha[-1 - i][1], hb[-1 - i][1]) for i in range(win)]
    early = sum(early_ds) / win
    late = sum(late_ds) / win
    if math.isnan(early) or math.isnan(late) or early <= 0:
        return None
    drop = (early - late) / early
    if drop < CONVERGENCE_DROP:
        return None
    return {
        "claim_id": _claim_id("conv", week, scene_a, scene_b),
        "emitted_week": week,
        "kind": "convergence",
        "scene_a": scene_a,
        "scene_b": scene_b,
        "statement": (
            f"{scene_a} and {scene_b} are converging sonically "
            f"(pairwise distance down {drop:.0%} over {weeks} weeks)"),
        "evidence": {"early": early, "late": late, "drop": drop, "weeks": weeks},
        "horizon_weeks": 26,
        "resolution_rule": (
            "HIT if within 26 weeks either (a) pairwise distance shrinks further, or "
            "(b) cross-scene bookings (artists from one billed on the other's nights) "
            "rise vs the trailing quarter; MISS if distance re-widens past the early value."),
    }


# -- NOVELTY ----------------------------------------------------------------

def detect_novelty(scene: str, week: str, tracks: list[FeatureVector],
                   centroid: list[float]) -> dict | None:
    """Single-link greedy clustering of the tracks that sit far from the scene
    centroid. Deliberately simple: a persistent far-cluster is the alert; taste
    is not claimed."""
    if len(tracks) < NOVELTY_MIN_CLUSTER * 2:
        return None

    def cdist(a, b):
        return 1.0 - sum(x * y for x, y in zip(a, b))

    outliers = [t for t in tracks if cdist(t.embedding, centroid) > NOVELTY_MIN_DIST]
    if len(outliers) < NOVELTY_MIN_CLUSTER:
        return None
    # are the outliers near EACH OTHER (a cluster) or just noise?
    cen = [sum(t.embedding[i] for t in outliers) / len(outliers)
           for i in range(len(centroid))]
    norm = math.sqrt(sum(c * c for c in cen)) or 1.0
    cen = [c / norm for c in cen]
    tight = [t for t in outliers if cdist(t.embedding, cen) < NOVELTY_MIN_DIST * 0.6]
    if len(tight) < NOVELTY_MIN_CLUSTER:
        return None
    return {
        "claim_id": _claim_id("novel", week, scene),
        "emitted_week": week,
        "kind": "novelty",
        "scene_a": scene,
        "statement": (
            f"a coherent new cluster of {len(tight)} tracks is forming inside "
            f"{scene}, away from the scene's centre"),
        "evidence": {"cluster_size": len(tight), "outliers": len(outliers)},
        "horizon_weeks": 13,
        "resolution_rule": (
            "HIT if a cluster at this location persists (>= same size) in at least "
            "6 of the next 13 weeks; MISS otherwise."),
    }


# -- DIVERGENCE -------------------------------------------------------------

def detect_divergence(scene: str, week: str, sonic_momentum_z: float,
                      booking_momentum_z: float) -> dict | None:
    """Inputs are z-scores across scenes: sonic drift rate vs the booking/listen
    momentum the existing Signal pipeline computes. The joint reading is the
    conviction-authority gap transposed."""
    gap = sonic_momentum_z - booking_momentum_z
    if abs(gap) < DIVERGENCE_GAP:
        return None
    direction = ("sound leads: the sonic shift has no booking response yet"
                 if gap > 0 else
                 "bookings lead: lineups moved but the sound has not")
    return {
        "claim_id": _claim_id("diverge", week, scene),
        "emitted_week": week,
        "kind": "divergence",
        "scene_a": scene,
        "statement": f"{scene}: sonic and booking momentum disagree ({direction})",
        "evidence": {"sonic_z": sonic_momentum_z, "booking_z": booking_momentum_z,
                     "gap": gap},
        "horizon_weeks": 13,
        "resolution_rule": (
            "HIT if the lagging series moves toward the leading one by 13 weeks "
            "(gap halves); MISS if the gap persists or the leading series reverts."),
    }
