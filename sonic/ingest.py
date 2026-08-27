"""Ingestion. A Source yields TrackSighting rows for (scene, week).

FixtureSource reads a JSON file — the offline path and the test path.
BeatportSource is a skeleton: charts per scene mapped through
scene_map.json; the fetch itself needs network + your session approach,
so it raises with instructions rather than pretending.

Every sighting carries provenance (source string) into the store.
"""
from __future__ import annotations
import json
from dataclasses import dataclass


@dataclass
class TrackSighting:
    track_id: str      # stable id (ISRC ideally; source id otherwise)
    audio_ref: str     # what the analyser needs (preview url / cyanite id / stub ref)
    scene: str
    week: str
    source: str        # provenance, e.g. "beatport:garage-chart"
    chart_rank: int | None = None
    scene_weight: float = 1.0


class FixtureSource:
    """JSON: [{track_id, audio_ref, scene, week, source, chart_rank?, scene_weight?}]"""

    def __init__(self, path: str):
        self.path = path

    def sightings(self, week: str) -> list[TrackSighting]:
        with open(self.path) as f:
            rows = json.load(f)
        return [TrackSighting(**{**r}) for r in rows if r["week"] == week]


class BeatportSource:
    """Chart-per-genre ingestion. Beatport genre ids -> Signal scenes live in
    scene_map.json. Fetching requires network access and a decision about
    method (their public chart pages vs the partner API) that belongs to you,
    not to a default in a library."""

    def __init__(self, scene_map_path: str):
        with open(scene_map_path) as f:
            self.scene_map = json.load(f)

    def sightings(self, week: str) -> list[TrackSighting]:
        raise NotImplementedError(
            "BeatportSource needs network + a fetch method decision. "
            "Implement fetch_chart(genre_id) and map via self.scene_map; "
            "everything downstream already accepts its output.")


def ingest_week(store, analyser, sources, week: str) -> dict:
    """Pull sightings, analyse only unseen tracks, record assignments.
    Returns counts for the run log."""
    seen, analysed = 0, 0
    for src in sources:
        for s in src.sightings(week):
            seen += 1
            row = store.conn.execute(
                "SELECT features FROM tracks WHERE track_id=? AND analyser_id=?",
                (s.track_id, analyser.analyser_id)).fetchone()
            if row is None:
                fv = analyser.analyse(s.audio_ref)
                store.upsert_track(s.track_id, fv.to_json(), analyser.analyser_id,
                                   analyser.version, s.source, week)
                analysed += 1
            store.assign_scene(s.track_id, s.scene, week, s.source,
                               weight=s.scene_weight, chart_rank=s.chart_rank)
    return {"sightings": seen, "newly_analysed": analysed}
