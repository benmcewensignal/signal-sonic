"""DJ sets from MixesDB tracklists, matched to Beatport and measured on the corpus analyser.

MixesDB's community tracklists are released under CC BY-SA 3.0: the derived measures here are
ours to publish; any tracklist shown must carry that licence. Read through its MediaWiki API, one
request a second. Each set becomes records at the minute they start ([mmm] stamps), matched to
Beatport by artist, title and label; records the corpus lacks are measured from their previews.
  python -m sonic.tracklists --djs "Carl Cox,Andy C" --since 2023 --db sonic.db --measure 300
"""
import argparse, json, os, re, sqlite3, time, urllib.parse, urllib.request, difflib
from sonic import beatport as B

API = "https://www.mixesdb.com/w/api.php"
UA = "SignalSonic/1.0 (research on DJ sets; github.com/benmcewensignal)"
OUT = "data/tracklists"


def api(params):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    for i in range(3):
        try:
            time.sleep(1.0)
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
                return json.load(r)
        except Exception as e:
            err = e; time.sleep(5 * (i + 1))
    raise err


def sets_for(dj, since):
    titles, cont = [], {}
    while True:
        r = api({"action": "query", "list": "categorymembers", "cmtitle": "Category:" + dj, "cmlimit": "500", **cont})
        titles += [m["title"] for m in r["query"]["categorymembers"] if m.get("ns") == 0]
        if "continue" in r: cont = {"cmcontinue": r["continue"]["cmcontinue"]}
        else: break
    return sorted(t for t in titles if re.match(r"\d{4}", t) and int(t[:4]) >= since)


STAMP = re.compile(r"^\[(\d{1,3}|\d{1,2}:\d{2}(?::\d{2})?|\?[:?]*)\]\s*(.+)$")


def parse(wikitext):
    """Records in order: minute (or None), artist, title, label. Unidentified entries are kept as gaps."""
    m = re.search(r"==\s*Tracklist\s*==(.*?)(?:\n==[^=]|\Z)", wikitext, re.S)
    body = m.group(1) if m else wikitext
    out = []
    for ln in body.split("\n"):
        s = re.sub(r"<[^>]+>", "", ln).strip().lstrip("#*:").strip()
        if not s or s.startswith(("{{", "|", "==")): continue
        mm = STAMP.match(s); rest = mm.group(2) if mm else s; minute = None
        if mm and "?" not in mm.group(1):
            parts = [int(x) for x in mm.group(1).split(":")]   # [mmm], [mm:ss] or [h:mm:ss]
            minute = parts[0] if len(parts) == 1 else (parts[0] if len(parts) == 2 else parts[0] * 60 + parts[1])
        if not mm and " - " not in rest: continue
        lab = re.search(r"\[([^\]]+)\]\s*$", rest); label = lab.group(1).strip() if lab else ""
        if lab: rest = rest[:lab.start()].strip()
        if rest.strip() in ("?", "ID", "ID - ID", "Intro") or " - " not in rest:
            out.append({"minute": minute, "id": None, "raw": rest}); continue
        artist, title = rest.split(" - ", 1)
        out.append({"minute": minute, "artist": artist.strip(), "title": title.strip(), "label": label})
    return out


def norm(s): return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()


