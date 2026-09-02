"""The venue layer. Rooms are where the decisions are made: which sound gets a
night. From the RA depth snapshots (events with venue, city, lineup, interest,
tags) and the artist leadership map (who is making each scene's new sound),
four instruments:

  direction   which rooms book the leading edge of a moving scene, which book
              its conservative end (per scene, named rooms)
  ladder      within each city, rooms tiered by audience intent per event;
              each scene's distribution over tiers, and movement across snapshots
  omnivores   high-breadth rooms where scenes mix; which scenes are entering them
  residencies recurring artist-room pairs: infrastructure vs touring, per scene

  python -m sonic.venues --site https://www.earlysignal.live --artists data/artists-latest.json
"""
import argparse, json, collections, statistics, time, urllib.request
from .artists import norm

GENRES = ["techhouse","techno","house","drumandbass","amapiano","afrohouse","garage","jungle","trance",
          "industrial","breakbeat","electronica","progressivehouse","minimal","disco","dubstep","psytrance",
          "deephouse","minimaltechno","acid","electro","afrotech","dubtechno","hardcore","bass"]
PLACEHOLDER = ("tba", "to be announced", "secret", "location tba", "venue tba", "announced")

def fetch_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "signal-sonic/venues"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def is_placeholder(name):
    n = (name or "").strip().lower()
    return (not n) or any(n == p or n.startswith(p + " ") or n.startswith(p + "-") or n.startswith(p + ":") for p in PLACEHOLDER) or n.startswith("tba")

def load_events(site):
    """{date: [event]} with events deduplicated across genre captures."""
    idx = fetch_json(f"{site}/api/ra-depth?horizon=1")
    dates = idx.get("dates", [])
    out = {}
    for d in dates:
        seen, ev = set(), []
        for g in GENRES:
            try:
                for e in fetch_json(f"{site}/api/ra-depth?genre={g}&date={d}").get("events", []):
                    if e.get("i") in seen: continue
                    seen.add(e.get("i")); ev.append(e)
            except Exception:
                continue
        out[d] = ev
    return out, dates

def vkey(e):
    return f"v{e['vid']}" if e.get("vid") else f"{e.get('v')}|{e.get('a')}|{e.get('c')}"

def venue_table(ev):
    V = {}
    for e in ev:
        if is_placeholder(e.get("v")): continue
        k = vkey(e)
        v = V.setdefault(k, {"venue": e.get("v"), "city": (e.get("a") or "?") + (", " + e["c"] if e.get("c") else ""), "events": 0, "interest": 0,
                             "genres": collections.Counter(), "artists": collections.Counter(), "names": {}, "fest": 0})
        v["events"] += 1; v["interest"] += (e.get("ic") or e.get("n") or 0)
        for g in (e.get("g") or []): v["genres"][g] += 1
        for a in (e.get("ar") or []): v["artists"][norm(a)] += 1; v["names"].setdefault(norm(a), a)
        v["fest"] += 1 if e.get("f") else 0
    for v in V.values():
        tot = sum(v["genres"].values()) or 1
        v["breadth"] = round(1 - max(v["genres"].values(), default=0) / tot, 2)
        v["per_event"] = round(v["interest"] / v["events"], 1)
    return V

def direction(V, lead):
    """Rooms ranked by the leadership of the artists they book, per scene."""
    per_scene = collections.defaultdict(list)
    for k, v in V.items():
        by_scene = collections.defaultdict(list)
        for a, n in v["artists"].items():
            L = lead.get(a)
            if L: by_scene[L["scene"]].extend([L["z"]] * n)
        for sc, zs in by_scene.items():
            if len(zs) >= 3:
                per_scene[sc].append({"venue": v["venue"], "city": v["city"], "scored": len(zs), "mean_z": round(statistics.mean(zs), 2),
                                      "leading_share": round(sum(1 for z in zs if z >= 1) / len(zs), 2),
                                      "conservative_share": round(sum(1 for z in zs if z <= -0.5) / len(zs), 2)})
    out = {}
    for sc, rows in per_scene.items():
        rows.sort(key=lambda r: -r["mean_z"])
        out[sc] = {"forward": rows[:6], "conservative": rows[-4:][::-1], "rooms_scored": len(rows)}
    return out

def ladder(V, ev):
    """Within-city tiers by interest per event (rooms with >=3 events, cities with >=8 such rooms)."""
    by_city = collections.defaultdict(list)
    for k, v in V.items():
        if v["events"] >= 3: by_city[v["city"]].append((k, v["per_event"]))
    tier = {}
    for city, rooms in by_city.items():
        if len(rooms) < 8: continue
        rooms.sort(key=lambda r: r[1])
        n = len(rooms)
        for i, (k, _) in enumerate(rooms):
            tier[k] = 1 + int(4 * i / n)          # 1 = quietest quartile, 4 = biggest pull
    genre_tiers = collections.defaultdict(list)
    for e in ev:
        if is_placeholder(e.get("v")): continue
        t = tier.get(vkey(e))
        if t is None: continue
        for g in (e.get("g") or []): genre_tiers[g].append(t)
    out = {}
    for g, ts in genre_tiers.items():
        if len(ts) < 25: continue
        out[g] = {"events_tiered": len(ts), "mean_tier": round(statistics.mean(ts), 2),
                  "top_tier_share": round(sum(1 for t in ts if t == 4) / len(ts), 2),
                  "bottom_tier_share": round(sum(1 for t in ts if t == 1) / len(ts), 2)}
    return out, tier

