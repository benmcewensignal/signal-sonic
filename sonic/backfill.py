"""Backfill: reconstruct what each scene sounded like, month by month,
from tracks published in that month — fetched via the same API, analysed
by the same analyser, stored in the same db, but structurally quarantined:

  - week labels are "YYYY-Mmm" (monthly), never "YYYY-Wnn"
  - scene_weeks rows are written under weighting='flat' ONLY (no ranks
    exist historically), so the live chart-weighted series the detectors
    read is never contaminated
  - NOTHING here may touch the claims table. fetch and report both assert
    the claims rowcount is unchanged before committing their work.
    Backfilled history informs thresholds and retrodiction; it is
    derived analysis, not calls.

Subcommands:
  probe   — try the publish-date filter spellings against one month and
            print which works (the blind-API lesson, built in)
  fetch   — pull + analyse one month range:  --from 2025-01 --to 2025-03
  report  — compute drift/convergence over the backfilled history and
            write backfill-report.md + .json (no claims, ever)
"""
from __future__ import annotations
import argparse
import calendar
import json
import time
import urllib.parse
from .store import Store
from .analyser import get_analyser, FeatureVector
from .aggregate import build_fingerprint, fingerprint_distance, decompose_drift
from .beatport import _get, get_token, analyse_sighting
from .ingest import TrackSighting

# candidate spellings for the publish-date range filter, tried in order;
# the working one is reported by `probe` and used by `fetch`
_MONTH_TOTALS = []

DATE_PARAM_VARIANTS = [
    ("publish_date", "{start}:{end}"),
    ("publish_date_start", None),          # paired with publish_date_end
    ("release_date", "{start}:{end}"),
]


def month_range(month: str) -> tuple[str, str]:
    y, m = int(month[:4]), int(month[5:7])
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"


def months_between(a: str, b: str) -> list[str]:
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    out = []
    while (ya, ma) <= (yb, mb):
        out.append(f"{ya:04d}-M{ma:02d}")
        ma += 1
        if ma == 13:
            ya, ma = ya + 1, 1
    return out


def _spread_sample(results: list[dict], k: int) -> list[dict]:
    """Deterministic even-stride sample across the month's fetched releases
    (sorted by publish date) instead of newest-first truncation — 'the
    month's sound' should not mean 'the last four days of the month'."""
    rs = sorted(results, key=lambda t: str(t.get("publish_date") or
                                           t.get("new_release_date") or ""))
    if len(rs) <= k:
        return rs
    step = len(rs) / k
    return [rs[int(i * step)] for i in range(k)]


def _try_fetch_month(token: str, genre_id: int, month: str, per_month: int):
    """Return (tracks, variant_desc) using the first date-filter spelling
    that yields results; raise with everything tried if none do."""
    start, end = month_range(f"{month[:4]}-{month[6:8]}")
    errors = []
    for name, fmt in DATE_PARAM_VARIANTS:
        params = {"genre_id": genre_id, "per_page": 100,
                  "order_by": "-publish_date"}
        if fmt:
            params[name] = fmt.format(start=start, end=end)
        else:
            params["publish_date_start"] = start
            params["publish_date_end"] = end
        try:
            d = _get("/catalog/tracks/", token, params)
            results = d.get("results", [])
            if results:
                # Beatport reports the true size of the month; we only download a sample,
                # so this count is the only honest measure of how much a scene released.
                _MONTH_TOTALS.append({"genre_id": genre_id, "month": month, "total": d.get("count")})
                return _spread_sample(results, per_month), f"{name}"
            errors.append(f"{name}: 200 but 0 results")
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError(f"no date-filter spelling worked for {month} genre {genre_id}: "
                       + " | ".join(errors))


def save_month_totals(db):
    """One row per genre-month: how many records the scene actually released."""
    if not _MONTH_TOTALS: return 0
    import sqlite3
    c = sqlite3.connect(db)
    c.execute("""create table if not exists scene_supply(
        genre_id text, month text, total integer, fetched_at text,
        primary key (genre_id, month))""")
    n = 0
    for r in _MONTH_TOTALS:
        if r.get("total") is None: continue
        c.execute("insert or replace into scene_supply values(?,?,?,datetime('now'))",
                  (str(r["genre_id"]), r["month"], int(r["total"])))
        n += 1
    c.commit(); return n


def cmd_probe(args):
    token = get_token()
    with open(args.scene_map) as f:
        scene_map = json.load(f)
    gid = next(int(k) for k in scene_map if not k.startswith("_"))
    tracks, variant = _try_fetch_month(token, gid, args.month, 5)
    print(f"WORKING VARIANT: {variant}")
    for t in tracks[:5]:
        print(t.get("id"), (t.get("name") or "")[:40],
              "publish:", t.get("publish_date") or t.get("new_release_date"),
              "preview:", bool(t.get("sample_url") or t.get("preview")))


