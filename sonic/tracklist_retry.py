"""A second try at the tracklist lines Beatport's search did not match the first time (data/tracklists/<dj>.json).
The first try searched the artist string as written with the title; a line with several artists, a "feat." or a
bracketed credit often found nothing. This tries cleaner queries, the first artist alone and the title without its
feature credit, and keeps the resolver's rules (sonic.tracklists.match): title and artist must agree, and a line that
names a remix must match that remix. Each distinct artist and title is tried once; the answer, match or not, is
written to every line that carries it, so no line is tried again. Matched lines are measured and brought into the
corpus by the tracklist workflow's measuring step, which this job starts when every line has been tried."""
import argparse, glob, json, re, time
from sonic import beatport as B
from sonic.tracklists import match

def first_artist(s):
    return re.split(r",|&| x | feat\.? | ft\.? | vs\.? | and | with ", str(s or ""), flags=re.I)[0].strip()

def clean_title(s):
    s = re.sub(r"\s*\((feat|ft|featuring)\.? [^)]*\)", "", str(s or ""), flags=re.I)
    return re.sub(r"\s+(feat|ft|featuring)\.? .*$", "", s, flags=re.I).strip()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=2500); ap.add_argument("--budget-minutes", type=float, default=50)
    a = ap.parse_args()
    files = [f for f in sorted(glob.glob("data/tracklists/*.json")) if not f.endswith("curves.json")]
    D = {f: json.load(open(f)) for f in files}; lines = {}
    for f, d in D.items():
        for st in d.get("sets", []):
            for r in st.get("records", []):
                if r.get("bp") or r.get("retry") or not r.get("artist") or not r.get("title") or str(r.get("artist")) == "None": continue
                lines.setdefault((str(r["artist"]).lower(), str(r["title"]).lower()), []).append((f, r))
    keys = list(lines); tok = B.get_token(); cache = {}; t0 = time.time(); tried = got = 0; changed = set()
    for k in keys[:a.limit]:
        if (time.time() - t0) / 60 > a.budget_minutes: break
        f0, r0 = lines[k][0]; res = None
        for rec in ({**r0, "artist": first_artist(r0["artist"]), "title": clean_title(r0["title"])},
                    {**r0, "title": clean_title(r0["title"])}):
            if (rec["artist"], rec["title"]) == (r0["artist"], r0["title"]) and rec is not None and res is None and tried == -1: continue
            res = match(tok, rec, cache); time.sleep(0.15)
            if res: break
        tried += 1; got += bool(res)
        for f, r in lines[k]:
            if res: r.update(res); r["via"] = "retry"
            else: r["retry"] = 1
            changed.add(f)
    for f in changed: json.dump(D[f], open(f, "w"), ensure_ascii=False, indent=1)
    open("/tmp/retry-changed.txt", "w").write("\n".join(sorted(changed)))
    left = len(keys) - tried
    s = {"distinct_entries_tried": tried, "matched": got, "left": left, "files_changed": len(changed)}
    print(json.dumps(s)); print("::notice title=tracklist retry::" + json.dumps(s))

if __name__ == "__main__":
    main()
