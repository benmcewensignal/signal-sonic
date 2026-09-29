"""Merge two copies of queue/done.json instead of letting the later save overwrite the earlier.

Every run saved the queue by copying its folder over main, so a run that started before another
finished wrote back its older done log: the commits of 19 to 21 September each deleted lines from
it. Now the save takes the union of main's entries and this run's, keyed on file and start time,
in start order. An entry that changed (a start marked done later) keeps the fuller version.

    python -m sonic.done_merge --main queue/done.json --ours /tmp/out/queue/done.json --out queue/done.json
"""
import argparse, json


def merge(main_entries, our_entries):
    by = {}
    for e in list(main_entries) + list(our_entries):
        if not isinstance(e, dict):
            continue
        k = (e.get("file"), e.get("started"))
        if k not in by or len(json.dumps(e)) > len(json.dumps(by[k])):
            by[k] = e
    # an attempt is claimed on main at the start of a job and removed from the run's copy when the job ends; the union
    # brought main's claim back every time, so each run added a permanent attempt and a job taking several bites was
    # parked after three however well it went. Drop an attempt once the same job has a later finished or requeued entry.
    ended = {}
    for e in by.values():
        f = str(e.get("file") or "")
        if not f.endswith("#attempt") and not f.endswith("#reset") and (e.get("finished") or e.get("requeued")):
            ended[f] = max(ended.get(f, ""), str(e.get("finished") or e.get("started") or ""))
    keep = [e for e in by.values() if not (str(e.get("file") or "").endswith("#attempt")
            and str(e.get("file"))[:-8] in ended and ended[str(e.get("file"))[:-8]] >= str(e.get("started") or ""))]
    return sorted(keep, key=lambda e: (str(e.get("started") or ""), str(e.get("file") or "")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", required=True); ap.add_argument("--ours", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    load = lambda p: (json.load(open(p)) if p else [])
    try: m = load(a.main)
    except Exception: m = []
    try: o = load(a.ours)
    except Exception: o = []
    out = merge(m, o)
    json.dump(out, open(a.out, "w"), indent=1)
    print(f"done log: {len(m)} on main, {len(o)} in this run, {len(out)} merged")


if __name__ == "__main__":
    main()