def _claims_count(store: Store) -> int:
    return store.conn.execute("SELECT COUNT(*) c FROM claims").fetchone()["c"]


def cmd_fetch(args):
    store = Store(args.db)
    claims_before = _claims_count(store)
    token = get_token()
    analyser = get_analyser(args.analyser)
    with open(args.scene_map) as f:
        scene_map = json.load(f)
    genres = {int(k): v for k, v in scene_map.items() if not k.startswith("_")}
    only = {x.strip() for x in (getattr(args, "scenes", "") or "").split(",") if x.strip()}
    if only:
        genres = {k: v for k, v in genres.items() if v["scene"] in only}
        missing = only - {v["scene"] for v in genres.values()}
        if missing:
            raise SystemExit(f"--scenes: not in scene_map: {sorted(missing)}")
        print(f"backfill restricted to {sorted(only)}", flush=True)

    log = {"mode": "backfill", "months": {}, "analyser": analyser.analyser_id}
    for month in months_between(args.mfrom, args.mto):
        done_scenes = {r["scene"] for r in store.conn.execute(
            "SELECT DISTINCT scene FROM scene_weeks WHERE week=? AND weighting='flat'",
            (month,))}
        if all(v["scene"] in done_scenes for v in genres.values()):
            print(f"{month}: already complete, skipping", flush=True)
            log["months"][month] = "already complete"
            continue
        mlog = {}
        for gid, cfg in genres.items():
            try:
                tracks, _ = _try_fetch_month(token, gid, month, args.per_month)
            except RuntimeError as e:
                mlog[cfg["scene"]] = f"FETCH FAILED: {e}"
                continue
            n_new, n_skip, t0 = 0, 0, time.time()
            for i, tr in enumerate(tracks, 1):
                preview = (tr.get("sample_url") or
                           (tr.get("preview") or {}).get("mp3", {}).get("url") or "")
                if not preview:
                    n_skip += 1
                    continue
                tid = f"bp:{tr['id']}"
                s = TrackSighting(track_id=tid, audio_ref=preview,
                                  scene=cfg["scene"], week=month,
                                  source=f"beatport:backfill:genre{gid}",
                                  chart_rank=None)
                row = store.conn.execute(
                    "SELECT 1 FROM tracks WHERE track_id=? AND analyser_id=?",
                    (tid, analyser.analyser_id)).fetchone()
                if row is None:
                    try:
                        fv = analyse_sighting(analyser, s)
                    except Exception as e:
                        n_skip += 1
                        if n_skip <= 3:
                            print(f"    skip {tid}: {type(e).__name__}: {str(e)[:80]}",
                                  flush=True)
                        continue
                    store.upsert_track(tid, fv.to_json(), analyser.analyser_id,
                                       analyser.version, s.source, month)
                    n_new += 1
                store.assign_scene(tid, s.scene, month, s.source)
                if i % 10 == 0:
                    rate = i / (time.time() - t0)
                    print(f"    {month} genre{gid} {i}/{len(tracks)} "
                          f"({rate:.1f} tracks/s, {n_skip} skipped)", flush=True)
            print(f"  {month} {cfg['scene']}: {n_new} analysed, {n_skip} skipped, "
                  f"{time.time()-t0:.0f}s", flush=True)
            # monthly fingerprint: FLAT ONLY — quarantine from the live
            # chart-weighted series
            rows = store.scene_track_rows(cfg["scene"], month, analyser.analyser_id)
            if rows:
                parsed = [(FeatureVector.from_json(r["features"]), r["weight"])
                          for r in rows]
                fp = build_fingerprint(parsed)
                store.save_scene_week(cfg["scene"], month, analyser.analyser_id,
                                      "flat", len(parsed), json.dumps(fp))
            mlog[cfg["scene"]] = {"fetched": len(tracks), "analysed": n_new}
        log["months"][month] = mlog
        print(json.dumps({month: mlog}), flush=True)

    assert _claims_count(store) == claims_before, \
        "backfill touched the claims table — refusing to continue"
    n_supply = save_month_totals(args.db)
    print(json.dumps({"supply_rows_saved": n_supply}), flush=True)
    print(json.dumps({"backfill_complete": True,
                      "claims_untouched": True}))


# ---------------------------------------------------------------------------
# report: derived analysis over the monthly history. No claims. Ever.
# ---------------------------------------------------------------------------

