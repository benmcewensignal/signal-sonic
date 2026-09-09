"""Analyser v3: a pretrained music representation.

v1 and v2 are hand-built statistics (MFCC means and spreads, chroma, contrast) over a
two-minute preview. They carry genre information — a classifier recovers the scene at
four times chance — but they average timbre across the clip and lose most of what
distinguishes close genres: groove, arrangement, how a record moves. MERT is trained
on millions of tracks specifically for music understanding; its embeddings typically
double fine-grained genre accuracy over hand-built features.

v3 embeddings live in their own table and never replace v1 or v2, so every existing
series is untouched. Each record gets a 768-dim vector: the mean over time of the
model's final layer.

This module cannot run where the model cannot be downloaded. It therefore self-tests
on a synthetic tone before touching a single real record, and refuses to proceed if
the embedding does not come out at the expected shape.

  python -m sonic.embed_v3 --db sonic.db --limit 300 --budget-minutes 50
"""
import argparse, json, os, sqlite3, tempfile, time, urllib.request
import numpy as np

MODEL = "m-a-p/MERT-v1-95M"
DIM = 768
SR = 24000
MAX_SECONDS = 120


def load_model():
    import torch
    from transformers import AutoModel, Wav2Vec2FeatureExtractor
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    model = AutoModel.from_pretrained(MODEL, trust_remote_code=True).eval()
    fe = Wav2Vec2FeatureExtractor.from_pretrained(MODEL, trust_remote_code=True)
    return model, fe


def embed_waveform(model, fe, y):
    import torch
    inputs = fe(y, sampling_rate=SR, return_tensors="pt")
    with torch.no_grad():
        out = model(**inputs, output_hidden_states=True)
    last = out.hidden_states[-1][0]              # (frames, 768)
    return last.mean(dim=0).cpu().numpy().astype(np.float32)


def load_audio(path):
    import librosa
    y, _ = librosa.load(path, sr=SR, mono=True, duration=MAX_SECONDS)
    if y.size < SR: raise ValueError("audio too short")
    return y / (np.max(np.abs(y)) or 1.0)


def self_test(model, fe):
    """A four-second tone must embed to a finite 768-vector. If not, do no work."""
    t = np.linspace(0, 4, 4 * SR, endpoint=False)
    y = (0.5 * np.sin(2 * np.pi * 220 * t) + 0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    v = embed_waveform(model, fe, y)
    ok = v.shape == (DIM,) and np.isfinite(v).all() and float(np.abs(v).sum()) > 0
    print(f"self-test: shape {v.shape}, finite {np.isfinite(v).all()}, norm {float(np.linalg.norm(v)):.3f} -> {'ok' if ok else 'FAIL'}", flush=True)
    return ok


def preview_url(track_id, token):
    from .beatport import _get
    if not str(track_id).startswith("bp:"): return None
    try:
        d = _get(f"/catalog/tracks/{str(track_id).split(':')[-1]}/", token)
        return (d.get("sample_url") or (d.get("preview") or {}).get("mp3", {}).get("url") or "") or None
    except Exception:
        return None


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "signal-sonic/v3"})
    fd, path = tempfile.mkstemp(suffix=".mp3")
    with urllib.request.urlopen(req, timeout=25) as r, os.fdopen(fd, "wb") as f:
        f.write(r.read())
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--budget-minutes", type=int, default=50)
    a = ap.parse_args()
    c = sqlite3.connect(a.db)
    c.execute("""create table if not exists embeddings_v3(
        track_id text primary key, model text, dim integer, vector text, embedded_at real)""")
    c.commit()
    model, fe = load_model()
    if not self_test(model, fe):
        print("v3: self-test failed; doing no work", flush=True); return
    from .beatport import get_token
    token = get_token()
    # spread across scene-months: sweeping newest-first fills up with whichever scene was
    # backfilled last, and the comparison this exists for needs the same scenes and months
    # the hand-built embedding covers
    import collections as _c, random as _r
    have = {x[0] for x in c.execute("select track_id from embeddings_v3")}
    pool = _c.defaultdict(list)
    for row in c.execute("""select ts.scene, ts.week, ts.track_id from track_scenes ts
                            join tracks t on t.track_id=ts.track_id and t.analyser_id='local'
                            where ts.week like '____-M__'"""):
        if row[2] not in have: pool[(row[0], row[1])].append(row[2])
    keys = sorted(pool); rr = _r.Random(4)
    for k in keys: rr.shuffle(pool[k])
    todo = []
    while len(todo) < a.limit and any(pool[k] for k in keys):
        for k in keys:
            if pool[k] and len(todo) < a.limit: todo.append(pool[k].pop())
    print(f"v3: {len(todo)} records to embed", flush=True)
    t0 = time.time(); done = err = 0
    for tid in todo:
        if (time.time() - t0) / 60 > a.budget_minutes: print("budget reached", flush=True); break
        path = None
        try:
            url = preview_url(tid, token)
            if not url: err += 1; continue
            path = fetch(url)
            v = embed_waveform(model, fe, load_audio(path))
            c.execute("insert or replace into embeddings_v3 values(?,?,?,?,?)",
                      (tid, MODEL, DIM, json.dumps([round(float(x), 5) for x in v]), time.time()))
            done += 1
            if done % 50 == 0: c.commit(); print(f"  {done}/{len(todo)} embedded, {err} failed", flush=True)
        except Exception as e:
            err += 1
            if err <= 3: print(f"  {tid}: {type(e).__name__}: {str(e)[:80]}", flush=True)
        finally:
            if path:
                try: os.unlink(path)
                except OSError: pass
    c.commit()
    left = c.execute("""select count(*) from tracks where analyser_id='local'
                        and track_id not in (select track_id from embeddings_v3)""").fetchone()[0]
    print(f"v3: {done} embedded, {err} failed, {left} remaining", flush=True)
    if left > 0: open("queue/.more", "w").write("embed3\n")


if __name__ == "__main__":
    main()
