"""How much of a scene do we actually see?

Our sound measurement samples 40 to 150 records a scene-month from Beatport's chart.
Beatport's own release counts say that is about 1.6% of what it lists. But Beatport is
one store with a tech-house lean, so its total is not the population either.

This asks an independent catalogue — Deezer, which is free, needs no key, and indexes
far beyond dance specialists — how many records exist for the same genre and month, and
what share of them we hold. It answers two questions our own data cannot:

  how much of the released music we see, measured against something that is not Beatport
  whether the records we sample are the ones people can find at all

Nothing here touches the sound layer. A Deezer preview is 30 seconds against Beatport's
two minutes, so measuring audio from both would silently break two years of comparability.
This is a read-only probe: counts and identifiers, never features.

  python -m sonic.coverage --db sonic.db --out data/coverage.json
"""
import argparse, collections, json, sqlite3, time, urllib.parse, urllib.request

DEEZER = "https://api.deezer.com"
SCENE_TERMS = {
    "deep-house": "deep house", "tech-house": "tech house", "house": "house",
    "techno-peak-time": "techno", "techno-raw-deep-hypnotic": "raw techno", "hard-techno": "hard techno",
    "melodic-house-techno": "melodic techno", "afro-house": "afro house", "amapiano": "amapiano",
    "drum-and-bass": "drum and bass", "uk-garage-speed-garage": "uk garage",
    "140-deep-dubstep-grime": "dubstep", "breaks-breakbeat-uk-bass": "breakbeat",
    "bass-house": "bass house", "trance-main-floor": "trance", "psy-trance": "psytrance",
    "progressive-house": "progressive house", "indie-dance": "indie dance",
    "organic-house": "organic house", "uk-funky-gqom": "gqom",
}


def _get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "signal-sonic/coverage"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(2 * (i + 1))


DEEZER_CAP = 300          # the search endpoint caps its total; anything at or near it is not a count


def deezer_count(term, year=None):
    """Deezer's search total, which saturates at 300. Returns (count, capped)."""
    q = f'{term} {year}' if year else term
    d = _get(f"{DEEZER}/search/track?q={urllib.parse.quote(q)}&limit=1")
    n = int(d.get("total") or 0)
    return n, n >= DEEZER_CAP - 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/coverage.json")
    ap.add_argument("--sleep", type=float, default=0.4)
    a = ap.parse_args()
    c = sqlite3.connect(a.db)
    ours = collections.Counter()
    for scene, week, n in c.execute("""select scene, week, count(*) from track_scenes
                                       where week like '____-M__' group by scene, week"""):
        ours[scene] += n
    months = c.execute("select count(distinct week) from track_scenes where week like '____-M__'").fetchone()[0] or 24
    try:
        sup = json.load(open("data/supply.json")).get("scenes", {})
    except Exception:
        sup = {}
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "note": ("our sample against two independent counts: Beatport's own release total, and Deezer's "
                    "catalogue total for the same term. Deezer is not dance-specialist, so its total is an "
                    "upper bound including reissues and mislabels; Beatport's is a lower bound for one store. "
                    "The truth sits between, and both are counts, never audio."),
           "scenes": {}}
    for scene, n_ours in sorted(ours.items()):
        term = SCENE_TERMS.get(scene)
        if not term: continue
        per_month = n_ours / max(1, months)
        rec = {"we_sample_per_month": round(per_month, 1), "records_held": n_ours}
        bp = (sup.get(scene) or {}).get("latest_total")
        if bp:
            rec["beatport_released_per_month"] = bp
            rec["share_of_beatport"] = round(per_month / bp, 5)
        try:
            total, capped = deezer_count(term)
            if capped:
                rec["deezer"] = "search total saturates at the API cap, so it is not a population count"
            else:
                rec["deezer_matching_tracks"] = total
        except Exception as e:
            rec["deezer_error"] = f"{type(e).__name__}: {str(e)[:60]}"
        out["scenes"][scene] = rec
        time.sleep(a.sleep)
    shares = [v["share_of_beatport"] for v in out["scenes"].values() if v.get("share_of_beatport")]
    if shares:
        shares.sort()
        out["median_share_of_beatport"] = shares[len(shares)//2]
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"scenes": len(out["scenes"]),
                      "median share of Beatport": out.get("median_share_of_beatport"),
                      "deezer usable": sum(1 for v in out["scenes"].values() if v.get("deezer_matching_tracks"))}, indent=1))


if __name__ == "__main__":
    main()
