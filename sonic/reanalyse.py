"""Re-analyse tracks whose features came from an older analyser version.

v1 stored a 32-dim embedding that truncated spectral contrast away. v2 keeps all 45
dimensions. Old and new vectors are not comparable, so this rewrites the corpus in
batches, newest first, and never mixes versions in one scene-month.

  python -m sonic.reanalyse --db sonic.db --limit 3000
"""
import argparse, json, os, sqlite3, tempfile, time, urllib.request
from .analyser_local import LocalAnalyser
from .beatport import get_token, _get


def _local_copy(url):
    """librosa cannot open an http url: fetch the preview to a temp file and analyse that."""
    req = urllib.request.Request(url, headers={"User-Agent": "signal-sonic/reanalyse"})
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(url.split("?")[0])[1] or ".mp3")
    with urllib.request.urlopen(req, timeout=25) as r, os.fdopen(fd, "wb") as f:
        f.write(r.read())
    return path


def _preview_url(track_id, token):
    """tracks store no audio reference: resolve the current preview from Beatport by id."""
    if not str(track_id).startswith("bp:"): return None
    try:
        d = _get(f"/catalog/tracks/{str(track_id).split(':')[-1]}/", token)
        return (d.get("sample_url") or (d.get("preview") or {}).get("mp3", {}).get("url") or "") or None
    except Exception as e:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--limit", type=int, default=3000)
    ap.add_argument("--budget-minutes", type=int, default=200)
    a = ap.parse_args()
    an = LocalAnalyser()
    token = get_token()
    want = an.version
    c = sqlite3.connect(a.db); c.row_factory = sqlite3.Row
    # keep the old vector: chain-linking needs both versions on the same records
    c.execute("""create table if not exists features_archive(
        track_id text, analyser_ver text, features text, archived_at real,
        primary key (track_id, analyser_ver))""")
    todo = [r["track_id"] for r in c.execute(
        "select track_id from tracks where analyser_id='local' and coalesce(analyser_ver,'1') not like '2%' order by rowid desc limit ?",
        (a.limit,))]
    print(f"reanalyse: {len(todo)} tracks on an older version (target {want})", flush=True)
    t0 = time.time(); done = err = 0
    for tid in todo:
        if (time.time() - t0) / 60 > a.budget_minutes:
            print("budget reached; dispatch again to continue", flush=True); break
        try:
            ref = _preview_url(tid, token)
            if not ref:
                err += 1
                if err <= 3: print(f"  {tid}: no preview url", flush=True)
                continue
            old = c.execute("select features, analyser_ver from tracks where track_id=?", (tid,)).fetchone()
            if old and old["features"]:
                c.execute("insert or ignore into features_archive values(?,?,?,?)",
                          (tid, old["analyser_ver"] or "1", old["features"], time.time()))
            _last_path = [_local_copy(ref)]
            fv = an.analyse(_last_path[0])
            c.execute("update tracks set features=?, analyser_ver=? where track_id=?",
                      (json.dumps(fv.__dict__ if hasattr(fv, "__dict__") else fv), want, tid))
            try: os.unlink(_last_path[0])
            except Exception: pass
            done += 1
            if done % 200 == 0: c.commit(); print(f"  {done}/{len(todo)} re-analysed, {err} failed", flush=True)
        except Exception as e:
            err += 1
            if err <= 3: print(f"  {tid}: {type(e).__name__}: {str(e)[:90]}", flush=True)
    c.commit()
    print(f"reanalyse: {done} done, {err} failed, {max(0, len(todo)-done-err)} left", flush=True)


if __name__ == "__main__":
    main()
