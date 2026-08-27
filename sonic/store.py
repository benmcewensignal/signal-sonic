"""Fingerprint store. SQLite, matching the forecaster's storage decision.

Tables:
  tracks        append-only feature vectors, one row per (track, analyser)
  track_scenes  many-to-many with weight and provenance
  scene_weeks   derived weekly aggregate per (scene, week, analyser, weighting)
  claims        the calibration ledger — timestamped, immutable at emission

Provenance discipline: every row carries source + first_seen. Every
aggregate query filters analyser_id.
"""
from __future__ import annotations
import json
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    track_id      TEXT NOT NULL,
    analyser_id   TEXT NOT NULL,
    analyser_ver  TEXT NOT NULL,
    features      TEXT NOT NULL,          -- FeatureVector json
    source        TEXT NOT NULL,          -- provenance: where we saw it
    first_seen    TEXT NOT NULL,          -- ISO week e.g. 2026-W35
    created_at    REAL NOT NULL,
    PRIMARY KEY (track_id, analyser_id)
);

CREATE TABLE IF NOT EXISTS track_scenes (
    track_id      TEXT NOT NULL,
    scene         TEXT NOT NULL,
    weight        REAL NOT NULL DEFAULT 1.0,   -- split across scenes if multi
    chart_rank    INTEGER,                     -- NULL if unranked
    source        TEXT NOT NULL,
    week          TEXT NOT NULL,
    PRIMARY KEY (track_id, scene, week)
);

CREATE TABLE IF NOT EXISTS scene_weeks (
    scene         TEXT NOT NULL,
    week          TEXT NOT NULL,
    analyser_id   TEXT NOT NULL,
    weighting     TEXT NOT NULL,          -- 'chart' | 'flat'
    n_tracks      INTEGER NOT NULL,
    fingerprint   TEXT NOT NULL,          -- aggregate json (see aggregate.py)
    created_at    REAL NOT NULL,
    PRIMARY KEY (scene, week, analyser_id, weighting)
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id      TEXT PRIMARY KEY,
    emitted_week  TEXT NOT NULL,
    kind          TEXT NOT NULL,          -- drift|convergence|novelty|divergence
    scene_a       TEXT NOT NULL,
    scene_b       TEXT,                   -- convergence only
    statement     TEXT NOT NULL,
    evidence      TEXT NOT NULL,          -- json: features behind the call
    horizon_weeks INTEGER NOT NULL,
    resolution_rule TEXT NOT NULL,        -- stated at emission or the call doesn't ship
    emitted_at    REAL NOT NULL,
    resolved_at   REAL,
    outcome       TEXT,                   -- hit|miss|void
    outcome_note  TEXT
);
"""


class Store:
    def __init__(self, path: str = "sonic.db"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- tracks ------------------------------------------------------------
    def upsert_track(self, track_id: str, features_json: str, analyser_id: str,
                     analyser_ver: str, source: str, week: str) -> bool:
        """Insert if unseen for this analyser. Returns True if newly analysed."""
        cur = self.conn.execute(
            "SELECT 1 FROM tracks WHERE track_id=? AND analyser_id=?",
            (track_id, analyser_id))
        if cur.fetchone():
            return False
        with self.tx() as c:
            c.execute(
                "INSERT INTO tracks VALUES (?,?,?,?,?,?,?)",
                (track_id, analyser_id, analyser_ver, features_json,
                 source, week, time.time()))
        return True

    def assign_scene(self, track_id: str, scene: str, week: str, source: str,
                     weight: float = 1.0, chart_rank: int | None = None):
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO track_scenes VALUES (?,?,?,?,?,?)",
                (track_id, scene, weight, chart_rank, source, week))

    def scene_track_rows(self, scene: str, week: str, analyser_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT t.track_id, t.features, ts.weight, ts.chart_rank
               FROM track_scenes ts JOIN tracks t ON t.track_id = ts.track_id
               WHERE ts.scene=? AND ts.week=? AND t.analyser_id=?""",
            (scene, week, analyser_id)).fetchall()

    # -- scene weeks -------------------------------------------------------
    def save_scene_week(self, scene: str, week: str, analyser_id: str,
                        weighting: str, n: int, fingerprint_json: str):
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO scene_weeks VALUES (?,?,?,?,?,?,?)",
                (scene, week, analyser_id, weighting, n, fingerprint_json, time.time()))

    def scene_history(self, scene: str, analyser_id: str,
                      weighting: str = "chart") -> list[tuple[str, dict]]:
        rows = self.conn.execute(
            """SELECT week, fingerprint FROM scene_weeks
               WHERE scene=? AND analyser_id=? AND weighting=?
               ORDER BY week""",
            (scene, analyser_id, weighting)).fetchall()
        return [(r["week"], json.loads(r["fingerprint"])) for r in rows]

    # -- claims ------------------------------------------------------------
    def emit_claim(self, claim: dict):
        if not claim.get("resolution_rule"):
            raise ValueError("claim without a resolution rule does not ship")
        with self.tx() as c:
            c.execute(
                """INSERT INTO claims
                   (claim_id, emitted_week, kind, scene_a, scene_b, statement,
                    evidence, horizon_weeks, resolution_rule, emitted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (claim["claim_id"], claim["emitted_week"], claim["kind"],
                 claim["scene_a"], claim.get("scene_b"), claim["statement"],
                 json.dumps(claim["evidence"]), claim["horizon_weeks"],
                 claim["resolution_rule"], time.time()))

    def open_claims(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM claims WHERE resolved_at IS NULL ORDER BY emitted_at").fetchall()

    def resolve_claim(self, claim_id: str, outcome: str, note: str = ""):
        assert outcome in ("hit", "miss", "void")
        with self.tx() as c:
            cur = c.execute(
                "UPDATE claims SET resolved_at=?, outcome=?, outcome_note=? "
                "WHERE claim_id=? AND resolved_at IS NULL",
                (time.time(), outcome, note, claim_id))
            if cur.rowcount == 0:
                raise ValueError(f"claim {claim_id} missing or already resolved (immutable)")

    def track_record(self) -> dict:
        rows = self.conn.execute(
            "SELECT outcome, COUNT(*) n FROM claims WHERE resolved_at IS NOT NULL "
            "GROUP BY outcome").fetchall()
        rec = {r["outcome"]: r["n"] for r in rows}
        scored = rec.get("hit", 0) + rec.get("miss", 0)
        return {
            "resolved": scored,
            "hits": rec.get("hit", 0),
            "misses": rec.get("miss", 0),
            "voids": rec.get("void", 0),
            "hit_rate": (rec.get("hit", 0) / scored) if scored else None,
            "open": len(self.open_claims()),
        }
