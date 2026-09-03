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
    "house": ["house"], "deep-house": ["deephouse"], "drum-and-bass": ["drumandbass"], "amapiano": ["amapiano"],
    "afro-house": ["afrohouse"], "uk-garage-speed-garage": ["garage"], "breaks-breakbeat-uk-bass": ["breakbeat"],
    "140-deep-dubstep-grime": ["dubstep"], "melodic-house-techno": [], "uk-funky-gqom": [],
    "hard-techno": ["techno", "industrial", "hardcore"], "bass-house": ["bass", "house"], "trance-main-floor": ["trance"],
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
              "industrial","breakbeat","electronica","progressivehouse","minimal","disco","dubstep","psytrance",
              "deephouse","minimaltechno","acid","electro","afrotech","dubtechno","hardcore","bass"]
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
    th = None
    try: th = json.load(open("data/set-calibration.json")).get("recommended_wvotes")
    except Exception: pass
    cols = [r[1] for r in c.execute("pragma table_info(mix_plays)")]
    plays = collections.Counter(r[0] for r in c.execute("select track_id from mix_plays where wvotes >= ?", (th,))) if (th is not None and "wvotes" in cols) else collections.Counter()
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

LEAD_MAP = {}
LABEL_SCENES = {}
LABEL_ARTISTS = {}
LABEL_TOTAL = {}
SCALE = {}

