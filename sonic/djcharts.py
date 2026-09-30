"""Beatport DJ charts: curated lists DJs publish of what they are playing.

A survey, not an import: how many DJs and records the charts would add to the DJ layer, how they
overlap with the corpus and with the DJs we hold from tracklists, and which scenes they cover.
Charts are a DJ's public selection, not a set as played: usable for 'who would play it', not for
set curves. Writes data/djcharts/charts.jsonl and a summary.
  python -m sonic.djcharts --db sonic.db --max-charts 1500
"""
import argparse, json, os, sqlite3, time, collections, glob
from . import beatport as B


def people(ch):
    """The chart's owner, whichever field the API uses."""
    for k in ("person", "artist", "owner", "user"):
        v = ch.get(k)
        if isinstance(v, dict) and (v.get("name") or v.get("owner_name")):
            return str(v.get("id") or ""), v.get("name") or v.get("owner_name")
    a = ch.get("artists") or []
    if a and isinstance(a[0], dict): return str(a[0].get("id") or ""), a[0].get("name")
    return "", None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--db", default="sonic.db"); ap.add_argument("--max-charts", type=int, default=1500)
    ap.add_argument("--since", default="2024-01-01")
    ap.add_argument("--resume", action="store_true", help="keep charts already read (data/djcharts/charts.jsonl), read only new ones, within --budget-minutes")
    ap.add_argument("--budget-minutes", type=float, default=0); a = ap.parse_args()
    done_ids = set()
    if a.resume and os.path.exists("data/djcharts/charts.jsonl"):
        for line in open("data/djcharts/charts.jsonl"):
            try: done_ids.add(json.loads(line).get("chart"))
            except Exception: pass
    tok = B.get_token(); os.makedirs("data/djcharts", exist_ok=True)
    # Beatport's listing stops at the most recent 10,000 charts (about three months), so charts are listed month by month
    # with a publish_date window, each well under that cap. If a window's results fall outside it, the filter is not
    # honoured and the job says so rather than re-reading the newest 10,000.
    import datetime as _dt
    def months(since):
        d = _dt.date.fromisoformat(since[:10]).replace(day=1); end = _dt.date.today()
        while d <= end:
            nx = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1); yield d.isoformat(), (min(nx - _dt.timedelta(days=1), end)).isoformat(); d = nx
    charts, sample, seen_ids, outside = [], None, set(), 0
    for m0, m1 in reversed(list(months(a.since))):
        page = f"/catalog/charts/?per_page=100&order_by=-publish_date&publish_date={m0}:{m1}"; got = 0
        while page and len(charts) < a.max_charts:
            try: d = B._get(page, tok)
            except Exception as e: print(f"::warning::charts page failed in {m0[:7]} after {got}: {type(e).__name__} {e}", flush=True); break
            for ch in d.get("results", []):
                sample = sample or ch; date = (ch.get("publish_date") or ch.get("change_date") or "")[:10]
                if date and not (m0 <= date <= m1): outside += 1; continue
                if ch.get("id") in seen_ids: continue
                seen_ids.add(ch.get("id")); charts.append(ch); got += 1
            nxt = d.get("next")   # the API's next link carries its own /v4 prefix
            page = ("/" + nxt.split("/v4/", 1)[1]) if nxt and "/v4/" in nxt else nxt
        print(f"  {m0[:7]}: {got} charts", flush=True)
        if outside > 500 and got == 0:
            print("::warning title=djcharts::the publish_date window is not honoured by the charts listing; stopping at the most recent charts", flush=True); break
    print(f"charts listed: {len(charts)}; sample fields: {sorted((sample or {}).keys())[:24]}", flush=True)
    t0 = time.time(); todo = [ch for ch in charts if ch.get("id") not in done_ids]
    out = open("data/djcharts/charts.jsonl", "a" if a.resume else "w"); rows = []; read_now = 0
    for i, ch in enumerate(todo if a.resume else charts):
        if a.budget_minutes and (time.time() - t0) / 60 > a.budget_minutes: break
        read_now += 1
        pid, pname = people(ch)
        try:
            t = B._get(f"/catalog/charts/{ch['id']}/tracks/", tok, {"per_page": 100})
            tracks = [(r.get("id"), ((r.get("genre") or {}).get("slug"))) for r in t.get("results", [])]
        except Exception as e:
            tracks = []
        row = {"chart": ch.get("id"), "name": ch.get("name"), "dj_id": pid, "dj": pname, "date": (ch.get("publish_date") or "")[:10],
               "tracks": [f"bp:{x}" for x, _ in tracks if x], "genres": [g for _, g in tracks]}
        rows.append(row); out.write(json.dumps(row) + "\n")
        if i % 200 == 0: print(f"  {i} charts read", flush=True)
        time.sleep(0.15)
    out.close()
    if a.resume:
        left = len(todo) - read_now; listed_oldest = min(((ch.get("publish_date") or "")[:10] for ch in charts), default="")
        s_ = {"listed": len(charts), "already_read": len(done_ids), "read_now": read_now, "left": left, "oldest_listed": listed_oldest}
        print(json.dumps(s_)); print("::notice title=djcharts::" + json.dumps(s_))
        rows = []
        for line in open("data/djcharts/charts.jsonl"):
            try: rows.append(json.loads(line))
            except Exception: pass
    c = sqlite3.connect(a.db); corpus = {r[0] for r in c.execute("select track_id from tracks")}
    have_djs = set()
    for f in glob.glob("data/tracklists/*.json"):
        if f.endswith("curves.json"): continue
        try: have_djs.add(json.load(open(f))["dj"].lower())
        except Exception: pass
    djs = collections.Counter(r["dj"] for r in rows if r["dj"]); tr = [t for r in rows for t in r["tracks"]]; uniq = set(tr)
    gen = collections.Counter(g for r in rows for g in r["genres"] if g)
    per_dj = collections.Counter()
    for r in rows:
        if r["dj"]: per_dj[r["dj"]] += len(r["tracks"])
    rich = sum(1 for d, n in per_dj.items() if n >= 30)
    known = sum(1 for d in djs if d.lower() in have_djs)
    summary = {"charts": len(rows), "djs": len(djs), "djs_with_30_plus_tracks": rich, "djs_already_from_tracklists": known,
               "track_entries": len(tr), "distinct_tracks": len(uniq), "in_corpus": len(uniq & corpus),
               "genres": gen.most_common(25), "sample_fields": sorted((sample or {}).keys())}
    json.dump(summary, open("data/djcharts/summary.json", "w"), indent=1)
    msg = (f"{len(rows)} charts since {a.since} by {len(djs)} DJs ({rich} with 30+ charted records; {known} already held from tracklists); "
           f"{len(uniq):,} distinct records, {len(uniq & corpus):,} already in the corpus. Top genres: " + ", ".join(f"{g} {n}" for g, n in gen.most_common(10)))
    print(msg); print(f"::notice title=Beatport DJ charts::{msg}")


if __name__ == "__main__":
    main()
