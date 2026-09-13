"""Does the tempo estimator reproduce tempos the field already agrees on?

A validation, not a re-measure. Twenty records a scene, fetched and measured for tempo only
-- no embedding, no spectral work -- which is a second a record rather than nine. The whole
check is minutes.

The test is external: drum and bass is 168-178, trance 136-144, house 120-128. These are not
things to discover. An estimator that cannot reproduce them is wrong, and we know that without
any reference to our own labels.
"""
import argparse, json, os, sqlite3, sys, time
import numpy as np

KNOWN = {
    "drum-and-bass": (168, 178), "deep-house": (118, 126), "tech-house": (122, 130),
    "trance-main-floor": (136, 144), "hard-techno": (145, 160), "amapiano": (108, 118),
    "140-deep-dubstep-grime": (138, 144), "uk-garage-speed-garage": (128, 140),
    "house": (120, 128), "techno-peak-time": (135, 150), "melodic-house-techno": (118, 126),
    "progressive-house": (120, 128), "afro-house": (118, 126), "bass-house": (124, 130),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db")
    ap.add_argument("--per-scene", type=int, default=20)
    ap.add_argument("--out", default="out/tempo-check.json")
    a = ap.parse_args()
    import librosa
    from .beatport import get_token
    from .backfill import _fetch_preview
    from .reanalyse import preview_urls
    from .tempo_resolved import tempo_resolved

    token = get_token()
    c = sqlite3.connect(a.db); c.row_factory = sqlite3.Row
    out = {}
    t0 = time.time()
    for scene, (lo, hi) in KNOWN.items():
        rows = [r["track_id"] for r in c.execute(
            "select distinct track_id from track_scenes where scene=? and week like '____-M__' "
            "order by random() limit ?", (scene, a.per_scene * 2))]
        urls = preview_urls(rows, token)
        got = []
        for tid in rows:
            if len(got) >= a.per_scene:
                break
            u = urls.get(tid)
            if not u:
                continue
            local = None
            try:
                local = _fetch_preview(u)
                y, sr = librosa.load(local, sr=22050, mono=True, duration=45.0)
                r = tempo_resolved(y, sr)
                if r.get("tempo"):
                    got.append(r["tempo"])
            except Exception:
                pass
            finally:
                if local:
                    try: os.unlink(local)
                    except OSError: pass
        if got:
            med = float(np.median(got))
            out[scene] = {"expected": [lo, hi], "median": round(med, 1),
                          "in_range_pct": round(float(np.mean((np.array(got) >= lo) & (np.array(got) <= hi)) * 100)),
                          "distinct": len(set(round(x, 1) for x in got)), "n": len(got),
                          "ok": lo <= med <= hi}
            print(f"{scene:26} expect {lo}-{hi:<4} median {med:6.1f}  "
                  f"{out[scene]['in_range_pct']:3}% in range  {len(got)} records  "
                  f"{'ok' if out[scene]['ok'] else 'WRONG'}", flush=True)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w"), indent=1)
    good = sum(1 for v in out.values() if v["ok"])
    print(f"\n{good} of {len(out)} scenes reproduce the tempo the field agrees on "
          f"({time.time()-t0:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
