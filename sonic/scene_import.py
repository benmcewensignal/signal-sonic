"""Import the scenes workflow's genres (data/track-genres.json) into the database: a row in track_scenes, with every
required column, for each record whose Beatport genre maps to a scene. Runs in every queue run; the scenes workflow
never writes the database itself, because two workflows saving the one database file would overwrite each other."""
import argparse, json, os, sqlite3

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--db", default="sonic.db"); ap.add_argument("--map", default="data/track-genres.json")
    a = ap.parse_args()
    if not os.path.exists(a.map): print("scene_import: no genre map yet"); return
    M = json.load(open(a.map)); c = sqlite3.connect(a.db); n = 0
    for tid, v in M.items():
        scene = v[0] if isinstance(v, list) else v
        if scene:
            n += c.execute("insert or ignore into track_scenes(track_id, scene, weight, chart_rank, source, week) values(?, ?, 1.0, NULL, 'beatport:track-genre', 'genre')", (tid, scene)).rowcount
    c.commit(); print(json.dumps({"genre_map": len(M), "scenes_added": n}))

if __name__ == "__main__":
    main()
