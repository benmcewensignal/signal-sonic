"""Recover preview addresses the deepen passes resolved but never kept (their cache files died with the
runners). Tries Beatport's batch lookup, a hundred ids per request, and falls back to one per track.
  python tools/recover_previews.py sonic.db <shard> <of> out.jsonl
"""
import sys, json, sqlite3, time
sys.path.insert(0, ".")
from sonic.beatport import get_token, _get
db, shard, of, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
c = sqlite3.connect(db)
have = {r[0] for r in c.execute("select track_id from preview_cache where url is not null and url != ''")}
todo = sorted(t for (t,) in c.execute("select track_id from tracks where analyser_id='local'") if t.startswith("bp:") and t not in have)
todo = todo[shard::of]; tok = get_token(); got = {}; batch_ok = None; fails = 0
def sample(tr): return tr.get("sample_url") or ((tr.get("preview") or {}).get("mp3") or {}).get("url") or ""
print(f"shard {shard}/{of}: {len(todo)} ids to recover", flush=True)
for i in range(0, len(todo), 100):
    ids = [t[3:] for t in todo[i:i + 100]]
    if batch_ok is not False:
        try:
            d = _get("/catalog/tracks/", tok, {"id": ",".join(ids), "per_page": 100})
            res = {str(x.get("id")): sample(x) for x in d.get("results", [])}
            batch_ok = bool(res) and any(k in res for k in ids)
            if batch_ok:
                got.update({"bp:" + k: u for k, u in res.items() if u and k in ids}); time.sleep(0.4); continue
        except Exception as e:
            print("batch lookup failed:", type(e).__name__, str(e)[:120], flush=True); batch_ok = False
    for k in ids:   # one at a time
        for attempt in range(3):
            try:
                u = sample(_get(f"/catalog/tracks/{k}/", tok))
                if u: got["bp:" + k] = u
                break
            except Exception as e:
                if "401" in str(e): tok = get_token()
                if attempt == 2: fails += 1
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.25)
    if i % 2000 == 0: print(f"  {i + len(ids)}/{len(todo)} checked, {len(got)} recovered", flush=True)
with open(out, "w") as f:
    for t, u in got.items(): f.write(json.dumps({"track_id": t, "url": u}) + "\n")
msg = f"shard {shard}: recovered {len(got)} of {len(todo)} ({'batch' if batch_ok else 'one at a time'}), {fails} failed"
print(msg); print(f"::notice title=recovered::{msg}")
