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


def _cache_table(conn):
    conn.execute("""create table if not exists preview_cache(
        track_id text primary key, url text, resolved_at text)""")
    conn.commit()


def cached_preview_urls(conn, track_ids, token, max_age_days=45):
    """Resolve previews once and remember them.

    The corpus has been re-measured five times and every pass resolved every URL again from
    scratch. With the batch endpoint answering the wrong question, that is one network round
    trip per record per pass, eighteen shards deep, which is why a hundred-minute pass moved
    fifty-five records. A URL that worked last week almost always works this week, so keep it.
    """
    import datetime as _dt
    _cache_table(conn)
    want = [str(t) for t in track_ids]
    out, stale = {}, []
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=max_age_days)).isoformat()
    have = {}
    for i in range(0, len(want), 400):
        part = want[i:i + 400]
        q = ",".join("?" * len(part))
        for tid, url, at in conn.execute(
                f"select track_id, url, resolved_at from preview_cache where track_id in ({q})", part):
            have[tid] = (url, at)
    for t in want:
        row = have.get(t)
        if row and row[0] and (row[1] or "") > cutoff:
            out[t] = row[0]
        else:
            stale.append(t)
    if stale:
        fresh = preview_urls(stale, token)
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        conn.executemany("insert or replace into preview_cache(track_id,url,resolved_at) values(?,?,?)",
                         [(t, fresh.get(t), now) for t in stale])
        conn.commit()
        out.update({k: v for k, v in fresh.items() if v})
    print(f"    previews: {len(out) - len([t for t in stale if out.get(t)])} from cache, "
          f"{len(stale)} looked up", flush=True)
    return out


def preview_urls(track_ids, token, chunk=100):
    """Resolve many previews in one request instead of one request each.

    The per-record call was costing a network round trip per track, and eighteen shards
    making them at once triggered rate limiting that made each call slower still. Beatport
    takes a comma-separated id list, so a hundred tracks cost one request rather than a
    hundred. Falls back to the single lookup for anything the batch does not return.
    """
    out = {}
    ids = [str(t).split(":")[-1] for t in track_ids if str(t).startswith("bp:")]
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        try:
            # The endpoint ignores an unknown filter and answers with its default page, so
            # asking for a hundred specific tracks returned the hundred newest on the site.
            # Every id we asked for was absent and we never noticed, because a track with no
            # entry is indistinguishable from a track with no preview. Ask with the parameter
            # the API documents, then check the answer contains what we asked for.
            d = _get(f"/catalog/tracks/?id={','.join(part)}&per_page={len(part)}", token)
            got = 0
            want = set(part)
            for item in (d.get("results") or []):
                if str(item.get("id")) not in want:
                    continue
                got += 1
                u = (item.get("sample_url")
                     or (item.get("preview") or {}).get("mp3", {}).get("url") or "") or None
                if u:
                    out[f"bp:{item.get('id')}"] = u
            if got == 0:
                # the batch answered with something else entirely: fall back one at a time,
                # which is slow and correct, rather than fast and wrong
                print(f"    batch {i//chunk + 1} returned none of the {len(part)} asked for; "
                      f"falling back to single lookups", flush=True)
                for tid in part:
                    try:
                        one = _get(f"/catalog/tracks/{tid}/", token)
                        u = (one.get("sample_url")
                             or (one.get("preview") or {}).get("mp3", {}).get("url") or "") or None
                        if u:
                            out[f"bp:{tid}"] = u
                    except Exception:
                        pass
        except Exception as e:
            # a swallowed chunk failure looks exactly like a chunk of tracks with no preview,
            # and with four chunks and one working it took three runs to notice
            print(f"    preview batch {i//chunk + 1} failed: {type(e).__name__}: {str(e)[:70]}",
                  flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--limit", type=int, default=3000)
    ap.add_argument("--budget-minutes", type=int, default=200)
    # A list of ids to measure whatever version they are on, or whether they are in the corpus
    # at all. The canon needs this: those records were never queued, so there is no version for
    # them to be stale against and the usual selector cannot see them.
    ap.add_argument("--ids-file", default=None)
    a = ap.parse_args()
    an = LocalAnalyser()
    token = get_token()
    want = an.version
    c = sqlite3.connect(a.db); c.row_factory = sqlite3.Row
    # keep the old vector: chain-linking needs both versions on the same records
    c.execute("""create table if not exists features_archive(
        track_id text, analyser_ver text, features text, archived_at real,
        primary key (track_id, analyser_ver))""")
    def _older(v, target):
        """Is this record on a version before the one we are targeting?

        The selector matched "not like '2%'", so a record at version 2 looked current even
        after the analyser moved to 2.1 and 2.2. A tempo fix shipped this morning and
        re-measured nothing, because every record already matched the prefix."""
        def parts(x):
            # versions are stored with a build hash: "2+865c64cc". Parsing that whole string
            # gave version zero, which is older than everything, so the job would have
            # re-measured all forty-four thousand records instead of the stale ones.
            out = []
            for p in str(x).split("+")[0].split("."):
                try: out.append(int(p))
                except ValueError: out.append(0)
            return out
        a, b = parts(v), parts(target)
        a = a + [0] * (len(b) - len(a)); b = b + [0] * (len(a) - len(b))
        return a < b

    explicit = None

    if a.ids_file:

        explicit = [x.strip() for x in open(a.ids_file) if x.strip()]

        for t in explicit:

            c.execute("insert or ignore into tracks(track_id, analyser_id, source) "

                      "values(?, 'local', 'canon')", (t,))

        c.commit()

        print(f"measuring {len(explicit)} records named in a list", flush=True)


    todo = [r["track_id"] for r in c.execute(
        "select track_id, coalesce(analyser_ver,'1') v from tracks where analyser_id='local'"
        " order by rowid")
            if _older(r["v"], want)]

    if explicit:

        todo = list(explicit)[:a.limit]
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
    left_total = c.execute("select count(*) from tracks where analyser_id='local' and coalesce(analyser_ver,'1') not like '2%'").fetchone()[0]
    print(f"reanalyse: {done} done, {err} failed, {left_total} still on the old version", flush=True)
    if left_total > 0:
        open("queue/.more", "w").write("reanalyse\n") if done else print("nothing converted: the records left have no preview, so this job is finished", flush=True)      # tells the runner to leave this job queued


if __name__ == "__main__":
    main()
