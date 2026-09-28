"""Classics into the corpus database. Records Listen recognises from the classics lists (the canon, the history pass,
and the records DJs play most that Sonic could not recognise) are measured on the corpus analyser and stored with
source 'classic', so separation picks them up and every tool that needs a record's parts (its key, its loops, the
part-by-part comparison) works for them. They get no scene-month, so no scene's this-year statistics change.
  python -m sonic.classics --db sonic.db --limit 250"""
import argparse, json, sqlite3, time, urllib.request
from sonic import beatport as B

LISTS = ["https://raw.githubusercontent.com/benmcewensignal/signal-sonic-audio/main/out/fp-canon.jsonl",
         "https://raw.githubusercontent.com/benmcewensignal/signal-sonic-audio/main/out/fp-history.jsonl"]


def classics():
    rows = {}
    for u in LISTS:
        try:
            with urllib.request.urlopen(u, timeout=60) as r:
                for ln in r.read().decode().splitlines():
                    try: x = json.loads(ln)
                    except Exception: continue
                    t = str(x.get("track_id") or "")
                    if t.startswith("bp:") and x.get("found") is not False: rows.setdefault(t, x)
        except Exception as e:
            print(f"list not read ({u.rsplit('/', 1)[-1]}): {type(e).__name__}", flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--db", default="sonic.db"); ap.add_argument("--limit", type=int, default=250); a = ap.parse_args()
    c = sqlite3.connect(a.db)
    have = {r[0] for r in c.execute("select track_id from tracks where analyser_id='local'")}
    todo = [(t, x) for t, x in classics().items() if t not in have]
    from sonic.analyser_local import LocalAnalyser
    A = LocalAnalyser(); token = B.get_token(); now = time.strftime("%Y-%m-%d"); n_ok = n_fail = 0
    for t, x in todo[:a.limit]:
        try:
            time.sleep(0.2)
            tr = B._get(f"/catalog/tracks/{t[3:]}/", token) or {}
            url = x.get("preview") or tr.get("sample_url")
            if not url: n_fail += 1; continue
            fv = A.analyse(B.download_preview(url)); d = fv.__dict__ if hasattr(fv, "__dict__") else dict(fv)
            c.execute("insert or ignore into tracks (track_id, analyser_id, analyser_ver, features, source, first_seen, created_at) values (?,?,?,?,?,?,?)",
                      (t, "local", A.version, json.dumps(d, default=float), "classic", now, time.time()))
            arts = [ar.get("name") for ar in (tr.get("artists") or []) if isinstance(ar, dict)] or x.get("artists") or []
            c.execute("insert or ignore into track_meta (track_id, name, mix, artists, label, released, fetched_at) values (?,?,?,?,?,?,?)",
                      (t, tr.get("name") or x.get("name"), tr.get("mix_name"), json.dumps(arts), ((tr.get("release") or {}).get("label") or {}).get("name"),
                       tr.get("publish_date") or tr.get("new_release_date"), now))
            c.execute("insert or ignore into preview_cache (track_id, url, resolved_at) values (?,?,?)", (t, url, now))
            n_ok += 1
            if n_ok % 25 == 0: c.commit()
        except Exception as e:
            n_fail += 1; print(f"classic {t}: {type(e).__name__}", flush=True)
    c.commit()
    left = max(0, len(todo) - a.limit); open("/tmp/classics_left.txt", "w").write(str(left))
    msg = f"classics: {len(todo)} not yet in the corpus, {n_ok} measured and added, {n_fail} failed, {left} left for the next run"
    print(msg, flush=True); print(f"::notice title=classics::{msg}", flush=True)


if __name__ == "__main__":
    main()
