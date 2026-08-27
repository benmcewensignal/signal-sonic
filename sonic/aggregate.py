"""Weekly scene fingerprint.

The aggregate keeps distribution shape, not just means: each scalar
feature stores (mean, p10, p50, p90), tag features store frequency
maps, and the embedding stores centroid + dispersion.

Chart weighting: rank r contributes weight 1/log2(r+2) (rank 1 ≈ 0.63,
rank 40 ≈ 0.19, unranked = the rank-40 floor). The flat version is
stored alongside — weighted vs flat divergence is itself a reading.
"""
from __future__ import annotations
import json
import math
from .analyser import FeatureVector, EMBED_DIM

SCALARS = ["tempo", "drum_density", "drum_swing", "bass_weight", "vocal_presence"]
TAG_FIELDS = ["drum_palette", "bass_character", "vocal_treatment", "mood"]
UNRANKED_FLOOR_RANK = 40


def chart_weight(rank: int | None) -> float:
    r = rank if rank is not None else UNRANKED_FLOOR_RANK
    return 1.0 / math.log2(r + 2)


def _weighted_quantiles(values: list[float], weights: list[float],
                        qs=(0.1, 0.5, 0.9)) -> list[float]:
    pairs = sorted(zip(values, weights))
    total = sum(weights)
    out, acc, i = [], 0.0, 0
    for q in qs:
        target = q * total
        while i < len(pairs) and acc + pairs[i][1] < target:
            acc += pairs[i][1]
            i += 1
        out.append(pairs[min(i, len(pairs) - 1)][0])
    return out


def build_fingerprint(rows: list[tuple[FeatureVector, float]]) -> dict:
    """rows: [(features, weight)] for one scene-week. Returns fingerprint dict."""
    if not rows:
        return {}
    total_w = sum(w for _, w in rows) or 1.0

    fp: dict = {"n": len(rows)}

    for name in SCALARS:
        vals = [getattr(f, name) for f, _ in rows]
        ws = [w for _, w in rows]
        mean = sum(v * w for v, w in zip(vals, ws)) / total_w
        p10, p50, p90 = _weighted_quantiles(vals, ws)
        fp[name] = {"mean": mean, "p10": p10, "p50": p50, "p90": p90}

    for name in TAG_FIELDS:
        freq: dict[str, float] = {}
        for f, w in rows:
            for tag in getattr(f, name):
                freq[tag] = freq.get(tag, 0.0) + w
        fp[name] = {k: v / total_w for k, v in sorted(freq.items())}

    # energy curve: pointwise weighted mean
    curve = [0.0] * 8
    for f, w in rows:
        for i, e in enumerate(f.energy_curve):
            curve[i] += e * w
    fp["energy_curve"] = [c / total_w for c in curve]

    # embedding centroid + dispersion
    cen = [0.0] * EMBED_DIM
    for f, w in rows:
        for i, x in enumerate(f.embedding):
            cen[i] += x * w
    cen = [c / total_w for c in cen]
    norm = math.sqrt(sum(c * c for c in cen)) or 1.0
    cen_n = [c / norm for c in cen]
    disp = sum(
        w * _cos_dist(f.embedding, cen_n) for f, w in rows) / total_w
    fp["embedding_centroid"] = cen_n
    fp["embedding_dispersion"] = disp
    return fp


def _cos_dist(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return 1.0 - dot  # inputs are L2-normalised


def fingerprint_distance(fa: dict, fb: dict) -> float:
    """Distance between two fingerprints in [0, ~2]: cosine distance of
    centroids, plus a small scalar-feature term so tag-blind embeddings
    don't dominate entirely."""
    if not fa or not fb:
        return float("nan")
    d_emb = _cos_dist(fa["embedding_centroid"], fb["embedding_centroid"])
    d_scal = 0.0
    for name in SCALARS:
        ra = fa[name]["mean"]
        rb = fb[name]["mean"]
        scale = 60.0 if name == "tempo" else 1.0
        d_scal += abs(ra - rb) / scale
    return d_emb + 0.1 * (d_scal / len(SCALARS))


def decompose_drift(fa: dict, fb: dict) -> dict[str, float]:
    """Signed per-feature change from fingerprint fa (baseline) to fb (now),
    as fractions of scale — the material for a plain-language call."""
    out = {}
    for name in SCALARS:
        scale = 60.0 if name == "tempo" else 1.0
        out[name] = (fb[name]["mean"] - fa[name]["mean"]) / scale
    return out


def aggregate_scene_week(store, scene: str, week: str, analyser_id: str):
    """Compute and persist both weightings for one scene-week."""
    rows = store.scene_track_rows(scene, week, analyser_id)
    if not rows:
        return 0
    parsed = [(FeatureVector.from_json(r["features"]), r["weight"], r["chart_rank"])
              for r in rows]
    for weighting in ("chart", "flat"):
        pairs = [
            (f, (w * chart_weight(rank)) if weighting == "chart" else w)
            for f, w, rank in parsed
        ]
        fp = build_fingerprint(pairs)
        store.save_scene_week(scene, week, analyser_id, weighting,
                              len(pairs), json.dumps(fp))
    return len(parsed)
