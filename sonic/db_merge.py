"""Save a run's database without erasing anyone else's: a three-way merge, row by row.

Every run used to save by copying its whole database over the shared one, so two runs that
overlapped erased each other: the last to finish won, and the other's rows were gone. The
database also lived in git, which refuses any file over 100 MB; it reached 25,553 full pages,
192 KB short, on 16 September, and from then every run that changed it had its whole save
rejected while the step reported success. It now lives in the 'deepened' release, and each save
applies only what this run changed since it fetched the database (ours minus base) to whatever
the release holds now (theirs).

    python -m sonic.db_merge --base base.db --ours sonic.db --theirs now.db --out merged.db

Rows this run added or changed are written over theirs (INSERT OR REPLACE on tables with a
primary key); rows this run deleted are deleted; rows only the other side touched are kept.
Where both changed the same row, this run's version wins. Tables without a primary key only
gain rows: a deletion there cannot be told from a row the other side added, so it is left.
"""
import argparse, os, shutil, sqlite3


def _tables(c, schema):
    return {n: sql for n, sql in c.execute(
        f"SELECT name, sql FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _cols(c, schema, t):
    return [(r[1], r[5]) for r in c.execute(f"PRAGMA {schema}.table_info('{t}')")]


def merge(base, ours, theirs, out):
    if os.path.abspath(out) != os.path.abspath(theirs):
        shutil.copy(theirs, out)
    c = sqlite3.connect(out)
    c.execute("ATTACH DATABASE ? AS b", (base,))
    c.execute("ATTACH DATABASE ? AS o", (ours,))
    tm, tb, to = _tables(c, "main"), _tables(c, "b"), _tables(c, "o")
    report = []
    with c:
        for t, sql in to.items():
            q = f'"{t}"'
            if t not in tm:
                c.execute(sql)
                n = c.execute(f"INSERT INTO main.{q} SELECT * FROM o.{q}").rowcount
                report.append(f"{t}: new table, {n} rows")
                continue
            main_cols = _cols(c, "main", t)
            have = {n for n, _ in main_cols}
            cols = [n for n, _ in _cols(c, "o", t) if n in have]
            if not cols:
                continue
            cl = ",".join(f'"{x}"' for x in cols)
            pk = [n for n, p in sorted(main_cols, key=lambda x: x[1]) if p > 0]
            base_cols = {n for n, _ in _cols(c, "b", t)} if t in tb else set()
            if t in tb and set(cols) <= base_cols:
                changed = f"SELECT {cl} FROM o.{q} EXCEPT SELECT {cl} FROM b.{q}"
            else:
                changed = f"SELECT {cl} FROM o.{q}"
            if pk:
                n = c.execute(f"INSERT OR REPLACE INTO main.{q} ({cl}) {changed}").rowcount
                d = 0
                if t in tb and set(pk) <= base_cols:
                    pl = ",".join(f'"{x}"' for x in pk)
                    d = c.execute(f"DELETE FROM main.{q} WHERE ({pl}) IN "
                                  f"(SELECT {pl} FROM b.{q} EXCEPT SELECT {pl} FROM o.{q})").rowcount
                if n or d:
                    report.append(f"{t}: {n} written, {d} deleted")
            else:
                n = c.execute(f"INSERT INTO main.{q} ({cl}) SELECT * FROM ({changed}) "
                              f"EXCEPT SELECT {cl} FROM main.{q}").rowcount
                if n:
                    report.append(f"{t}: {n} added (no primary key)")
    c.execute("DETACH DATABASE b")
    c.execute("DETACH DATABASE o")
    c.close()
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--ours", required=True)
    ap.add_argument("--theirs", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rep = merge(a.base, a.ours, a.theirs, a.out)
    print("merged: " + ("; ".join(rep) if rep else "nothing of this run's to apply"))


if __name__ == "__main__":
    main()
