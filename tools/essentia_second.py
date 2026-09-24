"""A second opinion from Essentia's Discogs-trained style model (non-commercial licence: private
calibration only, never shipped). For a sample of previews: its top styles against Beatport's tag
and against our scene model's call."""
import sys, os, json, sqlite3, pickle, collections, random, urllib.request, numpy as np
sys.path.insert(0, "."); from sonic import beatport as B
from essentia.standard import MonoLoader, TensorflowPredictEffnetDiscogs, TensorflowPredict2D
db, per = sys.argv[1], int(sys.argv[2])
emb = TensorflowPredictEffnetDiscogs(graphFilename="models/discogs-effnet-bs64-1.pb", output="PartitionedCall:1")
head = TensorflowPredict2D(graphFilename="models/genre_discogs400-discogs-effnet-1.pb", input="serving_default_model_Placeholder", output="PartitionedCall:0")
labels = json.load(open("models/genre_discogs400-discogs-effnet-1.json"))["classes"]
c = sqlite3.connect(db); url = dict(c.execute("select track_id, url from preview_cache")); by = collections.defaultdict(list)
H = set(json.load(open("data/hidden-scenes.json")))
for t, s in c.execute("select track_id, scene from track_scenes where week like '____-M__'"):
    if t in url and s not in H: by[s].append(t)
rng = random.Random(7); pick = [(t, s) for s, ts in by.items() for t in rng.sample(sorted(set(ts)), min(per, len(set(ts))))]
out = open("data/essentia/preds.jsonl", "w"); top = collections.defaultdict(collections.Counter)
for i, (t, s) in enumerate(pick):
    try:
        a = MonoLoader(filename=B.download_preview(url[t]), sampleRate=16000, resampleQuality=4)()
        p = head(emb(a)).mean(0); o = np.argsort(-p)[:5]
        r = {"track_id": t, "tag": s, "styles": [[labels[j], round(float(p[j]), 3)] for j in o]}; out.write(json.dumps(r) + "\n")
        top[s][labels[o[0]].split("---")[-1]] += 1
    except Exception as e: print("skip", t, type(e).__name__, flush=True)
    if i % 200 == 0: print(f"{i} of {len(pick)}", flush=True)
out.close()
msg = "Essentia's top style by Beatport tag: " + "; ".join(f"{s}: " + ", ".join(f"{k} {v}" for k, v in cnt.most_common(3)) for s, cnt in sorted(top.items()))
json.dump({s: cnt.most_common(8) for s, cnt in top.items()}, open("data/essentia/summary.json", "w"), indent=1)
print(msg); print(f"::notice title=Essentia second opinion::{msg[:1900]}")
