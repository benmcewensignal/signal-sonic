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
    return sorted(by.values(), key=lambda e: (str(e.get("started") or ""), str(e.get("file") or "")))


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
