"""Run the pipeline's own chain against a throwaway corpus and check what came out.

Three faults in one day were code that parsed, imported, and never ran: a name that does not
exist in its scope, a block written below sys.exit(main()), a variable from another function.
Every one of them passed a syntax check and every one of them was invisible until someone used
the product. What they have in common is that nothing exercised the path end to end.

This does. It is not a unit test of anything; it runs the real shard and the real merge with
the network stubbed, and asserts on what reaches the database.

    python -m sonic.smoke --db sonic.db
"""
import argparse, json, os, shutil, sqlite3, sys, tempfile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        print(f"smoke: no database at {a.db}", flush=True)
        return 0

    work = tempfile.mkdtemp(prefix="smoke-")
    db = os.path.join(work, "sonic.db")
    out = os.path.join(work, "out")
    os.makedirs(out, exist_ok=True)
    shutil.copy(a.db, db)

    import sonic.beatport as B
    import sonic.reanalyse as R
    B.get_token = lambda: "smoke"
    R.preview_urls = lambda ids, tok, chunk=100: {t: "https://example.invalid/" + str(t) for t in ids}
    R._fetch_preview = lambda u: None                 # nothing decodes, which is fine

    fails = []

    # 1. a shard resolves previews and leaves its cache behind
    sys.argv = ["deepen_shard", "--shard", "0", "--of", "18", "--from", "2026-07", "--to", "2026-08",
                "--per-month", "2", "--db", db, "--out", out, "--budget-minutes", "1",
                "--restale", "2.9"]
    import sonic.deepen_shard as D
    try:
        D.main()
    except SystemExit:
        pass
    except Exception as e:
        fails.append(f"the shard raised {type(e).__name__}: {e}")
    side = os.path.join(out, "preview-cache-0.jsonl")
    n_side = sum(1 for _ in open(side)) if os.path.exists(side) else 0
    print(f"smoke: shard wrote {n_side} cached preview urls", flush=True)
    if n_side == 0:
        fails.append("the shard wrote no preview cache: it never reached the write, or resolved nothing")

    # 2. the merge folds that cache into the corpus
    open(os.path.join(out, "deepen-shard-0.jsonl"), "w").close()
    sys.argv = ["import_deepen", "--db", db, "--glob", os.path.join(out, "deepen-shard-*.jsonl"),
                "--report", os.path.join(work, "merge.json")]
    import sonic.import_deepen as I
    try:
        I.main()
    except SystemExit:
        pass
    except Exception as e:
        fails.append(f"the merge raised {type(e).__name__}: {e}")
    c = sqlite3.connect(db)
    try:
        n_rows = c.execute("select count(*) from preview_cache").fetchone()[0]
    except Exception:
        n_rows = -1
    print(f"smoke: merge folded {n_rows} urls into the corpus", flush=True)
    if n_rows <= 0:
        fails.append("the merge did not fold the preview cache: the table is missing or empty, "
                     "which means the fold never ran")

    # 3. a second shard reads them back rather than resolving again
    looked_up = {"n": 0}
    real = R.preview_urls
    def counting(ids, tok, chunk=100):
        looked_up["n"] += len(ids)
        return real(ids, tok, chunk)
    R.preview_urls = counting
    sys.argv = ["deepen_shard", "--shard", "0", "--of", "18", "--from", "2026-07", "--to", "2026-08",
                "--per-month", "2", "--db", db, "--out", out, "--budget-minutes", "1",
                "--restale", "2.9"]
    try:
        D.main()
    except SystemExit:
        pass
    except Exception as e:
        fails.append(f"the second shard raised {type(e).__name__}: {e}")
    print(f"smoke: second pass looked up {looked_up['n']} urls afresh", flush=True)
    if n_side and looked_up["n"] >= n_side:
        fails.append(f"the cache is not being read: the second pass looked up {looked_up['n']} "
                     f"of {n_side} it already had")

    shutil.rmtree(work, ignore_errors=True)
    for f in fails:
        print(f"::error::smoke: {f}", flush=True)
    print("smoke: " + ("ok" if not fails else f"{len(fails)} problem(s)"), flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
