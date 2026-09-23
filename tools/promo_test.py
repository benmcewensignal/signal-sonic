"""Can a record's sound pick the DJs who play it? Leave-one-record-out on the tracklist plays.

For each record played in the sets: remove it (and every version of the same song) from every
DJ's history, rank all DJs by how close the record sounds to what each plays, and see where the
DJs who really played it land. Sound is set against the baseline a promo list uses today: DJs
ranked by how often they play the record's Beatport genre.
  python tools/promo_test.py sonic.db data/tracklists
"""
import sys, json, glob, sqlite3, collections, re
import numpy as np
db, tl = sys.argv[1], sys.argv[2]
c = sqlite3.connect(db)
genre = {}; song = {}
for f in glob.glob(f"{tl}/*.json"):
    if f.endswith("curves.json"): continue
    for s in json.load(open(f))["sets"]:
        for r in s["records"]:
            if r.get("bp"):
                genre[r["bp"]] = r.get("genre")
                song[r["bp"]] = (re.sub(r"[^a-z0-9]", "", (r.get("artist") or "").lower())[:20], re.sub(r"\(.*?\)|[^a-z0-9]", "", (r.get("title") or "").lower())[:30])
plays = collections.defaultdict(set)
for dj, tid in c.execute("select dj, track_id from tracklist_plays where track_id is not null"): plays[tid].add(dj)
E = {}
for tid, f in c.execute("select track_id, features from tracks where analyser_id='local' and analyser_ver like '3.0%'"):
    if tid in plays:
        j = json.loads(f); e = j.get("embedding"); tp = j.get("tempo"); ec = j.get("energy_curve"); rv = j.get("rhythm_vector")
        sca = [j.get(k) for k in ("loudness", "bass_weight", "drum_density", "vocal_presence", "drum_swing")]
        # the scene model's 75 inputs: the 45 numbers alone cannot tell drum and bass from techno
        if e and len(e) == 45 and tp and ec and len(ec) == 8 and isinstance(rv, list) and len(rv) == 16 and all(isinstance(x, (int, float)) for x in sca):
            E[tid] = np.array(e + [np.log2(tp * 2 if tp < 100 else tp)] + ec + sca + rv, float)
ids = sorted(E); X = np.array([E[t] for t in ids]); mu, sd = X.mean(0), X.std(0) + 1e-9
Z = (X - mu) / sd; Z /= np.linalg.norm(Z, axis=1, keepdims=True); idx = {t: i for i, t in enumerate(ids)}
djs = sorted({d for t in ids for d in plays[t]}); nd = len(djs)
by_dj = {d: [t for t in ids if d in plays[t]] for d in djs}
print(f"{len(ids)} measured records played by {nd} DJs")
def rank_sound(t):
    sid = song.get(t); out = []
    for d in djs:
        pool = [idx[u] for u in by_dj[d] if u != t and song.get(u) != sid]
        if not pool: out.append((d, -9)); continue
        # the DJ's average sound: a nearest-matches score favours DJs with longer histories
        cen = Z[pool].mean(0); out.append((d, float(cen @ Z[idx[t]] / (np.linalg.norm(cen) or 1))))
    return [d for d, _ in sorted(out, key=lambda x: -x[1])]
def rank_genre(t):
    g = genre.get(t); sid = song.get(t); out = []
    for d in djs:
        pool = [u for u in by_dj[d] if u != t and song.get(u) != sid]
        out.append((d, (sum(1 for u in pool if genre.get(u) == g) / len(pool)) if pool else 0))
    return [d for d, _ in sorted(out, key=lambda x: -x[1])]
res = {"sound": [], "genre": []}
for t in ids:
    truth = plays[t]
    if len(truth) == nd: continue
    for name, fn in (("sound", rank_sound), ("genre", rank_genre)):
        r = fn(t); best = min(r.index(d) for d in truth) + 1; res[name].append(best)
top = min(5, max(1, nd // 5))
chance_top = 1 - np.prod([(nd - len(plays[t]) - i) / (nd - i) for t in ids[:1] for i in range(top)]) if nd > top else 1.0
msg = f"{len(res['sound'])} records, {nd} DJs: a DJ who played it ranked in the top {top} by sound {np.mean(np.array(res['sound']) <= top)*100:.0f}%, by genre {np.mean(np.array(res['genre']) <= top)*100:.0f}% (about {top/nd*100:.0f}% by chance); median rank by sound {np.median(res['sound']):.0f}, by genre {np.median(res['genre']):.0f}, of {nd}"
print(msg)
json.dump({"n_records": len(res["sound"]), "n_djs": nd, "top": top, "sound": res["sound"], "genre": res["genre"]}, open("/tmp/promo_test.json", "w"))
