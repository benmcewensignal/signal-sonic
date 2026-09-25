"""A Discogs sample through its API (60 requests a minute with a personal token): for corpus records,
the community's styles for the matching release, against Beatport's tag and the scene model's call.
Tells us whether the monthly dump is worth fetching before anyone downloads 10 GB."""
import sys, os, json, time, sqlite3, random, re, urllib.request, urllib.parse, collections
os.makedirs("data/discogs", exist_ok=True)
db, n = sys.argv[1], int(sys.argv[2]); tok = os.environ.get("DISCOGS_TOKEN")
if not tok: print("::error title=Discogs::no DISCOGS_TOKEN secret: create a personal access token at discogs.com/settings/developers and add it to the repository's secrets"); sys.exit(1)
c = sqlite3.connect(db); tag = {}
for t, s in c.execute("select track_id, scene from track_scenes where week like '____-M__' order by week"): tag.setdefault(t, s)
rows = []
for t, name, arts in c.execute("select track_id, name, artists from track_meta"):
    try: a = (json.loads(arts) if arts and arts.startswith("[") else [arts])[0]
    except Exception: a = None
    if t in tag and name and a: rows.append((t, a, re.sub(r"\s*\(.*?\)", "", name)))
random.Random(9).shuffle(rows); UA = {"User-Agent": "SignalSonic/1.0 +https://www.earlysignal.live", "Authorization": f"Discogs token={tok}"}
out = open("data/discogs/api_sample.jsonl", "w"); st = collections.Counter(); by = collections.defaultdict(collections.Counter)
for t, a, title in rows[:n]:
    q = urllib.parse.urlencode({"type": "release", "artist": a, "track": title, "per_page": 3})
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://api.discogs.com/database/search?{q}", headers=UA), timeout=30).read())
    except Exception as e:
        st["failed"] += 1; time.sleep(2); continue
    st["asked"] += 1; res = d.get("results", [])
    if res and res[0].get("style"):
        st["found"] += 1; r = res[0]; out.write(json.dumps({"track_id": t, "tag": tag[t], "styles": r.get("style"), "genre": r.get("genre"), "year": r.get("year"), "label": (r.get("label") or [None])[0]}) + "\n")
        for s in r["style"]: by[tag[t]][s] += 1
    time.sleep(1.05)
out.close()
json.dump({"counts": st, "styles_by_tag": {k: v.most_common(6) for k, v in by.items()}}, open("data/discogs/api_summary.json", "w"), indent=1)
msg = f"{st['asked']} asked, {st['found']} found with styles ({st['found']/max(1,st['asked'])*100:.0f}%), {st['failed']} failed. Top styles by Beatport tag: " + "; ".join(f"{k}: " + ", ".join(s for s, _ in v.most_common(3)) for k, v in sorted(by.items()))
print(msg); print(f"::notice title=Discogs API sample::{msg[:1900]}")
