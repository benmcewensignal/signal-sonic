"""The next batch of DJs for the tracklist job: the next 40 from data/tracklists-candidates.json (DJs who chart and are
artists Signal knows, most charts first) that have no tracklist file and have not been tried, recorded in
data/tracklists-tried.json so none is tried twice. Prints the batch, comma separated, for the workflow to dispatch."""
import glob, json, os, re, sys, unicodedata
def nm(s): return " ".join(re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()).split())
C = json.load(open("data/tracklists-candidates.json")).get("djs", [])
have = {nm(json.load(open(f)).get("dj", "")) for f in glob.glob("data/tracklists/*.json") if not f.endswith("curves.json")}
tried = json.load(open("data/tracklists-tried.json")) if os.path.exists("data/tracklists-tried.json") else []
T = {nm(x) for x in tried}; batch = [d for d in C if nm(d) not in have and nm(d) not in T][:int(sys.argv[1]) if len(sys.argv) > 1 else 40]
json.dump(sorted(set(tried) | set(batch)), open("data/tracklists-tried.json", "w"), ensure_ascii=False, indent=0)
print(",".join(batch)); print(f"::notice title=tracklist batch::{len(batch)} DJs; {len([d for d in C if nm(d) not in have and nm(d) not in T]) - len(batch)} candidates left", file=sys.stderr)
