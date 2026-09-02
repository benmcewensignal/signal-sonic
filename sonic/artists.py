"""The artist join. Three layers are artist-shaped and blind to each other:
releases (track_meta, Beatport artist ids), sets (mix_plays -> track_meta),
bookings (RA depth snapshots, RA artist ids). This module resolves identity
across them by normalised name, builds per-artist aggregates, and answers the
questions that only the join can answer. Output: data/artists-latest.json
(committed; the public repo makes it fetchable by the site).

  python -m sonic.artists --db sonic.db --site https://www.earlysignal.live
"""
import argparse, json, re, sqlite3, time, unicodedata, urllib.request, collections, statistics

SCENES_RA = {  # sonic scene -> RA genre slugs it draws on (booking side)
    "tech-house": ["techhouse"], "techno-peak-time": ["techno"], "techno-raw-deep-hypnotic": ["techno"],
    "house": ["house"], "deep-house": ["house"], "drum-and-bass": ["drumandbass"], "amapiano": ["amapiano"],
    "afro-house": ["afrohouse"], "uk-garage-speed-garage": ["garage"], "breaks-breakbeat-uk-bass": ["breakbeat"],
    "140-deep-dubstep-grime": ["dubstep"], "melodic-house-techno": [], "uk-funky-gqom": [],
    "hard-techno": ["techno", "industrial"], "bass-house": ["house"], "trance-main-floor": ["trance"],
}

