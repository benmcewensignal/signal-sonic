"""NTS tracklists: ground truth for the set layer.

Every NTS episode page carries a tracklist. We already scan NTS audio with the
fingerprinter; reading the tracklist alongside gives (a) a direct record of what was
played, with no audio matching at all, and (b) a way to score the fingerprint matcher
against something true rather than against the chronological null alone.

For each scanned NTS mix:
  - fetch its episode JSON (nts.live/api/v2/shows/<show>/episodes/<episode>)
  - take the tracklist (artist, title) pairs
  - match each pair to our named records by normalised artist and title
  - store every tracklist line in nts_tracklist, matched or not
Then compare: of the records the fingerprinter says it heard in that mix, how many
appear in the tracklist (precision), and of the tracklist records we hold, how many
did the fingerprinter find (recall).

  python -m sonic.nts_tracklists --db sonic.db --out data/nts-truth.json
"""
import argparse, collections, json, re, sqlite3, time, urllib.request, urllib.parse
from .artists import norm


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "signal-sonic/nts-tracklists", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def episode_api(url):
    """nts.live/shows/<show>/episodes/<ep> -> api/v2 path"""
    m = re.search(r"nts\.live/shows/([^/]+)/episodes/([^/?#]+)", url or "")
    if not m: return None
    return f"https://www.nts.live/api/v2/shows/{m.group(1)}/episodes/{m.group(2)}"


def norm_title(t):
    t = (t or "").lower()
    t = re.sub(r"\((original mix|extended mix|radio edit|club mix|edit|remaster(ed)?)\)", " ", t)
    t = re.sub(r"\[.*?\]", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/nts-truth.json")
    ap.add_argument("--limit", type=int, default=200); ap.add_argument("--sleep", type=float, default=0.6)
    a = ap.parse_args()
    c = sqlite3.connect(a.db); c.row_factory = sqlite3.Row
    c.execute("""create view if not exists usable_mixes as
        select * from mixes where error is null
          and (published is null or substr(published,1,10) >= '2024-08-01')""")
    c.execute("""create table if not exists nts_tracklist(
        mix_url text, position integer, artist text, title text, track_id text, fetched_at real,
        primary key (mix_url, position))""")
    c.commit()
    # index of our named records by (artist, title)
    idx = {}
    for r in c.execute("select track_id, name, artists from track_meta where artists is not null and name is not null"):
        try: arts = json.loads(r["artists"])
        except Exception: continue
        nt = norm_title(r["name"])
        for n in arts: idx[(norm(n), nt)] = r["track_id"]
    by_title = collections.defaultdict(list)
    for (ak, tk), tid in idx.items(): by_title[tk].append((ak, tid))
    mixes = [dict(r) for r in c.execute("select mix_url, title from usable_mixes where source='nts'")]
    have = {r[0] for r in c.execute("select distinct mix_url from nts_tracklist")}
    todo = [m for m in mixes if m["mix_url"] not in have][: a.limit]
    print(f"nts tracklists: {len(mixes)} scanned NTS mixes, {len(todo)} to fetch", flush=True)
    fetched = lines = matched = 0
    for m in todo:
        api = episode_api(m["mix_url"])
        if not api: continue
        try:
            ep = _get(api); tl = ep.get("tracklist") or []
        except Exception as e:
            print(f"  skip {m['mix_url'][-40:]}: {e!r}"[:100], flush=True); time.sleep(a.sleep); continue
        fetched += 1
        for i, t in enumerate(tl):
            art, title = (t.get("artist") or "").strip(), (t.get("title") or "").strip()
            tid = idx.get((norm(art), norm_title(title)))
            if not tid:                                    # title match with any of the credited artists
                for ak, cand in by_title.get(norm_title(title), []):
                    if ak and ak in norm(art) or norm(art) in ak: tid = cand; break
            if tid: matched += 1
            lines += 1
            c.execute("insert or replace into nts_tracklist values(?,?,?,?,?,?)", (m["mix_url"], i, art, title, tid, time.time()))
        c.commit(); time.sleep(a.sleep)
    # score the fingerprinter against the tracklists
    truth = collections.defaultdict(set)
    for r in c.execute("select mix_url, track_id from nts_tracklist where track_id is not null"): truth[r["mix_url"]].add(r["track_id"])
    heard = collections.defaultdict(set)
    for r in c.execute("select mix_url, track_id from mix_plays"): heard[r["mix_url"]].add(r["track_id"])
    tp = fp = fn = 0; per = []
    for u, T in truth.items():
        H = heard.get(u, set())
        tp += len(T & H); fp += len(H - T); fn += len(T - H)
        per.append({"mix": u[-50:], "truth": len(T), "heard": len(H), "agree": len(T & H)})
    prec = tp / (tp + fp) if tp + fp else None; rec = tp / (tp + fn) if tp + fn else None
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "nts_mixes_with_tracklist": len(truth), "tracklist_lines": lines, "lines_matched_to_our_records": matched,
           "fingerprint_vs_tracklist": {"true_positive": tp, "false_positive": fp, "false_negative": fn,
                                        "precision": round(prec, 2) if prec is not None else None,
                                        "recall": round(rec, 2) if rec is not None else None},
           "note": "precision: of records the fingerprinter heard, how many the tracklist confirms; recall: of tracklist records we hold, how many it found. Both limited by the fingerprint index covering only part of the corpus.",
           "per_mix": per[:50]}
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({k: v for k, v in out.items() if k != "per_mix"}, indent=1), flush=True)


if __name__ == "__main__":
    main()
