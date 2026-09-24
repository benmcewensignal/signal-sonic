"""Discogs' public-domain (CC0) release dump against the corpus: a second, independent opinion on
what each record is. Streams the monthly releases dump, keeps electronic releases from 2023 on,
matches tracks to corpus records by normalised artist and title (label as tie-break), and reports
how Discogs' styles line up with Beatport's tag and with the scene model's call where they differ.
  python tools/discogs.py sonic.db <dump.xml.gz url>
"""
import sys, re, json, gzip, sqlite3, collections, urllib.request
from lxml import etree
import os; os.makedirs("data/discogs", exist_ok=True)
db, url = sys.argv[1], sys.argv[2]
def norm(s): return " ".join(re.sub(r"\(.*?\)|\[.*?\]|[^a-z0-9 ]", " ", (s or "").lower()).split())
c = sqlite3.connect(db); want = {}; meta = {}
for t, name, arts, label in c.execute("select track_id, name, artists, label from track_meta"):
    try: a = (json.loads(arts) if arts and arts.startswith("[") else [arts])[0]
    except Exception: a = None
    if name and a: want.setdefault((norm(a), norm(name)), []).append(t); meta[t] = norm(label)
tag = {}
for t, s in c.execute("select track_id, scene from track_scenes where week like '____-M__' order by week"): tag.setdefault(t, s)
print(f"{len(want):,} artist-title keys to find", flush=True)
stream = gzip.GzipFile(fileobj=sys.stdin.buffer) if url == "-" else gzip.GzipFile(fileobj=urllib.request.urlopen(url, timeout=120))
found = {}; n = kept = 0
for _, el in etree.iterparse(stream, tag="release", huge_tree=True):
    n += 1
    g = [x.text for x in el.findall("genres/genre")]; styles = [x.text for x in el.findall("styles/style")]
    yr = (el.findtext("released") or "")[:4]
    if "Electronic" in g and styles and yr.isdigit() and int(yr) >= 2023:
        kept += 1; rel_art = [x.findtext("name") for x in el.findall("artists/artist")]; labels = [norm(x.get("name")) for x in el.findall("labels/label")]
        for tr in el.findall("tracklist/track"):
            ta = [x.findtext("name") for x in tr.findall("artists/artist")] or rel_art
            for a in ta[:2]:
                k = (norm(a), norm(tr.findtext("title")))
                for tid in want.get(k, []):
                    if tid not in found or meta.get(tid) in labels:
                        found[tid] = {"release": el.get("id"), "styles": styles, "released": el.findtext("released"), "label_match": meta.get(tid) in labels}
    el.clear()
    while el.getprevious() is not None: del el.getparent()[0]
    if n % 500000 == 0: print(f"  {n:,} releases read, {kept:,} electronic since 2023, {len(found):,} corpus records matched", flush=True)
with open("data/discogs/matches.jsonl", "w") as f:
    for t, r in found.items(): f.write(json.dumps({"track_id": t, **r}) + "\n")
by = collections.defaultdict(collections.Counter)
for t, r in found.items():
    if t in tag:
        for s in r["styles"]: by[tag[t]][s] += 1
summary = {"releases_read": n, "electronic_since_2023": kept, "matched": len(found), "with_tag": sum(1 for t in found if t in tag),
           "label_confirmed": sum(1 for r in found.values() if r["label_match"]), "styles_by_tag": {k: v.most_common(6) for k, v in by.items()}}
json.dump(summary, open("data/discogs/summary.json", "w"), indent=1)
msg = f"{n:,} releases read; {kept:,} electronic since 2023; {len(found):,} corpus records matched ({summary['label_confirmed']:,} with the label confirmed). Top styles for: " + "; ".join(f"{k}: " + ", ".join(s for s, _ in v.most_common(3)) for k, v in list(by.items())[:8])
print(msg); print(f"::notice title=Discogs::{msg[:1900]}")
