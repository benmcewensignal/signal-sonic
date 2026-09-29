"""Give a scene to records that have none. Most arrived through DJ tracklists and the classics, which record no
genre: about 9,400 recognisable records, and 94% of what DJs played in the last year's sets, so the DJ tool's
scene filter dropped them and DJ-play share per scene could not be compared. Each record's Beatport genre is
fetched and mapped through scene_map.json into track_scenes, with week 'genre' and source 'beatport:track-genre':
the recognition index takes a record's scene from any row, while the month and week series other analyses read
select by week pattern and so never see these rows. Every record tried is noted in scene_fill_seen, so a genre
outside the map, or a record Beatport no longer has, is not fetched again and cannot keep the job alive."""
import argparse, json, os, sqlite3, time
from .beatport import get_token, _get

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--map", default="scene_map.json")
    ap.add_argument("--limit", type=int, default=2500); ap.add_argument("--budget-minutes", type=float, default=40)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--out", default=None, help="write found genres to this JSON map instead of the database (the scenes workflow; each queue run imports it)")
    a = ap.parse_args()
    M = {int(k): v["scene"] for k, v in json.load(open(a.map)).items() if not str(k).startswith("_")}
    c = sqlite3.connect(a.db)
    c.execute("create table if not exists scene_fill_seen(track_id text primary key, genre_id integer, scene text, at text)")
    found = json.load(open(a.out)) if a.out and os.path.exists(a.out) else {}
    todo = [r[0] for r in c.execute("""select distinct t.track_id from tracks t
                                       left join track_scenes s on s.track_id = t.track_id
                                       left join scene_fill_seen f on f.track_id = t.track_id
                                       where s.track_id is null and f.track_id is null and t.track_id like 'bp:%'""")]
    todo = [t for t in todo if t not in found]
    print(f"scene_fill: {len(todo)} records without a scene", flush=True)
    token = get_token(); t0 = time.time(); placed = unmapped = err = tried = 0; now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    for tid in todo[:a.limit]:
        if (time.time() - t0) / 60 > a.budget_minutes:
            print("budget reached", flush=True); break
        tried += 1; gid = None; scene = None
        try:
            d = _get(f"/catalog/tracks/{tid.split(':')[-1]}/", token)
            gid = (d.get("genre") or {}).get("id"); sub = (d.get("sub_genre") or {}).get("id")
            scene = M.get(gid) or (M.get(sub) if sub else None)
            if scene:
                if a.out is None:
                    c.execute("insert or ignore into track_scenes(track_id, scene, weight, chart_rank, source, week) values(?, ?, 1.0, NULL, 'beatport:track-genre', 'genre')", (tid, scene))
                placed += 1
            else:
                unmapped += 1
        except Exception as e:
            err += 1
            if err <= 3: print(f"  {tid}: {type(e).__name__}: {str(e)[:80]}", flush=True)
        if a.out is None: c.execute("insert or replace into scene_fill_seen values(?,?,?,?)", (tid, gid, scene, now))
        else: found[tid] = [scene, gid]
        if tried % 200 == 0: c.commit(); print(f"  {tried} tried, {placed} placed", flush=True)
        time.sleep(a.sleep)
    c.commit()
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump(dict(sorted(found.items())), open(a.out, "w"), separators=(",", ":"))
    left = len(todo) - tried
    print(json.dumps({"tried": tried, "placed": placed, "genre_outside_the_map": unmapped, "errors": err, "left": left}), flush=True)
    print("::notice title=scenes::" + json.dumps({"tried": tried, "placed": placed, "outside_the_map": unmapped, "errors": err, "left": left}), flush=True)
    if left > 0 and tried > 0 and a.out is None:
        os.makedirs("queue", exist_ok=True); open("queue/.more", "w").write("scenes\n")

if __name__ == "__main__":
    main()
