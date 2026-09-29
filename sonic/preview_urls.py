"""Beatport preview links for a list of records (the listening check's sample, data/listening-sample.json), written
to data/listening-previews.json as {record: link}. Uses the measuring step's own lookup (reanalyse._preview_url)."""
import argparse, json, os, time
from .beatport import get_token
from .reanalyse import _preview_url

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--ids", default="data/listening-sample.json"); ap.add_argument("--out", default="data/listening-previews.json")
    ap.add_argument("--budget-minutes", type=float, default=40); ap.add_argument("--sleep", type=float, default=0.25)
    a = ap.parse_args()
    ids = json.load(open(a.ids))["ids"]; out = json.load(open(a.out)) if os.path.exists(a.out) else {}
    todo = [t for t in ids if t not in out]; token = get_token(); t0 = time.time(); got = miss = 0
    for t in todo:
        if (time.time() - t0) / 60 > a.budget_minutes: break
        u = _preview_url(t, token)
        if u: out[t] = u; got += 1
        else: miss += 1
        time.sleep(a.sleep)
    json.dump(dict(sorted(out.items())), open(a.out, "w"), separators=(",", ":"))
    s = {"asked": len(todo), "found": got, "no_preview": miss, "total_known": len(out)}
    print(json.dumps(s)); print("::notice title=previews::" + json.dumps(s))

if __name__ == "__main__":
    main()
