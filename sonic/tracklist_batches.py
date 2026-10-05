"""The next batch of DJs for the tracklist job. Usage: tracklist_batches.py [size] [since] [candidates file].
A DJ is due if they have no tracklist file, or their file does not yet reach back to `since` (re-reading adds the earlier
sets: the job rewrites the file with every set from `since`); each DJ is tried once per `since`, recorded in
data/tracklists-tried-<since>.json. Prints the batch, comma separated, for the workflow to dispatch."""
import glob, json, os, re, sys, unicodedata
def nm(s): return " ".join(re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()).split())
size = int(sys.argv[1]) if len(sys.argv) > 1 else 40
since = int(sys.argv[2]) if len(sys.argv) > 2 else 2024
cand = sys.argv[3] if len(sys.argv) > 3 else "data/tracklists-candidates.json"
C = json.load(open(cand)).get("djs", [])
reach = {}
for f in glob.glob("data/tracklists/*.json"):
    if f.endswith("curves.json"): continue
    try: d = json.load(open(f))
    except Exception: continue
    ys = [int(m.group(1)) for s in d.get("sets", []) for m in [re.match(r"(\d{4})-", str(s.get("title", "")))] if m]
    reach[nm(d.get("dj", ""))] = min(ys) if ys else 9999
tf = f"data/tracklists-tried-{since}.json"
tried = json.load(open(tf)) if os.path.exists(tf) else []
T = {nm(x) for x in tried}
due = [d for d in C if nm(d) not in T and reach.get(nm(d), 9999) > since]
batch = due[:size]
json.dump(sorted(set(tried) | set(batch)), open(tf, "w"), ensure_ascii=False, indent=0)
print(",".join(batch)); print(f"::notice title=tracklist batch::{len(batch)} DJs from {since}; {len(due) - len(batch)} due after this", file=sys.stderr)
