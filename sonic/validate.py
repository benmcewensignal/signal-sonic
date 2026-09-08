"""Validation after every job.

Every silent failure this week was a schema or state mismatch that nothing checked:
a table written but never created, a column read that never existed, a fetch that
reported success while downloading nothing, an exclusion flag cleared by the next scan.
This runs after each queue job and asserts, plainly, that the world is as we believe.

Two kinds of check:
  invariants  — always true, whatever ran (tables exist, no conflict markers, no
                pre-corpus mix inside usable_mixes, counts never went down)
  job effects — the job's own claim (a mixscan added plays or explains why not; a
                reanalyse increased v2 rows; a backfill increased rows in its months)

Failures are written to data/validation.json and printed. A failed invariant returns
rc 2 so the runner can park the job that caused it rather than carry on.

  python -m sonic.validate --db sonic.db --job mixscan --before /tmp/before.json
"""
import argparse, json, os, sqlite3, sys, time

REQUIRED_TABLES = ["tracks", "track_scenes", "track_meta", "mixes", "mix_plays", "mix_pop", "scene_supply"]
REQUIRED_COLUMNS = {"tracks": ["track_id", "analyser_id", "analyser_ver", "features"],
                    "mixes": ["mix_url", "source", "published", "error", "plays", "dj"],
                    "mix_plays": ["mix_url", "track_id", "offset_s"]}
DATA_FILES = ["data/artists-summary.json", "data/artist-lookup.json", "data/supply.json",
              "data/texture.json", "data/chainlink.json", "data/set-calibration.json"]


def snapshot(db):
    """Counts that a job may change. Taken before a job, compared after."""
    c = sqlite3.connect(db)
    def n(q):
        try: return c.execute(q).fetchone()[0]
        except Exception: return None
    return {"tracks": n("select count(*) from tracks where analyser_id='local'"),
            "v2": n("select count(*) from tracks where analyser_ver like '2%'"),
            "v3": n("select count(*) from embeddings_v3"),
            "named": n("select count(*) from track_meta where artists is not null"),
            "plays": n("select count(*) from mix_plays"),
            "usable_mixes": n("select count(*) from mixes where error is null and (published is null or substr(published,1,10)>='2024-08-01')"),
            "mix_pop": n("select count(*) from mix_pop"),
            "nts_lines": n("select count(*) from nts_tracklist"),
            "supply": n("select count(*) from scene_supply"),
            "aliases": n("select count(*) from artist_aliases")}


def check(db, job, before):
    c = sqlite3.connect(db)
    fails, notes = [], []
    tables = {r[0] for r in c.execute("select name from sqlite_master where type in ('table','view')")}
    for t in REQUIRED_TABLES:
        if t not in tables: fails.append(f"table missing: {t}")
    for t, cols in REQUIRED_COLUMNS.items():
        if t in tables:
            have = {r[1] for r in c.execute(f"pragma table_info({t})")}
            for col in cols:
                if col not in have: fails.append(f"column missing: {t}.{col}")
    # no pre-corpus mix can be usable
    # the view is the exclusion mechanism: it must exist and it must contain no pre-corpus mix
    if "usable_mixes" not in tables:
        # the view is how the pre-corpus exclusion is enforced; recreate it rather than fail,
        # then verify it, because a missing view is a fixable state and not a corrupt one
        try:
            c.execute("""create view if not exists usable_mixes as
                select * from mixes where error is null
                  and (published is null or substr(published,1,10) >= '2024-08-01')""")
            c.commit(); notes.append("usable_mixes view was missing and has been recreated")
            tables.add("usable_mixes")
        except Exception as e:
            fails.append(f"usable_mixes view missing and could not be created: {e}")
    if "usable_mixes" in tables:
        try:
            bad = c.execute("select count(*) from usable_mixes where published is not null and substr(published,1,10) < '2024-08-01'").fetchone()[0]
            if bad: fails.append(f"{bad} pre-corpus mixes inside usable_mixes")
        except Exception as e: fails.append(f"usable_mixes check errored: {e}")
    # data files parse and carry no conflict markers
    for f in DATA_FILES:
        if not os.path.exists(f): continue
        head = open(f, "rb").read(64)
        if head.startswith(b"<<<<<<<"): fails.append(f"conflict markers in {f}")
        else:
            try: json.load(open(f))
            except Exception as e: fails.append(f"{f} is not valid json: {str(e)[:40]}")
    # counts never go down
    after = snapshot(db)
    for k, v in (before or {}).items():
        if v is not None and after.get(k) is not None and after[k] < v:
            fails.append(f"{k} fell from {v} to {after[k]}")
    # the job's own claim
    def grew(k): return before and before.get(k) is not None and after.get(k) is not None and after[k] > before[k]
    claims = {"mixscan": ["plays", "usable_mixes"], "mixrescan": ["plays"], "reanalyse": ["v2"], "embed3": ["v3"],
              "metadata": ["named"], "backfill": ["tracks"], "nts": ["nts_lines"], "discogs": ["aliases"], "supply": ["supply"]}
    if job in claims:
        if not any(grew(k) for k in claims[job]):
            notes.append(f"{job} ran but none of {claims[job]} grew: check its log for why")
    report = {"ran": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "job": job, "before": before, "after": after,
              "failures": fails, "notes": notes, "ok": not fails}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--job", default="")
    ap.add_argument("--before", default=""); ap.add_argument("--snapshot", default="")
    a = ap.parse_args()
    if a.snapshot:
        json.dump(snapshot(a.db), open(a.snapshot, "w")); print("snapshot written"); return
    before = json.load(open(a.before)) if a.before and os.path.exists(a.before) else None
    rep = check(a.db, a.job, before)
    os.makedirs("data", exist_ok=True)
    json.dump(rep, open("data/validation.json", "w"), indent=1)
    for f in rep["failures"]: print(f"VALIDATION FAIL: {f}", flush=True)
    for n in rep["notes"]: print(f"validation note: {n}", flush=True)
    if rep["ok"]: print("validation: ok", flush=True)
    sys.exit(2 if rep["failures"] else 0)


if __name__ == "__main__":
    main()
