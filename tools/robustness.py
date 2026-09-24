"""Robustness: how far the conditions real users create move a reading.

For a sample of corpus previews, each record is measured clean and under six conditions: less
compressed (an unmastered demo), phone (bass and treble cut, a little room, a little noise), a 64
kbps file, and the first, middle and last thirty seconds alone. Volume is not a condition: the
analyser scales every file to the same peak. For each condition: whether the scene model's call
matches the clean call, accuracy against the tag, how close the 75 numbers stay, and which inputs
move most. The vectors are kept for training on these variations later.
  python tools/robustness.py sonic.db features/worker/scene_model.pkl 20
"""
import sys, os, json, sqlite3, pickle, tempfile, subprocess, collections, numpy as np, soundfile as sf, librosa
from scipy.signal import butter, sosfilt, fftconvolve
sys.path.insert(0, "."); from sonic.analyser_local import LocalAnalyser; from sonic import beatport as B
db, model_path, per = sys.argv[1], sys.argv[2], int(sys.argv[3])
M = pickle.load(open(model_path, "rb")); A = LocalAnalyser(); SR = 22050; rng = np.random.default_rng(3)
c = sqlite3.connect(db); url = dict(c.execute("select track_id, url from preview_cache"))
by = collections.defaultdict(list)
for t, s in c.execute("select track_id, scene from track_scenes where week like '____-M__' and track_id in (select track_id from tracks where analyser_ver like '3.0%')"):
    if t in url: by[s].append(t)
H = set(json.load(open("data/hidden-scenes.json"))) if os.path.exists("data/hidden-scenes.json") else set()
pick = []
for s, ts in by.items():
    if s in H: continue
    ts = sorted(set(ts)); rng.shuffle(ts); pick += [(t, s) for t in ts[:per]]
KEYS = ("loudness", "bass_weight", "drum_density", "vocal_presence", "drum_swing")
def vec(path):
    fv = A.analyse(path); j = fv.__dict__ if hasattr(fv, "__dict__") else dict(fv)
    j = {**j, **(j.get("extra") or {})}; tp = j["tempo"]
    return j["embedding"] + [float(np.log2(tp * 2 if tp < 100 else tp))] + list(j["energy_curve"]) + [j[k] for k in KEYS] + list(j["rhythm_vector"])
def call(x):
    z = (np.array(x, float) - np.array(M["mu"])) / np.array(M["sd"]); p = M["model"].predict_proba(z[None, :])[0]; return str(M["classes"][int(np.argmax(p))])
def wav(y):
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); sf.write(f.name, y, SR); return f.name
def less_compressed(y):
    y = y / (np.max(np.abs(y)) or 1); return np.sign(y) * np.abs(y) ** 1.35
def phone(y):
    y = sosfilt(butter(4, [200, 6000], btype="band", fs=SR, output="sos"), y)
    ir = rng.standard_normal(int(0.25 * SR)) * np.exp(-np.linspace(0, 8, int(0.25 * SR))); ir[0] = 6
    y = fftconvolve(y, ir / np.abs(ir).sum() * 4)[: len(y)]; return y + 0.003 * rng.standard_normal(len(y)) * (np.max(np.abs(y)) or 1)
def lowbit(path):
    out = path + ".64.mp3"; subprocess.run(["ffmpeg", "-loglevel", "quiet", "-y", "-i", path, "-b:a", "64k", out], check=True); return out
CONDS = ["less compressed", "phone", "64 kbps", "first 30 s", "middle 30 s", "last 30 s"]
rows = []; out = open("data/robustness/vectors.jsonl", "w")
for n, (t, s) in enumerate(pick):
    try:
        p = B.download_preview(url[t]); y, _ = librosa.load(p, sr=SR, mono=True)
        if len(y) < 40 * SR: continue
        base = wav(y); v = {"clean": vec(base)}
        v["less compressed"] = vec(wav(less_compressed(y))); v["phone"] = vec(wav(phone(y))); v["64 kbps"] = vec(lowbit(base))
        L = len(y); w = 30 * SR
        v["first 30 s"] = vec(wav(y[:w])); v["middle 30 s"] = vec(wav(y[(L - w) // 2:(L + w) // 2])); v["last 30 s"] = vec(wav(y[-w:]))
        r = {"track_id": t, "tag": s, "vectors": v}; out.write(json.dumps(r) + "\n"); rows.append(r)
    except Exception as e:
        print("skip", t, type(e).__name__, flush=True)
    if n % 50 == 0: print(f"{n} of {len(pick)}", flush=True)
out.close()
mu, sd = np.array(M["mu"]), np.array(M["sd"])
def z(x): return (np.array(x, float) - mu) / sd
clean_calls = [call(r["vectors"]["clean"]) for r in rows]; tags = [r["tag"] for r in rows]
res = {"records": len(rows), "clean_accuracy": float(np.mean([a == b for a, b in zip(clean_calls, tags)])), "conditions": {}}
names = [f"emb{i}" for i in range(45)] + ["tempo"] + [f"energy{i}" for i in range(8)] + list(KEYS) + [f"rhythm{i}" for i in range(16)]
for cnd in CONDS:
    cc = [call(r["vectors"][cnd]) for r in rows]
    zs = np.array([z(r["vectors"][cnd]) - z(r["vectors"]["clean"]) for r in rows]); shift = np.median(np.abs(zs), 0)
    cos = [float(np.dot(z(r["vectors"][cnd]), z(r["vectors"]["clean"])) / (np.linalg.norm(z(r["vectors"][cnd])) * np.linalg.norm(z(r["vectors"]["clean"])) + 1e-9)) for r in rows]
    res["conditions"][cnd] = {"same_call_as_clean": float(np.mean([a == b for a, b in zip(cc, clean_calls)])), "accuracy": float(np.mean([a == b for a, b in zip(cc, tags)])),
                              "median_similarity_to_clean": float(np.median(cos)), "most_moved_inputs": [[names[i], round(float(shift[i]), 2)] for i in np.argsort(-shift)[:5]]}
json.dump(res, open("data/robustness/summary.json", "w"), indent=1)
msg = f"{len(rows)} records; clean accuracy {res['clean_accuracy']*100:.0f}%. " + " | ".join(
    f"{k}: same call {v['same_call_as_clean']*100:.0f}%, accuracy {v['accuracy']*100:.0f}%, similarity {v['median_similarity_to_clean']:.2f}, most moved {', '.join(n for n, _ in v['most_moved_inputs'][:3])}"
    for k, v in res["conditions"].items())
print(msg); print(f"::notice title=robustness::{msg[:1900]}")