def _monthly_history(store: Store, scene: str, analyser_id: str):
    rows = store.conn.execute(
        """SELECT week, fingerprint FROM scene_weeks
           WHERE scene=? AND analyser_id=? AND weighting='flat'
             AND week LIKE '____-M__' ORDER BY week""",
        (scene, analyser_id)).fetchall()
    return [(r["week"], json.loads(r["fingerprint"])) for r in rows]


def cmd_report(args):
    store = Store(args.db)
    claims_before = _claims_count(store)
    scenes = [r["scene"] for r in store.conn.execute(
        "SELECT DISTINCT scene FROM scene_weeks WHERE week LIKE '____-M__'")]
    analyser_id = args.analyser_id

    report = {"scenes": {}, "convergence": [], "note":
              "RETRODICTIVE ANALYSIS — derived from backfilled history. "
              "Not calls, not the track record, and never eligible for it."}

    hist = {s: _monthly_history(store, s, analyser_id) for s in scenes}

    for s, h in hist.items():
        if len(h) < 6:
            report["scenes"][s] = {"months": len(h), "note": "too short"}
            continue
        drifts = []
        for i in range(4, len(h)):
            base_fps = [fp for _, fp in h[max(0, i - 12):i - 1]]
            if not base_fps:
                continue
            base = base_fps[len(base_fps) // 2]
            d = fingerprint_distance(base, h[i][1])
            decomp = decompose_drift(base, h[i][1])
            top = max(decomp.items(), key=lambda kv: abs(kv[1]))
            drifts.append({"month": h[i][0], "distance": round(d, 4),
                           "top_mover": f"{top[0]} {top[1]:+.0%}"})
        peak = max(drifts, key=lambda x: x["distance"]) if drifts else None
        report["scenes"][s] = {"months": len(h), "series": drifts, "peak": peak}

    for i, a in enumerate(scenes):
        for b in scenes[i + 1:]:
            ha, hb = hist[a], hist[b]
            common = sorted(set(w for w, _ in ha) & set(w for w, _ in hb))
            if len(common) < 6:
                continue
            fa = dict(ha)
            fb = dict(hb)
            series = [(w, fingerprint_distance(fa[w], fb[w])) for w in common]
            early = sum(d for _, d in series[:3]) / 3
            late = sum(d for _, d in series[-3:]) / 3
            if early > 0:
                report["convergence"].append({
                    "pair": f"{a} × {b}",
                    "early": round(early, 4), "late": round(late, 4),
                    "change": round((late - early) / early, 3)})
    report["convergence"].sort(key=lambda x: x["change"])

    with open(args.out + ".json", "w") as f:
        json.dump(report, f, indent=1)
    _write_md(report, args.out + ".md")
    assert _claims_count(store) == claims_before, \
        "report touched the claims table — refusing"
    print(json.dumps({"report_written": args.out + ".md",
                      "scenes": len(scenes),
                      "claims_untouched": True}))


def _write_md(report: dict, path: str):
    lines = ["# Backfill report — retrodictive, not calls", "",
             f"> {report['note']}", "", "## Sonic drift by scene (monthly)", ""]
    for s, d in sorted(report["scenes"].items()):
        if "peak" not in d or not d.get("peak"):
            lines.append(f"- **{s}**: {d.get('months', 0)} months — {d.get('note', '')}")
            continue
        p = d["peak"]
        lines.append(f"- **{s}** ({d['months']} months): peak drift {p['distance']} "
                     f"in {p['month']} ({p['top_mover']})")
    lines += ["", "## Convergence / divergence between scenes", "",
              "Most converging first (change = fractional distance shift, "
              "negative means closer):", ""]
    for c in report["convergence"][:15]:
        lines.append(f"- {c['pair']}: {c['early']} → {c['late']}  ({c['change']:+.0%})")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe")
    p.add_argument("--month", default="2025-M06")
    p.add_argument("--scene-map", default="scene_map.json")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("fetch")
    p.add_argument("--from", dest="mfrom", required=True, help="e.g. 2025-01")
    p.add_argument("--to", dest="mto", required=True, help="e.g. 2025-03")
    p.add_argument("--per-month", type=int, default=40)
    p.add_argument("--db", default="sonic.db")
    p.add_argument("--analyser", default="local")
    p.add_argument("--scene-map", default="scene_map.json")
    p.add_argument("--scenes", default="", help="comma-separated scene ids to restrict the backfill to (e.g. new scenes only)")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("report")
    p.add_argument("--db", default="sonic.db")
    p.add_argument("--analyser-id", default="local")
    p.add_argument("--out", default="backfill-report")
    p.set_defaults(fn=cmd_report)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
