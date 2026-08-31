"""Export the sonic layer's weekly per-scene read to the demand pipeline.

Contract (api/sonic-ingest.js on earlysignal.live):
  POST  Authorization: Bearer $SONIC_INGEST_SECRET
  { week, analyser, mapping_version, scenes: { <site name>: {
      sonic (0-100), drift, displacement, dispersion, dispersion_delta,
      novelty, dubplate, inbound, n } } }

The srchist scalar is `sonic`: drift velocity scaled to 0-100 with a fixed
constant so weeks are comparable. Correlation-based consumers (lead-lag) are
scale-invariant, so the constant only needs to be stable, not "right".

Both Beatport house charts map to the site's single House scene; records are
merged weighted by track count. Missing months yield no payload rather than
a fabricated one.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, urllib.request

import numpy as np

from .store import Store

MAPPING_VERSION = 1
SONIC_SCALE = 5000.0          # drift 0.02 (a large month) -> 100
SITE = {                       # beatport scene -> earlysignal scene name
    "140-deep-dubstep-grime": "140 / Deep Dubstep / Grime",
    "afro-house": "Afro House",
    "amapiano": "Amapiano",
    "breaks-breakbeat-uk-bass": "Breaks / Breakbeat / UK Bass",
    "deep-house": "House — Classic/Deep",
    "drum-and-bass": "Drum & Bass",
    "house": "House — Classic/Deep",
    "melodic-house-techno": "Melodic House & Techno",
    "tech-house": "Tech House",
    "techno-peak-time": "Techno — Peak Time",
    "techno-raw-deep-hypnotic": "Techno — Raw/Deep",
    "uk-funky-gqom": "UK Funky / Gqom",
    "uk-garage-speed-garage": "UK Garage / Speed Garage",
}


def _months(store: Store) -> list[str]:
    rows = store.conn.execute(
        "SELECT DISTINCT week FROM scene_weeks WHERE week LIKE '____-M__' ORDER BY week"
    ).fetchall()
    return [r[0] for r in rows]


def _embeddings(store: Store, scene: str, month: str) -> np.ndarray:
    rows = store.conn.execute(
        """SELECT t.features FROM track_scenes ts
           JOIN tracks t ON t.track_id=ts.track_id AND t.analyser_id='local'
           WHERE ts.scene=? AND ts.week=?""", (scene, month)).fetchall()
    embs = [json.loads(r[0])["embedding"] for r in rows]
    return np.array(embs) if embs else np.zeros((0, 1))


def _cent(X: np.ndarray):
    if len(X) == 0:
        return None
    v = X.mean(0)
    n = np.linalg.norm(v)
    return v / n if n else v


def compute(store: Store) -> dict | None:
    """Per-scene sonic record from the two most recent complete months."""
    months = _months(store)
    if len(months) < 3:
        return None
    m_now, m_prev = months[-1], months[-2]
    home_months = months[-8:-2] or months[:-2]
    scenes: dict[str, dict] = {}
    for bp in SITE:
        X_now = _embeddings(store, bp, m_now)
        X_prev = _embeddings(store, bp, m_prev)
        if len(X_now) < 10 or len(X_prev) < 10:
            continue
        c_now, c_prev = _cent(X_now), _cent(X_prev)
        drift = float(1 - np.dot(c_now, c_prev))
        homes = [_cent(_embeddings(store, bp, m)) for m in home_months]
        homes = [h for h in homes if h is not None]
        home = _cent(np.array(homes)) if homes else c_prev
        displacement = float(1 - np.dot(c_now, home))
        disp_now = float(np.mean(1 - X_now @ c_now))
        prior = []
        for m in months[-5:-1]:
            Xm = _embeddings(store, bp, m)
            cm = _cent(Xm)
            if cm is not None and len(Xm) >= 10:
                prior.append(float(np.mean(1 - Xm @ cm)))
        disp_delta = disp_now - (sum(prior) / len(prior)) if prior else 0.0
        # novelty: new entrants this month vs last month's centroid
        new_ids = {r[0] for r in store.conn.execute(
            """SELECT ts.track_id FROM track_scenes ts JOIN tracks t
               ON t.track_id=ts.track_id
               WHERE ts.scene=? AND ts.week=? AND t.first_seen=?""",
            (bp, m_now, m_now)).fetchall()}
        nov = None
        if new_ids:
            rows = store.conn.execute(
                f"""SELECT features FROM tracks WHERE analyser_id='local'
                    AND track_id IN ({','.join('?'*len(new_ids))})""",
                tuple(new_ids)).fetchall()
            E = np.array([json.loads(r[0])["embedding"] for r in rows])
            nov = float(np.mean(1 - E @ c_prev)) if len(E) else None
        # set-harvest signals, last 60 days of successful scans
        cutoff = (dt.date.today() - dt.timedelta(days=60)).isoformat()
        try:
            r = store.conn.execute(
                """SELECT AVG(unmatched_share), COUNT(*) FROM mixes
                   WHERE scene=? AND error IS NULL AND scanned_at>=?""",
                (bp, cutoff)).fetchone()
            dub = round(float(r[0]), 3) if r and r[0] is not None else None
            inbound = store.conn.execute(
                """SELECT COUNT(*) FROM mix_plays mp JOIN mixes m ON m.mix_url=mp.mix_url
                   WHERE m.scene=? AND m.error IS NULL AND m.scanned_at>=?""",
                (bp, cutoff)).fetchone()[0]
        except Exception:            # mixscan has never run against this db
            dub, inbound = None, 0
        rec = {
            "sonic": min(100, round(drift * SONIC_SCALE)),
            "drift": round(drift, 5),
            "displacement": round(displacement, 5),
            "dispersion": round(disp_now, 5),
            "dispersion_delta": round(disp_delta, 5),
            "novelty": round(nov, 5) if nov is not None else None,
            "dubplate": dub,
            "inbound": int(inbound),
            "n": int(len(X_now)),
        }
        site = SITE[bp]
        if site in scenes:                    # merge (house + deep-house)
            a, b = scenes[site], rec
            wa, wb = a["n"], b["n"]
            merged = {}
            for k in rec:
                va, vb = a[k], b[k]
                if k == "n":
                    merged[k] = wa + wb
                elif k == "inbound":
                    merged[k] = (va or 0) + (vb or 0)
                elif va is None or vb is None:
                    merged[k] = va if vb is None else vb
                else:
                    merged[k] = round((va * wa + vb * wb) / (wa + wb),
                                      0 if k == "sonic" else 5)
            merged["sonic"] = int(min(100, merged["sonic"]))
            scenes[site] = merged
        else:
            scenes[site] = rec
    if not scenes:
        return None
    # Integrity guard: the payload is stamped with today's date, but the
    # measurement comes from the latest backfilled month. Posting year-old
    # sound labelled as this week would poison the joined series at birth.
    y, mo = int(m_now[:4]), int(m_now[6:8])
    age_days = (dt.date.today() - dt.date(y, mo, 28)).days
    if age_days > 75:
        print(f"export: latest sonic month is {m_now} ({age_days}d old); "
              f"refusing to post stale sound as current. Run backfill forward first.")
        return None
    from .analyser import get_analyser
    ana = get_analyser("local")
    return {"week": dt.date.today().isoformat(),
            "analyser": f"local@{ana.version}",
            "mapping_version": MAPPING_VERSION,
            "scenes": scenes}


def post(payload: dict, url: str, secret: str) -> tuple[int, str]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {secret}"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode()[:300]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="sonic.db")
    p.add_argument("--post", default=None, help="ingest URL; prints payload if omitted")
    args = p.parse_args()
    store = Store(args.db)
    payload = compute(store)
    if payload is None:
        print("export: not enough history for a payload (need 3+ months, 10+ tracks/scene)")
        return
    print(f"export: {len(payload['scenes'])} scenes, week {payload['week']}, "
          f"analyser {payload['analyser']}")
    for name, rec in sorted(payload["scenes"].items()):
        print(f"  {name[:34]:34} sonic {rec['sonic']:3d}  n={rec['n']}")
    if args.post:
        secret = os.environ.get("SONIC_INGEST_SECRET", "")
        if not secret:
            print("export: SONIC_INGEST_SECRET unset; not posting")
            return
        status, body = post(payload, args.post, secret)
        print(f"POST {status}: {body}")


if __name__ == "__main__":
    main()
