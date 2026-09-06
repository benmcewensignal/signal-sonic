"""Discogs: aliases, memberships and labels for name resolution.

Half of the bookings we cannot join to a release are alias or b2b problems: the
same person under two names, a duo booked under a shared name, a producer who
releases as X and DJs as Y. Discogs is the reference for exactly that. For each
named artist we hold, fetch the artist record and store:

  - aliases      (other names the same person releases under)
  - members      (for groups) and groups (for people)
  - real name, profile, and the labels they have released on

The artist join then matches a booking to a release through any alias, and the
labels give a second key for the label instruments.

Needs DISCOGS_TOKEN. Rate limit is 60 requests a minute authenticated; we go slower.

  python -m sonic.discogs --db sonic.db --limit 600
"""
import argparse, json, os, sqlite3, time, urllib.parse, urllib.request
from .artists import norm

API = "https://api.discogs.com"


def _get(path, token, params=None):
    q = urllib.parse.urlencode(params or {})
    req = urllib.request.Request(f"{API}{path}?{q}", headers={
        "User-Agent": "signal-sonic/1.0 +https://earlysignal.live", "Authorization": f"Discogs token={token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--limit", type=int, default=600)
    ap.add_argument("--sleep", type=float, default=1.1)
    a = ap.parse_args()
    token = os.environ.get("DISCOGS_TOKEN")
    if not token:
        print("discogs: DISCOGS_TOKEN not set; skipping"); return
    c = sqlite3.connect(a.db)
    c.execute("""create table if not exists artist_aliases(
        artist_key text primary key, name text, discogs_id integer, real_name text,
        aliases text, groups text, members text, labels text, fetched_at real)""")
    c.commit()
    have = {r[0] for r in c.execute("select artist_key from artist_aliases")}
    # most-released first: they anchor the most joins
    import collections
    cnt = collections.Counter(); disp = {}
    for (arts,) in c.execute("select artists from track_meta where artists is not null"):
        try: names = json.loads(arts)
        except Exception: continue
        for n in names:
            k = norm(n)
            if k and k not in have: cnt[k] += 1; disp[k] = n
    todo = [k for k, _ in cnt.most_common(a.limit)]
    print(f"discogs: {len(cnt)} artists without aliases, fetching {len(todo)}", flush=True)
    ok = miss = err = 0
    for i, k in enumerate(todo, 1):
        name = disp[k]
        try:
            s = _get("/database/search", token, {"q": name, "type": "artist", "per_page": 3})
            hit = None
            for r in s.get("results", []):
                if norm(r.get("title", "")) == k: hit = r; break
            if not hit and s.get("results"): hit = s["results"][0] if norm(s["results"][0].get("title", "")).startswith(k[:6]) else None
            if not hit:
                miss += 1
                c.execute("insert or replace into artist_aliases values(?,?,?,?,?,?,?,?,?)", (k, name, None, None, "[]", "[]", "[]", "[]", time.time()))
            else:
                time.sleep(a.sleep)
                art = _get(f"/artists/{hit['id']}", token)
                aliases = [x.get("name") for x in art.get("aliases", []) if x.get("name")]
                groups = [x.get("name") for x in art.get("groups", []) if x.get("name")]
                members = [x.get("name") for x in art.get("members", []) if x.get("name")]
                labels = []
                try:
                    time.sleep(a.sleep)
                    rel = _get(f"/artists/{hit['id']}/releases", token, {"per_page": 50, "sort": "year", "sort_order": "desc"})
                    labels = sorted({x.get("label") for x in rel.get("releases", []) if x.get("label")})[:20]
                except Exception:
                    pass
                c.execute("insert or replace into artist_aliases values(?,?,?,?,?,?,?,?,?)",
                          (k, name, hit["id"], art.get("realname"), json.dumps(aliases), json.dumps(groups), json.dumps(members), json.dumps(labels), time.time()))
                ok += 1
        except Exception as e:
            err += 1
            if "429" in repr(e): time.sleep(30)
        if i % 50 == 0:
            c.commit(); print(f"  {i}/{len(todo)} ok={ok} miss={miss} err={err}", flush=True)
        time.sleep(a.sleep)
    c.commit()
    n_alias = c.execute("select count(*) from artist_aliases where aliases<>'[]' or groups<>'[]' or members<>'[]'").fetchone()[0]
    print(f"discogs: done ok={ok} miss={miss} err={err}; artists with any alias/group/member link: {n_alias}", flush=True)


if __name__ == "__main__":
    main()
