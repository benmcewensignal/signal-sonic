"""Beatport's own key and tempo for the records the DJ tool places (data/key-queue.json), written to
data/track-keys.json as {record: [camelot, key name, bpm]}. Sonic's own key reading is exactly right 60 times in 100;
Beatport's label replaces it for catalogue records, and becomes the test set for the reading an upload still needs."""
import argparse, json, os, time
from .beatport import get_token, _get

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--ids", default="data/key-queue.json"); ap.add_argument("--out", default="data/track-keys.json")
    ap.add_argument("--limit", type=int, default=3000); ap.add_argument("--budget-minutes", type=float, default=50); ap.add_argument("--sleep", type=float, default=0.2)
    a = ap.parse_args()
    ids = json.load(open(a.ids))["ids"]; out = json.load(open(a.out)) if os.path.exists(a.out) else {}
    todo = [t for t in ids if t not in out]; token = get_token(); t0 = time.time(); got = nokey = err = 0; samples = []
    for t in todo[:a.limit]:
        if (time.time() - t0) / 60 > a.budget_minutes: break
        try:
            d = _get(f"/catalog/tracks/{t.split(':')[-1]}/", token); k = d.get("key") or {}
            if len(samples) < 2: samples.append(json.dumps(k)[:200])
            cam = (str(k.get("camelot_number") or "") + str(k.get("camelot_letter") or "")) or None
            out[t] = [cam, k.get("name"), d.get("bpm")]; got += 1 if cam else 0; nokey += 0 if cam else 1
        except Exception as e:
            err += 1; out[t] = [None, None, None]
        time.sleep(a.sleep)
    json.dump(dict(sorted(out.items())), open(a.out, "w"), separators=(",", ":"))
    left = len([t for t in ids if t not in out])
    s = {"tried": min(len(todo), a.limit), "with_key": got, "no_key": nokey, "errors": err, "left": left, "total_known": len(out), "raw_key_sample": samples}
    print(json.dumps(s)); print("::notice title=keys::" + json.dumps(s))

if __name__ == "__main__":
    main()
