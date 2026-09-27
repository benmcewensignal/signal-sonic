"""Beatport's key for a sample of corpus records, to test Sonic's key detection against the key DJs use.
Reads data/key-sample.json (track ids "bp:<id>"); writes data/key-check.json {track_id: key}, keys as C, C#, ... Am, C#m."""
import json, time, re
from sonic.beatport import get_token, _get

SHARP = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B", "Fb": "E", "E#": "F", "B#": "C"}

def norm(k):
    if not isinstance(k, dict): return None
    name = (k.get("name") or "").replace("\u266f", "#").replace("\u266d", "b").strip()
    m = re.match(r"^([A-G][#b]?)\s*(Major|Minor|maj|min)", name, re.I)
    if not m: return None
    root = SHARP.get(m.group(1), m.group(1)); minor = m.group(2).lower().startswith("min")
    return root + ("m" if minor else "")

def main():
    ids = json.load(open("data/key-sample.json"))["ids"]; tok = get_token(); out = {}; raw = {}; t0 = time.time()
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]; nums = [x.split(":", 1)[1] for x in chunk]
        try:
            d = _get("/catalog/tracks/", tok, {"id": ",".join(nums), "per_page": 100})
            got = {str(t.get("id")): t for t in (d.get("results") or [])}
        except Exception as e:
            got = {}; print("batch failed:", type(e).__name__, e, flush=True)
        for x, n in zip(chunk, nums):
            t = got.get(n)
            if t is None:   # fall back to one at a time when the batch filter misses a record
                try: t = _get(f"/catalog/tracks/{n}/", tok)
                except Exception: t = None
            if t:
                k = norm(t.get("key")); raw[x] = (t.get("key") or {}).get("name")
                if k: out[x] = k
        if i % 500 == 0: print(f"{i + len(chunk)} of {len(ids)} looked up, {len(out)} keys, {int(time.time() - t0)} s", flush=True)
        time.sleep(0.3)
    json.dump({"source": "Beatport catalogue key field", "fetched": len(out), "of": len(ids), "keys": out}, open("data/key-check.json", "w"))
    print(f"::notice title=key check::" + json.dumps({"looked_up": len(ids), "with_key": len(out), "examples": dict(list(raw.items())[:4])}))

if __name__ == "__main__": main()
