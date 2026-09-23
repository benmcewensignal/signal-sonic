"""Measure real previews with the corpus analyser and dump what the browser port must reproduce.

Picks records across every scene from the release database, downloads their previews, and writes
for each: the decoded 22,050 Hz samples (raw float32, so the port reads the identical numbers) and
the analyser's measures. tools/port_check.js then runs the port on the same samples and compares.
"""
import sqlite3, json, sys, os, random, numpy as np, librosa
sys.path.insert(0, ".")
from sonic.analyser_local import LocalAnalyser
sys.path.insert(0, 'tools'); import analyser_29 as A29
from sonic.beatport import download_preview
n_per = int(sys.argv[1]) if len(sys.argv) > 1 else 10
c = sqlite3.connect("sonic.db"); A = LocalAnalyser(); A2 = A29.LocalAnalyser(); os.makedirs("portcheck", exist_ok=True)
scenes = [r[0] for r in c.execute("select distinct scene from track_scenes where week like '____-M__'")]
random.seed(7); picked = []
for s in scenes:
    ids = [r[0] for r in c.execute("""select t.track_id from tracks t join track_scenes ts on ts.track_id=t.track_id join preview_cache p on p.track_id=t.track_id
        where t.analyser_id='local' and t.analyser_ver like '2.9%' and ts.scene=? and ts.week like '____-M__' group by t.track_id""", (s,))]
    random.shuffle(ids); picked += [(s, i) for i in ids[:n_per]]
out = []
for s, tid in picked:
    url = c.execute("select url from preview_cache where track_id=?", (tid,)).fetchone()[0]
    try:
        path = download_preview(url)
        y, sr = librosa.load(path, sr=22050, mono=True); y = (y / (np.max(np.abs(y)) or 1.0)).astype(np.float32)
        fv = A.analyse(path); d = fv.__dict__ if hasattr(fv, "__dict__") else dict(fv)
        y.tofile(f"portcheck/{len(out)}.f32")
        out.append({"i": len(out), "track_id": tid, "scene": s, "embedding": d["embedding"], "emb29": [float(v) for v in A2._embedding(y, 22050)], "rhythm_vector": d["rhythm_vector"], "tempo": d["tempo"],
                    "loudness": d["loudness"], "energy_curve": d["energy_curve"], "bass_weight": d["bass_weight"], "drum_density": d["drum_density"], "drum_swing": d["drum_swing"], "vocal_presence": d["vocal_presence"]})
    except Exception as e:
        print(f"skip {tid}: {type(e).__name__}", flush=True)
json.dump(out, open("portcheck/oracle.json", "w"))
print(f"measured {len(out)} previews across {len(scenes)} scenes", flush=True)
