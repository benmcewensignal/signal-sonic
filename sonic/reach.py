"""Mix popularity, made into instruments.

A DJ set is a broadcast: a record in a mix with 400,000 plays reached a different order
of magnitude of listeners than one in a mix with 200. Every scan now stores the mix's
plays, followers and DJ. This module turns that into three things:

  set reach   per producer: sum of log10(plays+1) over the mixes that played them,
              ranked within source platform so Mixcloud and YouTube are not compared raw
  endorsement per producer: which DJs (by reach) reached for their records
  appetite    per scene: age-adjusted plays per mix, the demand for a sound as performed

  python -m sonic.reach --db sonic.db --out data/reach.json
"""
import argparse, collections, json, math, sqlite3, statistics as st, datetime as dt
from .artists import norm

CORPUS_START = dt.date(2024, 8, 1)


def _date(x):
    try:
        if isinstance(x, (int, float)) or str(x).isdigit(): return dt.datetime.utcfromtimestamp(float(x)).date()
        return dt.date.fromisoformat(str(x)[:10])
    except Exception: return None


def build(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    mixes = {}
    for r in c.execute("select * from usable_mixes"):
        p = _date(r["published"])
        if p and p < CORPUS_START: continue                     # cannot contain our records
        mixes[r["mix_url"]] = dict(r)
    meta = {r["track_id"]: json.loads(r["artists"]) for r in c.execute(
        "select track_id, artists from track_meta where artists is not null")}
    # within-source percentile of plays, so platforms are not compared raw
    by_src = collections.defaultdict(list)
    for m in mixes.values():
        if m.get("plays"): by_src[m["source"]].append(m["plays"])
    def pct(src, v):
        vs = by_src.get(src) or []
        if not vs or v is None: return None
        return round(sum(1 for x in vs if x <= v) / len(vs), 2)
    reach = collections.defaultdict(lambda: {"plays": 0, "reach": 0.0, "djs": collections.Counter(), "mixes": set(), "scenes": collections.Counter()})
    edges = collections.Counter()
    for r in c.execute("select mix_url, track_id from mix_plays"):
        m = mixes.get(r["mix_url"])
        if not m or r["track_id"] not in meta: continue
        w = math.log10((m.get("plays") or 0) + 1) or 0.3       # a play in an unknown-size mix still counts a little
        dj = (m.get("dj") or m.get("title") or "?")[:40]
        for n in meta[r["track_id"]]:
            k = norm(n); a = reach[k]
            a["name"] = n; a["plays"] += 1; a["reach"] += w; a["djs"][dj] += w; a["mixes"].add(r["mix_url"]); a["scenes"][m["scene"]] += 1
            edges[(dj, k)] += w
    producers = []
    for k, a in reach.items():
        producers.append({"key": k, "name": a["name"], "plays": a["plays"], "reach": round(a["reach"], 2),
                          "mixes": len(a["mixes"]), "scene": a["scenes"].most_common(1)[0][0],
                          "top_djs": [d for d, _ in a["djs"].most_common(3)]})
    producers.sort(key=lambda x: -x["reach"])
    # appetite: age-adjusted plays per mix by scene
    app = collections.defaultdict(list)
    today = dt.date.today()
    for m in mixes.values():
        p = _date(m.get("published"))
        if not p or not m.get("plays"): continue
        days = max(7, (today - p).days)
        app[m["scene"]].append(m["plays"] / days)
    appetite = {sc: {"mixes": len(v), "plays_per_day_median": round(st.median(v), 1)} for sc, v in app.items() if len(v) >= 3}
    return {"generated": today.isoformat(),
            "mixes_with_popularity": sum(1 for m in mixes.values() if m.get("plays")),
            "note": "reach = sum of log10(plays+1) over mixes that played the record; plays ranked within source",
            "producers": producers[:200],
            "edges": [{"dj": d, "producer": k, "w": round(w, 2)} for (d, k), w in edges.most_common(400)],
            "appetite": appetite}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/reach.json")
    a = ap.parse_args()
    out = build(a.db)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"mixes_with_popularity": out["mixes_with_popularity"], "producers": len(out["producers"]),
                      "top": [(p["name"], p["reach"]) for p in out["producers"][:5]], "appetite": out["appetite"]}, indent=1))


if __name__ == "__main__":
    main()