def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\((uk|us|de|it|fr|nl|live|dj set|official)\)", " ", s)
    s = re.sub(r"\b(feat\.?|ft\.?|featuring)\b.*$", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s

def fetch_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "signal-sonic/artists"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def load_bookings(site):
    """All RA depth snapshots: per artist -> slots, tags, cities, interest, dates seen."""
    idx = fetch_json(f"{site}/api/ra-depth?horizon=1")
    dates = idx.get("dates", [])
    A = {}
    genres = ["techhouse","techno","house","drumandbass","amapiano","afrohouse","garage","jungle","trance",
              "industrial","breakbeat","electronica","progressivehouse","minimal","disco","dubstep","psytrance"]
    for d in dates:
        seen = set()
        for g in genres:
            try:
                ev = fetch_json(f"{site}/api/ra-depth?genre={g}&date={d}").get("events", [])
            except Exception:
                continue
            for e in ev:
                if e.get("i") in seen: continue
                seen.add(e.get("i"))
                tags = e.get("g") or [g]
                for k, name in enumerate(e.get("ar") or []):
                    key = norm(name)
                    if not key: continue
                    a = A.setdefault(key, {"names": collections.Counter(), "ra_ids": set(), "slots": 0, "tags": collections.Counter(),
                                           "cities": collections.Counter(), "interest": 0, "first_seen": d, "last_seen": d, "picks": 0,
                                           "events": set()})
                    a["names"][name] += 1
                    ai = (e.get("ai") or [])
                    if k < len(ai) and ai[k]: a["ra_ids"].add(str(ai[k]))
                    if e.get("i") in a["events"]: continue
                    a["events"].add(e.get("i"))
                    a["slots"] += 1
                    for t in tags: a["tags"][t] += 1
                    if e.get("a"): a["cities"][e["a"] + (", " + e["c"] if e.get("c") else "")] += 1
                    a["interest"] += e.get("ic") or e.get("n") or 0
                    a["picks"] += 1 if e.get("p") else 0
                    a["first_seen"] = min(a["first_seen"], d); a["last_seen"] = max(a["last_seen"], d)
    return A, dates

def load_releases(db):
    """Per artist -> releases by scene and month, sound displacement context, set plays."""
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    have = c.execute("select count(*) from sqlite_master where name='track_meta'").fetchone()[0]
    R = {}
    if not have:
        return R, 0
    rows = c.execute("""select m.track_id, m.artists, m.artist_ids, m.label, m.released, ts.scene, ts.week
                        from track_meta m join track_scenes ts on ts.track_id=m.track_id
                        where m.artists is not null""").fetchall()
    plays = collections.Counter(r[0] for r in c.execute("select track_id from mix_plays"))
    n_meta = c.execute("select count(*) from track_meta where artists is not null").fetchone()[0]
    for r in rows:
        names = json.loads(r["artists"] or "[]"); ids = json.loads(r["artist_ids"] or "[]")
        for k, name in enumerate(names):
            key = norm(name)
            if not key: continue
            a = R.setdefault(key, {"names": collections.Counter(), "bp_ids": set(), "tracks": set(), "scenes": collections.Counter(),
                                   "months": collections.Counter(), "labels": collections.Counter(), "set_plays": 0, "first_release": None})
            a["names"][name] += 1
            if k < len(ids) and ids[k]: a["bp_ids"].add(str(ids[k]))
            if r["track_id"] in a["tracks"]: continue
            a["tracks"].add(r["track_id"]); a["scenes"][r["scene"]] += 1; a["months"][r["week"]] += 1
            if r["label"]: a["labels"][r["label"]] += 1
            a["set_plays"] += plays.get(r["track_id"], 0)
            rel = r["released"] or r["week"]
            a["first_release"] = rel if a["first_release"] is None else min(a["first_release"], rel)
    return R, n_meta

def build(db, site):
    B, dates = load_bookings(site)
    R, n_meta = load_releases(db)
    keys = set(B) | set(R)
    artists = []
    for k in keys:
        b = B.get(k); r = R.get(k)
        rec = {"key": k,
               "name": (b["names"].most_common(1)[0][0] if b else r["names"].most_common(1)[0][0]),
               "ra_ids": sorted(b["ra_ids"]) if b else [], "bp_ids": sorted(r["bp_ids"]) if r else [],
               "bookings": {"slots": b["slots"], "tags": dict(b["tags"].most_common(6)), "cities": dict(b["cities"].most_common(5)),
                            "n_cities": len(b["cities"]), "interest": b["interest"], "picks": b["picks"],
                            "first_seen": b["first_seen"], "last_seen": b["last_seen"]} if b else None,
               "releases": {"tracks": len(r["tracks"]), "scenes": dict(r["scenes"]), "labels": dict(r["labels"].most_common(3)),
                            "set_plays": r["set_plays"], "first_release": r["first_release"],
                            "recent": sum(v for m, v in r["months"].items() if m[:4] >= "2026")} if r else None}
        artists.append(rec)
    joined = [a for a in artists if a["bookings"] and a["releases"]]
    # ---- instruments ----
    def inst_under_booked():
        # releasing recently, set-played or in a moving scene, but thin on the circuit
        out = [a for a in artists if a["releases"] and a["releases"]["recent"] >= 2 and (not a["bookings"] or a["bookings"]["slots"] <= 2)]
        return sorted(out, key=lambda a: -(a["releases"]["recent"] + a["releases"]["set_plays"]))[:40]
    def inst_under_released():
        out = [a for a in artists if a["bookings"] and a["bookings"]["slots"] >= 6 and (not a["releases"] or a["releases"]["recent"] == 0)]
        return sorted(out, key=lambda a: -a["bookings"]["slots"])[:40]
    def inst_border_crossers():
        out = []
        for a in joined:
            tags = a["bookings"]["tags"]; scn = a["releases"]["scenes"]
            if len(tags) >= 3 and len(scn) == 1:
                out.append({"name": a["name"], "release_scene": next(iter(scn)), "booked_under": list(tags)[:5], "slots": a["bookings"]["slots"]})
        return sorted(out, key=lambda x: -x["slots"])[:40]
    def inst_lag():
        lags = []
        for a in joined:
            fr = a["releases"]["first_release"]; fs = a["bookings"]["first_seen"]
            if fr and fs and len(fr) >= 7:
                lags.append({"name": a["name"], "first_release": fr, "first_booked_seen": fs})
        return lags[:200]
    def inst_melodic_bookings():
        # RA has no melodic tag: reconstruct from release-side melodic artists' bookings
        mel = [a for a in joined if "melodic-house-techno" in a["releases"]["scenes"]]
        slots = sum(a["bookings"]["slots"] for a in mel)
        cities = collections.Counter()
        for a in mel:
            for c_, n in a["bookings"]["cities"].items(): cities[c_] += n
        return {"artists": len(mel), "slots": slots, "cities": dict(cities.most_common(10)), "snapshots": dates}
    def inst_new_names():
        # artists first seen in the latest snapshot only (needs >=2 snapshots to mean anything)
        if len(dates) < 2: return {"note": "needs two or more snapshots", "snapshots": dates}
        latest = dates[-1]
        new = [a for a in artists if a["bookings"] and a["bookings"]["first_seen"] == latest]
        by_tag = collections.Counter()
        for a in new:
            for t in a["bookings"]["tags"]: by_tag[t] += 1
        return {"latest": latest, "new_artists": len(new), "by_tag": dict(by_tag.most_common(12))}
    summary = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "snapshots": dates,
               "artists_total": len(artists), "booking_side": sum(1 for a in artists if a["bookings"]),
               "release_side": sum(1 for a in artists if a["releases"]), "joined": len(joined),
               "tracks_with_metadata": n_meta,
               "join_rate_release_side": round(len(joined) / max(1, sum(1 for a in artists if a["releases"])), 3)}
    return {"summary": summary,
            "instruments": {"under_booked": inst_under_booked(), "under_released": inst_under_released(),
                            "border_crossers": inst_border_crossers(), "release_to_booking": inst_lag(),
                            "melodic_bookings_reconstructed": inst_melodic_bookings(), "new_names": inst_new_names()},
            "artists": sorted([a for a in artists if (a["bookings"] and a["bookings"]["slots"] >= 3) or a["releases"]],
                              key=lambda a: -((a["bookings"] or {}).get("slots", 0) + 3 * (a["releases"] or {}).get("tracks", 0)))[:3000]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--site", default="https://www.earlysignal.live")
    ap.add_argument("--out", default="data/artists-latest.json")
    a = ap.parse_args()
    out = build(a.db, a.site)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(out["summary"], indent=1))

if __name__ == "__main__":
    main()