def omnivores(V, ev, prev_ev=None):
    rooms = [(k, v) for k, v in V.items() if v["events"] >= 10 and v["breadth"] >= 0.65]
    rooms.sort(key=lambda kv: -kv[1]["breadth"])
    omni_keys = {k for k, _ in rooms}
    share = collections.defaultdict(lambda: [0, 0])
    for e in ev:
        if is_placeholder(e.get("v")): continue
        inside = vkey(e) in omni_keys
        for g in (e.get("g") or []): share[g][1] += 1; share[g][0] += 1 if inside else 0
    genre_share = {g: round(a / b, 2) for g, (a, b) in share.items() if b >= 40}
    entrants = None
    if prev_ev is not None:
        prev_genres_by_room = collections.defaultdict(set)
        for e in prev_ev:
            if vkey(e) in omni_keys:
                for g in (e.get("g") or []): prev_genres_by_room[vkey(e)].add(g)
        entrants = []
        for k, v in rooms:
            new = [g for g in v["genres"] if g not in prev_genres_by_room.get(k, set())]
            if new: entrants.append({"venue": v["venue"], "city": v["city"], "new_genres": new[:6]})
    table = [{"venue": v["venue"], "city": v["city"], "events": v["events"], "breadth": v["breadth"], "genres": len(v["genres"]),
              "mix": dict(v["genres"].most_common(5))} for k, v in rooms[:40]]
    return {"rooms": table, "genre_share_in_omnivore_rooms": genre_share, "entrants_since_previous": entrants}

def residencies(V, ev):
    res_pairs = {}
    for k, v in V.items():
        for a, n in v["artists"].items():
            if n >= 3: res_pairs[(k, a)] = n
    resident_rooms = collections.defaultdict(set)
    for (k, a), n in res_pairs.items(): resident_rooms[k].add(a)
    share = collections.defaultdict(lambda: [0, 0])
    for e in ev:
        if is_placeholder(e.get("v")): continue
        k = vkey(e); res = resident_rooms.get(k, set())
        has_res = any(norm(a) in res for a in (e.get("ar") or []))
        for g in (e.get("g") or []): share[g][1] += 1; share[g][0] += 1 if has_res else 0
    per_genre = {g: round(a / b, 2) for g, (a, b) in share.items() if b >= 40}
    top = sorted(res_pairs.items(), key=lambda kv: -kv[1])[:40]
    table = [{"venue": V[k]["venue"], "city": V[k]["city"], "artist": V[k]["names"].get(a, a), "nights": n} for (k, a), n in top]
    return {"pairs": len(res_pairs), "residency_share_by_genre": per_genre, "top": table}

def build(site, artists_path):
    events, dates = load_events(site)
    lead = {}
    try: lead = json.load(open(artists_path)).get("artist_leadership", {})
    except Exception: pass
    latest = dates[-1]; prev = dates[-2] if len(dates) > 1 else None
    V = venue_table(events[latest])
    ladder_now, tier = ladder(V, events[latest])
    movement = None
    if prev:
        Vp = venue_table(events[prev]); ladder_prev, _ = ladder(Vp, events[prev])
        movement = {g: round(ladder_now[g]["mean_tier"] - ladder_prev[g]["mean_tier"], 2) for g in ladder_now if g in ladder_prev}
    return {"summary": {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "snapshots": dates, "latest": latest,
                        "venues": len(V), "venues_with_3plus": sum(1 for v in V.values() if v["events"] >= 3),
                        "leadership_artists": len(lead), "tiered_rooms": len(tier)},
            "direction": direction(V, lead),
            "ladder": {"by_genre": ladder_now, "movement_since_previous": movement, "tiers": "1 quietest quartile of the city's rooms .. 4 biggest pull; interest is comparable only within a city"},
            "omnivores": omnivores(V, events[latest], events.get(prev) if prev else None),
            "residencies": residencies(V, events[latest])}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="https://www.earlysignal.live")
    ap.add_argument("--artists", default="data/artists-latest.json")
    ap.add_argument("--out", default="data/venues-latest.json")
    a = ap.parse_args()
    out = build(a.site, a.artists)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    slim = {"summary": out["summary"],
            "direction": {sc: {"forward": d["forward"][:4], "conservative": d["conservative"][:3], "rooms_scored": d["rooms_scored"]} for sc, d in out["direction"].items()},
            "ladder": {"by_genre": out["ladder"]["by_genre"], "movement_since_previous": out["ladder"]["movement_since_previous"]},
            "omnivores": {"rooms": out["omnivores"]["rooms"][:15], "genre_share_in_omnivore_rooms": out["omnivores"]["genre_share_in_omnivore_rooms"], "entrants_since_previous": (out["omnivores"]["entrants_since_previous"] or [])[:12] if out["omnivores"]["entrants_since_previous"] is not None else None},
            "residencies": {"pairs": out["residencies"]["pairs"], "residency_share_by_genre": out["residencies"]["residency_share_by_genre"], "top": out["residencies"]["top"][:15]}}
    json.dump(slim, open(a.out.replace("latest", "summary"), "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(out["summary"], indent=1))

if __name__ == "__main__":
    main()
