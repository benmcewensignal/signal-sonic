"""Streaming stock for every named artist: Last.fm listeners and playcount, cached
in artist_pop. With RA bookings and interest already in the join, this gives the
second axis of popularity — audience at home versus pull on the circuit — and the
gap between the two is the interesting part (a big streamer nobody books, a heavily
booked name few people stream).

  python -m sonic.listeners --db sonic.db --limit 800
"""
import argparse, json, os, sqlite3, time, urllib.parse, urllib.request

API = "https://ws.audioscrobbler.com/2.0/"
DDL = """create table if not exists artist_pop(
  artist_key text primary key, name text, listeners integer, playcount integer,
  source text, fetched_at text)"""

def norm(n):
    import re, unicodedata
    s = unicodedata.normalize("NFKD", n or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\((uk|us|de|it|fr|nl|live|dj set|official)\)", " ", s)
    s = re.sub(r"\b(feat\.?|ft\.?|featuring)\b.*$", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()

def fetch(name, key):
    q = urllib.parse.urlencode({"method": "artist.getinfo", "artist": name, "api_key": key, "format": "json", "autocorrect": "1"})
    req = urllib.request.Request(API + "?" + q, headers={"User-Agent": "signal-sonic/listeners"})
    with urllib.request.urlopen(req, timeout=12) as r:
        d = json.loads(r.read().decode())
    a = d.get("artist") or {}
    st = a.get("stats") or {}
    return (int(st.get("listeners") or 0), int(st.get("playcount") or 0)) if st else (None, None)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--limit", type=int, default=800)
    ap.add_argument("--sleep", type=float, default=0.28)
    a = ap.parse_args()
    key = os.environ.get("LASTFM_API_KEY")
    if not key:
        print("listeners: LASTFM_API_KEY not set; skipping"); return
    c = sqlite3.connect(a.db); c.execute(DDL); c.commit()
    have = {r[0] for r in c.execute("select artist_key from artist_pop")}
    # candidates: artists on named records, most-released first (they matter most)
    counts = {}
    for (arts,) in c.execute("select artists from track_meta where artists is not null"):
        try: names = json.loads(arts)
        except Exception: continue
        for n in names:
            k = norm(n)
            if k and k not in have: counts.setdefault(k, [0, n]); counts[k][0] += 1
    todo = sorted(counts.items(), key=lambda kv: -kv[1][0])[:a.limit]
    print(f"listeners: {len(counts)} artists lack a reading; fetching {len(todo)}", flush=True)
    ok = err = 0
    for i, (k, (n_rec, disp)) in enumerate(todo, 1):
        try:
            li, pc = fetch(disp, key)
            c.execute("insert or replace into artist_pop values(?,?,?,?,?,?)",
                      (k, disp, li, pc, "lastfm", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
            ok += 1
        except Exception as e:
            err += 1
            c.execute("insert or replace into artist_pop values(?,?,?,?,?,?)", (k, disp, None, None, f"error: {str(e)[:60]}", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        if i % 100 == 0: c.commit(); print(f"  {i}/{len(todo)} ok={ok} err={err}", flush=True)
        time.sleep(a.sleep)
    c.commit()
    print(f"listeners: done ok={ok} err={err}; {len(counts)-len(todo)} still to fetch", flush=True)

if __name__ == "__main__":
    main()
