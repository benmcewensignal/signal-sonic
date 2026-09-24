"""A MusicBrainz sample: how many corpus records it knows through their ISRC, and what it adds.
ISRCs come from Beatport's track records; MusicBrainz is asked one record a second, as it requires."""
import sys, json, time, sqlite3, random, urllib.request, collections
sys.path.insert(0, "."); from sonic import beatport as B
db, n = sys.argv[1], int(sys.argv[2]); c = sqlite3.connect(db); tok = B.get_token()
ids = [r[0] for r in c.execute("select track_id from track_meta where track_id like 'bp:%'")]; random.Random(5).shuffle(ids)
UA = {"User-Agent": "signal-sonic/1.0 ( https://www.earlysignal.live )", "Accept": "application/json"}
out = open("data/musicbrainz/sample.jsonl", "w"); stats = collections.Counter()
for t in ids[:n]:
    try: isrc = (B._get(f"/catalog/tracks/{t.split(':')[1]}/", tok) or {}).get("isrc")
    except Exception: isrc = None
    stats["asked"] += 1
    if not isrc: continue
    stats["with_isrc"] += 1; time.sleep(1.1)
    try:
        d = json.loads(urllib.request.urlopen(urllib.request.Request(f"https://musicbrainz.org/ws/2/isrc/{isrc}?fmt=json&inc=releases+artist-credits", headers=UA), timeout=30).read())
        recs = d.get("recordings", [])
        if recs:
            stats["in_musicbrainz"] += 1; r = recs[0]
            dates = sorted(x.get("date") for x in r.get("releases", []) if x.get("date"))
            out.write(json.dumps({"track_id": t, "isrc": isrc, "mbid": r.get("id"), "releases": len(r.get("releases", [])), "first_date": dates[0] if dates else None}) + "\n")
            if len(r.get("releases", [])) > 1: stats["on_several_releases"] += 1
    except Exception: stats["lookup_failed"] += 1
out.close()
msg = f"{stats['asked']} records asked: {stats['with_isrc']} have an ISRC at Beatport, {stats['in_musicbrainz']} are in MusicBrainz, {stats['on_several_releases']} of those on more than one release; {stats['lookup_failed']} lookups failed"
print(msg); print(f"::notice title=MusicBrainz sample::{msg}")
