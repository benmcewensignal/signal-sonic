"""Beatport's facts for every record DJs chart (data/djcharts/charts.jsonl), written to data/chart-tracks.json as
{record: [label, [artists], bpm, camelot, preview, released, genre]}. Chart rows carry only record ids and genres; this
gives the DJ graph its labels and artists (who charts which labels and artists together), adoption speed its release
dates, and the corpus the preview links to measure charted records. Records already looked up are skipped, and the
queue is re-read each run, so it keeps up as the chart job adds charts."""
import argparse, json, os, time
from .beatport import get_token, _get

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--charts", default="data/djcharts/charts.jsonl"); ap.add_argument("--out", default="data/chart-tracks.json")
    ap.add_argument("--limit", type=int, default=3000); ap.add_argument("--budget-minutes", type=float, default=50); ap.add_argument("--sleep", type=float, default=0.2)
    a = ap.parse_args()
    ids = []; seen = set()
    for line in open(a.charts):
        try: r = json.loads(line)
        except Exception: continue
        for t in r.get("tracks") or []:
            if t and t not in seen: seen.add(t); ids.append(t)
    out = json.load(open(a.out)) if os.path.exists(a.out) else {}
    todo = [t for t in ids if t not in out]; token = get_token(); t0 = time.time(); got = err = 0
    for t in todo[:a.limit]:
        if (time.time() - t0) / 60 > a.budget_minutes: break
        try:
            d = _get(f"/catalog/tracks/{t.split(':')[-1]}/", token); k = d.get("key") or {}
            cam = (str(k.get("camelot_number") or "") + str(k.get("camelot_letter") or "")) or None
            out[t] = [(d.get("release") or {}).get("label", {}).get("name") if isinstance(d.get("release"), dict) else None,
                      [x.get("name") for x in (d.get("artists") or []) if isinstance(x, dict)], d.get("bpm"), cam, d.get("sample_url"),
                      d.get("new_release_date") or d.get("publish_date"), (d.get("genre") or {}).get("slug")]; got += 1
        except Exception:
            err += 1; out[t] = None
        time.sleep(a.sleep)
    json.dump(out, open(a.out, "w"), separators=(",", ":"))
    s = {"charted_records": len(ids), "looked_up_now": got, "errors": err, "left": len([t for t in ids if t not in out]), "total_known": len(out)}
    print(json.dumps(s)); print("::notice title=chart tracks::" + json.dumps(s))

if __name__ == "__main__":
    main()
