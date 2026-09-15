"""Did the records change, or did the previews?

Twenty-two of forty-five numbers move the same way across every scene, ten of them
unanimously. That is either a real production trend or Beatport re-encoding its previews over
the period, and the two look identical in the data. If it is encoding, every drift finding we
publish is an artefact.

The test is direct. Take records first measured months ago, fetch their preview again today,
measure it again with the same analyser, and compare. A record cannot have changed. If the
numbers have, the change is in the file rather than in the music, and the size of that change
puts a floor under every drift we claim.

    python -m sonic.encoding_check --db sonic.db --n 300

Reports the median shift per feature block, and how it compares to the drift we report.
"""
import argparse, json, sqlite3, sys, tempfile, os, time
import numpy as np

BLOCKS = {"timbre": range(0, 13), "movement": range(13, 26),
          "harmony": range(26, 38), "texture": range(38, 45)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--out", default="out/encoding-check.json")
    a = ap.parse_args()

    from .analyser import get_analyser
    from .backfill import _fetch_preview
    from .beatport import get_token
    from .reanalyse import preview_urls

    an = get_analyser("local")
    token = get_token()
    c = sqlite3.connect(a.db)
    c.row_factory = sqlite3.Row

    # spread the sample across the window: an encoding change has a date, and a sample
    # bunched in one month cannot see it
    # only records already measured by the analyser we are about to run. Comparing a record
    # stored at version two against a fresh reading at two point seven would mostly measure
    # the analyser upgrade, which is exactly the confound this test exists to avoid.
    want = str(an.version).split("+")[0]
    rows = [r for r in c.execute("""
        select ts.week, t.track_id, t.features, t.analyser_ver
          from track_scenes ts join tracks t on t.track_id = ts.track_id
         where t.analyser_id='local' and t.features is not null
           and ts.week like '____-M__'
         group by t.track_id""")
            if str(r["analyser_ver"] or "").split("+")[0] == want]
    if not rows:
        print(f"no records stored at {want}: the re-measure has to reach a scene before its "
              f"records can be checked for encoding drift")
        return 1
    print(f"comparing only records already at {want}: {len(rows)} available", flush=True)
    by_month = {}
    for r in rows:
        by_month.setdefault(r["week"], []).append(r)
    months = sorted(by_month)
    if not months:
        print("no dated records"); return 1
    per = max(1, a.n // len(months))
    rng = np.random.default_rng(7)
    sample = []
    for m in months:
        pool = by_month[m]
        pick = rng.choice(len(pool), min(per, len(pool)), replace=False)
        sample += [pool[i] for i in pick]
    print(f"{len(sample)} records across {len(months)} months", flush=True)

    urls = preview_urls([r["track_id"] for r in sample], token)
    out, t0 = [], time.time()
    for r in sample:
        if time.time() - t0 > 50 * 60:
            print("budget reached", flush=True); break
        u = urls.get(r["track_id"])
        if not u:
            continue
        local = None
        try:
            local = _fetch_preview(u)
            if not local:
                continue
            fresh = json.loads(an.analyse(local).to_json())
            old = json.loads(r["features"])
            a_emb, b_emb = old.get("embedding"), fresh.get("embedding")
            if not a_emb or not b_emb or len(a_emb) != len(b_emb):
                continue
            d = np.abs(np.array(b_emb) - np.array(a_emb))
            out.append({"week": r["week"], "ver": r["analyser_ver"],
                        "blocks": {k: float(np.mean(d[list(ix)])) for k, ix in BLOCKS.items()},
                        "total": float(np.mean(d))})
        except Exception:
            continue
        finally:
            if local and os.path.exists(local):
                try: os.unlink(local)
                except Exception: pass
        if len(out) % 25 == 0 and out:
            print(f"  {len(out)} re-measured", flush=True)

    if not out:
        print("nothing re-measured"); return 1
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w"))
    tot = np.array([x["total"] for x in out])
    print(f"\n{len(out)} records re-measured with the same analyser")
    print(f"  median change in a record that cannot have changed: {np.median(tot):.5f}")
    for k in BLOCKS:
        v = np.array([x["blocks"][k] for x in out])
        print(f"    {k:10} {np.median(v):.5f}")
    # does the change depend on how old the first measurement is?
    early = [x["total"] for x in out if x["week"] < "2025-M09"]
    late = [x["total"] for x in out if x["week"] >= "2025-M09"]
    if len(early) >= 20 and len(late) >= 20:
        print(f"\n  first measured before Sep 2025: {np.median(early):.5f}")
        print(f"  first measured after:            {np.median(late):.5f}")
        print("  an encoding change would make the older ones differ more.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
