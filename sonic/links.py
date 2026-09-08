"""Where to hear a record, and where it was first heard.

Momentum figures without a way to listen are half a product. This builds, for every
record we have matched inside a DJ set, the links a person would actually use:

  first_heard   the earliest set in the corpus containing it, deep-linked to the
                timestamp where it starts, with the mix title and date
  beatport      the record itself
  bandcamp / soundcloud / youtube   searches, since we cannot guarantee a canonical url

The first of those is the one nobody else has: not "here is a record" but "here is the
set where a DJ played it, at 42:10, three months before it charted".

  python -m sonic.links --db sonic.db --out data/links.json
"""
import argparse, collections, datetime as dt, json, sqlite3, urllib.parse

CORPUS_START = dt.date(2024, 8, 1)


def _date(x):
    try:
        if isinstance(x, (int, float)) or str(x).isdigit(): return dt.datetime.utcfromtimestamp(float(x)).date()
        return dt.date.fromisoformat(str(x)[:10])
    except Exception: return None


def build(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    usable = {r["mix_url"]: dict(r) for r in c.execute(
        "select mix_url, title, source, published from mixes where error is null")
        if not (_date(r["published"]) and _date(r["published"]) < CORPUS_START)}
    meta = {r["track_id"]: (r["name"], json.loads(r["artists"]) if r["artists"] else [], r["label"])
            for r in c.execute("select track_id, name, artists, label from track_meta where name is not null")}
    plays = collections.defaultdict(list)
    for r in c.execute("select mix_url, track_id, offset_s from mix_plays"):
        m = usable.get(r["mix_url"])
        if m: plays[r["track_id"]].append((m.get("published") or "", r["mix_url"], m.get("title") or "", m.get("source"), r["offset_s"]))
    out = {}
    for tid, ps in plays.items():
        if tid not in meta: continue
        name, arts, label = meta[tid]
        q = urllib.parse.quote(f"{' '.join(arts[:2])} {name}".strip())
        rec = {"beatport": f"https://www.beatport.com/track/x/{tid.split(':')[-1]}" if tid.startswith("bp:") else None,
               "bandcamp": f"https://bandcamp.com/search?q={q}",
               "soundcloud": f"https://soundcloud.com/search?q={q}"}
        pub, url, title, src, off = sorted(ps)[0]
        t = int(off or 0)
        deep = url + (f"#t={t//60:02d}:{t%60:02d}" if src == "mixcloud" else (f"&t={t}" if "youtube" in url else ""))
        rec["first_heard"] = {"mix": title[:70], "source": src, "published": pub,
                              "at": f"{t//60}:{t%60:02d}", "url": deep, "n_sets": len(ps)}
        out[tid] = rec
    return {"generated": dt.datetime.now(dt.timezone.utc).isoformat()[:19] + "Z",
            "note": "first_heard is the earliest set in the corpus containing the record, deep-linked to where it starts",
            "records": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/links.json")
    a = ap.parse_args()
    d = build(a.db)
    json.dump(d, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({"records": len(d["records"]),
                      "with a set appearance": sum(1 for v in d["records"].values() if v.get("first_heard"))}, indent=1))


if __name__ == "__main__":
    main()
