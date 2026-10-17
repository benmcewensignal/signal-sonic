"""The next batch of DJs for the tracklist job. Usage: tracklist_batches.py [size] [since] [candidates file].
A DJ is due if they have no tracklist file, or their file does not yet reach back to `since` (re-reading adds the earlier
sets: the job rewrites the file with every set from `since`); each DJ is tried once per `since`, recorded in
data/tracklists-tried-<since>.json. Prints the batch, comma separated, for the workflow to dispatch.

From 17 Oct an active DJ is also due again once their file is REFRESH_DAYS old: the crawl finished on 11 Oct and nothing
read the sets published since. Active means a set within ACTIVE_DAYS of the last read (537 DJs had one from 2026 on 17
Oct); the file's read_at says when it was read, and a file without one (read before 17 Oct) counts as read on 11 Oct.
New DJs come first in a batch, then the oldest re-reads."""
import datetime, glob, json, os, re, sys, unicodedata
REFRESH_DAYS, ACTIVE_DAYS, FIRST_CRAWL_END = 45, 240, "2026-10-11"
def nm(s): return " ".join(re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()).split())
size = int(sys.argv[1]) if len(sys.argv) > 1 else 40
since = int(sys.argv[2]) if len(sys.argv) > 2 else 2024
cand = sys.argv[3] if len(sys.argv) > 3 else "data/tracklists-candidates.json"
C = json.load(open(cand)).get("djs", [])
reach, stale = {}, []
today = datetime.date.today()
for f in glob.glob("data/tracklists/*.json"):
    if f.endswith("curves.json"): continue
    try: d = json.load(open(f))
    except Exception: continue
    ys = [int(m.group(1)) for s in d.get("sets", []) for m in [re.match(r"(\d{4})-", str(s.get("title", "")))] if m]
    reach[nm(d.get("dj", ""))] = min(ys) if ys else 9999
    dates = sorted(m.group(1) for s in d.get("sets", []) for m in [re.match(r"(\d{4}-\d{2}-\d{2})", str(s.get("title", "")))] if m)
    try: read = datetime.date.fromisoformat(str(d.get("read_at") or FIRST_CRAWL_END))
    except ValueError: continue
    newest = None
    for ds in reversed(dates):          # the newest date that is a date (a title can say 2026-02-30)
        try: newest = datetime.date.fromisoformat(ds); break
        except ValueError: continue
    if newest and (today - read).days >= REFRESH_DAYS and (read - newest).days <= ACTIVE_DAYS:
        stale.append((read, d.get("dj")))
tf = f"data/tracklists-tried-{since}.json"
tried = json.load(open(tf)) if os.path.exists(tf) else []
T = {nm(x) for x in tried}
due = [d for d in C if nm(d) not in T and reach.get(nm(d), 9999) > since]
refresh = [dj for _, dj in sorted(stale, key=lambda x: x[0]) if dj and nm(dj) not in {nm(x) for x in due}]
due = due + refresh
batch = due[:size]
json.dump(sorted(set(tried) | set(batch)), open(tf, "w"), ensure_ascii=False, indent=0)
print(",".join(batch)); print(f"::notice title=tracklist batch::{len(batch)} DJs from {since} ({sum(1 for b in batch if b in refresh)} re-read); {len(due) - len(batch)} due after this, {len(refresh)} of them re-reads", file=sys.stderr)