def match(tok, rec, cache):
    """Best Beatport track for a tracklist entry, or None. Title and artist must both agree."""
    key = (rec["artist"].lower(), rec["title"].lower())
    if key in cache: return cache[key]
    q = f'{rec["artist"]} {re.sub(r"[()]", " ", rec["title"])}'[:120]
    try:
        res = B._get("/catalog/search/", tok, {"q": q, "type": "tracks", "per_page": 10}).get("tracks") or []
    except Exception:
        res = []
    best, bs = None, 0.0
    want_t = " ".join(norm(rec["title"])); want_a = set(norm(rec["artist"]))
    for t in res:
        full = f'{t.get("name","")} {t.get("mix_name","") or ""}'
        ts = difflib.SequenceMatcher(None, want_t, " ".join(norm(full))).ratio()
        arts = set(w for a in (t.get("artists") or []) for w in norm(a.get("name")))
        ov = len(want_a & arts) / max(1, len(want_a))
        lab = 0.1 if rec.get("label") and norm(rec["label"])[:1] == norm((t.get("label") or {}).get("name"))[:1] else 0.0
        sc = 0.6 * ts + 0.4 * ov + lab
        # when the tracklist names a mix, the match must be that mix: a different remix of the
        # same song can sound nothing like it (Delta Heavy's Voodoo People was matched to Pendulum's)
        want_mix = re.search(r"\(([^)]*(?:mix|remix|dub|edit|version|vip|rework)[^)]*)\)", rec["title"], re.I)
        if want_mix:
            wm = " ".join(norm(want_mix.group(1))); gm = " ".join(norm(t.get("mix_name") or ""))
            if difflib.SequenceMatcher(None, wm, gm).ratio() < 0.7: continue
        if ts >= 0.6 and ov >= 0.5 and sc > bs: best, bs = t, sc
    out = None if best is None else {"bp": "bp:%d" % best["id"], "score": round(bs, 3), "preview": best.get("sample_url"),
                                     "name": best.get("name"), "mix": best.get("mix_name"), "genre": (best.get("genre") or {}).get("slug")}
    cache[key] = out
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--djs", default="Carl Cox,Andy C"); ap.add_argument("--since", type=int, default=2023)
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--measure", type=int, default=300)
    ap.add_argument("--max-sets", type=int, default=40)
    a = ap.parse_args(); os.makedirs(OUT, exist_ok=True)
    tok = B.get_token(); cache = {}
    have = set()
    if os.path.exists(a.db):
        have = {r[0] for r in sqlite3.connect(a.db).execute("select track_id from tracks where analyser_id='local'")}
    measured_path = f"{OUT}/measured.jsonl"
    done = {json.loads(l)["track_id"] for l in open(measured_path)} if os.path.exists(measured_path) else set()
    to_measure = {}
    for dj in [d.strip() for d in a.djs.split(",") if d.strip()]:
        titles = sets_for(dj, a.since)[-a.max_sets:]
        sets = []
        for t in titles:
            try: recs = parse(api({"action": "parse", "page": t, "prop": "wikitext"})["parse"]["wikitext"]["*"])
            except Exception as e: print(f"skip {t}: {type(e).__name__}", flush=True); continue
            for r in recs:
                if r.get("artist"):
                    m = match(tok, r, cache)
                    if m:
                        r.update(m)
                        if m["bp"] not in have and m["bp"] not in done and m.get("preview"): to_measure[m["bp"]] = m["preview"]
            sets.append({"title": t, "records": recs})
        slug = re.sub(r"[^a-z0-9]+", "-", dj.lower()).strip("-")
        json.dump({"dj": dj, "source": "MixesDB (CC BY-SA 3.0)", "sets": sets}, open(f"{OUT}/{slug}.json", "w"), indent=0)
        n = sum(len(s["records"]) for s in sets); ids = sum(1 for s in sets for r in s["records"] if not r.get("artist"))
        mt = sum(1 for s in sets for r in s["records"] if r.get("bp")); inc = sum(1 for s in sets for r in s["records"] if r.get("bp") in have)
        stamped = sum(1 for s in sets for r in s["records"] if r.get("minute") is not None)
        msg = f"{dj}: {len(sets)} sets since {a.since}, {n} entries ({ids} unidentified), {mt} matched to Beatport, {inc} already in the corpus, {stamped} with a minute stamp"
        print(msg, flush=True); print(f"::notice title={dj}::{msg}", flush=True)
    # grow the corpus: measure what the sets play and the corpus lacks, on the corpus analyser
    from sonic.analyser_local import LocalAnalyser
    A = LocalAnalyser(); n_ok = 0
    with open(measured_path, "a") as f:
        for bp, url in list(to_measure.items())[:a.measure]:
            try:
                fv = A.analyse(B.download_preview(url)); d = fv.__dict__ if hasattr(fv, "__dict__") else dict(fv)
                f.write(json.dumps({"track_id": bp, "analyser_ver": A.version, "features": d}, default=float) + "\n"); n_ok += 1
            except Exception as e:
                print(f"measure {bp}: {type(e).__name__}", flush=True)
    msg = f"measured {n_ok} records the sets play and the corpus lacked, on analyser {A.version}; {max(0, len(to_measure) - a.measure)} left for the next run"
    print(msg, flush=True); print(f"::notice title=corpus growth::{msg}", flush=True)


if __name__ == "__main__":
    main()