def load_leadership(db, R, B):
    """Per scene: artists whose 2026 records sit furthest from the scene's 2024 home (leading edge),
    labels aggregated the same way, and artists played in sets but thin on the circuit."""
    import numpy as np
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    have = c.execute("select count(*) from sqlite_master where name='track_meta'").fetchone()[0]
    if not have: return {}, {}, []
    E = collections.defaultdict(dict)
    for r in c.execute("""select ts.scene, ts.week, ts.track_id, t.features from track_scenes ts
                          join tracks t on t.track_id=ts.track_id and t.analyser_id='local'"""):
        try: v = np.array(json.loads(r["features"])["embedding"])
        except Exception: continue
        E[r["scene"]][(r["track_id"], r["week"])] = v / (np.linalg.norm(v) or 1)
    meta = {r["track_id"]: (json.loads(r["artists"]), r["label"]) for r in c.execute("select track_id, artists, label from track_meta where artists is not null")}
    th = None
    try: th = json.load(open("data/set-calibration.json")).get("recommended_wvotes")
    except Exception: pass
    cols = [r[1] for r in c.execute("pragma table_info(mix_plays)")]
    if th is not None and "wvotes" in cols:
        plays = collections.Counter(r[0] for r in c.execute("select track_id from mix_plays where wvotes >= ?", (th,)))
    else:
        plays = collections.Counter()   # uncalibrated plays are not evidence
    edge, labels = {}, {}
    played = collections.defaultdict(lambda: {"plays": 0, "records": 0, "name": ""})
    for sc, tracks in E.items():
        home = [v for (t, w), v in tracks.items() if w <= "2025-M05" and "-M" in w]
        if len(home) < 50: continue
        H = np.mean(home, axis=0); H /= np.linalg.norm(H)
        dh = [1 - float(v @ H) for v in home]; mu, sd = statistics.mean(dh), (statistics.stdev(dh) or 1e-9)
        now = [v for (t, w), v in tracks.items() if w[:4] == "2026"]
        N = np.mean(now, axis=0) if len(now) >= 30 else H; N = N / (np.linalg.norm(N) or 1)
        spread = statistics.pstdev([1 - float(v @ N) for v in now]) if len(now) >= 30 else sd
        spread = spread or 1e-9
        mv = N - H; mvn = float(np.linalg.norm(mv)) or 1e-9
        art = collections.defaultdict(lambda: {"z": [], "plays": 0, "name": "", "vecs": []}); lab = collections.defaultdict(list)
        for (t, w), v in tracks.items():
            if w[:4] != "2026" or t not in meta: continue
            z = (1 - float(v @ H) - mu) / sd
            names, label = meta[t]
            for nm in names:
                k = norm(nm); a = art[k]; a["z"].append(z); a["plays"] += plays.get(t, 0); a["name"] = nm; a["vecs"].append(v)
                p = played[k]; p["plays"] += plays.get(t, 0); p["records"] += 1; p["name"] = nm
            if label:
                lab[label].append(z)
                LABEL_ARTISTS.setdefault(label, set()).update(norm(n) for n in names)
                LABEL_SCENES.setdefault(label, set()).add(sc)
                LABEL_TOTAL[label] = LABEL_TOTAL.get(label, 0) + 1
        for k, v in art.items():
            if len(v["z"]) >= 1:
                prev = LEAD_MAP.get(k)
                if not prev or len(v["z"]) > prev["records"]:
                    LEAD_MAP[k] = {"name": v["name"], "scene": sc, "z": round(statistics.mean(v["z"]), 1), "records": len(v["z"])}
        # Shrink toward the scene mean by evidence: an artist scored on two records
        # is mostly noise, so z_adj = z * n/(n+K). Ranking on z_adj stops the board
        # filling with two-record flukes (80 of 117 ranked rows before this).
        K = 3.0
        rows = []
        for k, v in art.items():
            n = len(v["z"])
            if n < 2: continue
            z = statistics.mean(v["z"])
            sc_ = SCALE.get(k) or {}
            if not sc_:
                sl = (B.get(k) or {}).get("slots", 0); ct = len((B.get(k) or {}).get("cities", {}))
                sc_ = {"tier": ("touring" if ct >= 3 else "regional" if ct == 2 else "local") if sl else "unbooked"}
            cen = np.mean(v["vecs"], axis=0); cen = cen / (np.linalg.norm(cen) or 1)
            dist = (1 - float(cen @ N)) / spread
            align = float(np.dot(cen - N, mv) / mvn) / spread
            pos = "ahead" if align >= 0.5 else ("behind" if align <= -0.5 else ("centre" if dist < 0.5 else "aside"))
            rows.append({"name": v["name"], "key": k, "z": round(z, 1), "z_adj": round(z * n / (n + K), 2),
                         "dist": round(dist, 1), "align": round(align, 1), "pos": pos,
                         "records": n, "set_plays": v["plays"],
                         "ra_slots": (B.get(k) or {}).get("slots", 0), "cities": len((B.get(k) or {}).get("cities", {})),
                         "tier": sc_.get("tier", "unbooked"), "per_event": sc_.get("per_event", 0)})
        rows.sort(key=lambda x: -x["z_adj"])
        known = [r for r in rows if r["ra_slots"] >= 3]
        regime = None
        if len(known) >= 4:
            al = [r["align"] for r in known]
            ahead = sum(1 for a in al if a >= 0.5) / len(al); behind = sum(1 for a in al if a <= -0.5) / len(al)
            centre = sum(1 for r in known if r["dist"] < 0.5) / len(known)
            regime = ("stars are the centre" if centre >= 0.5 else "stars lead" if ahead >= 0.6 else "stars behind" if behind >= 0.5 else "mixed")
        if rows:
            edge[sc] = {"leading": rows[:6], "conservative": rows[-3:][::-1],
                        "established": sorted(known, key=lambda r: -r["dist"])[:5], "established_n": len(known), "regime": regime,
                        "named_2026_records": sum(len(v["z"]) for v in art.values())}
        DISTRIBUTORS = {"distrokid", "united masters", "unitedmasters", "cd baby", "cdbaby", "tunecore",
                        "believe", "the orchard", "amuse", "symphonic", "label engine", "labelworx", "routenote"}
        lr = [{"label": l, "z": round(statistics.mean(z), 1), "records": len(z),
               "artists": len(LABEL_ARTISTS.get(l, ())),
               "focus": round(len(z) / max(1, LABEL_TOTAL.get(l, len(z))), 2)}
              for l, z in lab.items()
              if len(z) >= 3 and l.strip().lower() not in DISTRIBUTORS
              and len(LABEL_SCENES.get(l, set())) <= 3            # 4+ scenes = aggregator, not a label
              and len(z) / max(1, LABEL_TOTAL.get(l, len(z))) >= 0.5      # this scene is its main business
              and len(LABEL_ARTISTS.get(l, ())) >= 2]                      # 1 artist = a self-release channel
        lr.sort(key=lambda x: -x["z"])
        if lr: labels[sc] = lr[:6]
    pnb = [{"name": v["name"], "set_plays": v["plays"], "records_2026": v["records"], "ra_slots": (B.get(k) or {}).get("slots", 0)}
           for k, v in played.items() if v["plays"] >= 2 and (B.get(k) or {}).get("slots", 0) <= 1]
    pnb.sort(key=lambda x: -x["set_plays"])
    return edge, labels, pnb[:30]

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
    # --- scale: how big a name is this, from bookings, reach and editorial picks ---
    booked = [a for a in artists if a["bookings"] and a["bookings"]["slots"] > 0]
    def pct(vals, v):
        vals = sorted(vals); 
        return sum(1 for x in vals if x <= v) / max(1, len(vals))
    slots_all = [a["bookings"]["slots"] for a in booked]
    cities_all = [a["bookings"]["n_cities"] for a in booked]
    int_all = [a["bookings"]["interest"] for a in booked]
    for a in artists:
        b = a["bookings"]
        if not b or not b["slots"]:
            a["scale"] = {"tier": "unbooked", "slots": 0, "cities": 0, "interest": 0, "score": 0.0,
                          "note": "no bookings in the next ninety days"}
            continue
        score = (pct(slots_all, b["slots"]) + pct(cities_all, b["n_cities"]) + pct(int_all, b["interest"])) / 3
        tier = ("headliner" if (b["n_cities"] >= 6 and score >= 0.9) else
                "touring" if (b["n_cities"] >= 3 and score >= 0.65) else
                "regional" if b["n_cities"] >= 2 else "local")
        a["scale"] = {"tier": tier, "slots": b["slots"], "cities": b["n_cities"], "interest": b["interest"],
                      "per_event": round(b["interest"] / max(1, b["slots"])), "picks": b["picks"],
                      "score": round(score, 2),
                      "note": {"headliner": "booked widely across many cities",
                               "touring": "booked in several cities",
                               "regional": "booked in two or three cities",
                               "local": "booked in one city"}[tier]}
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
            tot = sum(scn.values()); dom, dn = max(scn.items(), key=lambda kv: kv[1])
            if len(tags) >= 3 and dn / max(1, tot) >= 0.6 and dom.split("-")[0] not in [t[:len(dom.split("-")[0])] for t in tags][:1]:
                out.append({"name": a["name"], "release_scene": dom, "release_tracks": tot, "booked_under": list(tags)[:5], "slots": a["bookings"]["slots"]})
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
    SCALE.update({a["key"]: a.get("scale", {}) for a in artists})
    edge, labels_dir, played_not_booked = load_leadership(db, R, B)
    summary = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "snapshots": dates,
               "artists_total": len(artists), "booking_side": sum(1 for a in artists if a["bookings"]),
               "release_side": sum(1 for a in artists if a["releases"]), "joined": len(joined),
               "tracks_with_metadata": n_meta,
               "join_rate_release_side": round(len(joined) / max(1, sum(1 for a in artists if a["releases"])), 3)}
    return {"summary": summary, "artist_leadership": LEAD_MAP,
            "instruments": {"leading_edge": edge, "labels_direction": labels_dir, "played_not_booked": played_not_booked,
                            "under_booked": inst_under_booked(), "under_released": inst_under_released(),
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
    slim = {"summary": out["summary"], "instruments": {k: (v[:25] if isinstance(v, list) else v) for k, v in out["instruments"].items()}}
    json.dump(slim, open(a.out.replace("latest", "summary"), "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(out["summary"], indent=1))

if __name__ == "__main__":
    main()
