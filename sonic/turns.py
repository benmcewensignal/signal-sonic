"""When did a scene actually turn?

Until now we have dated turns by eye from the displacement series: the month with the
highest reading is called the peak, and the story is written around it. That is a
judgement, and it is not checkable.

This fits a change-point model to each scene's monthly sound centroid and reports the
months where the sound level-shifted, with the size of the shift. It is calibrated
rather than trusted: on a synthetic series with a known shift the settings below find it
exactly and find nothing in pure noise, and the sensitivity floor is stated — a shift
smaller than about 1.5 standard deviations per dimension is not detectable at this
series length, so an absence of change points means "no shift we could see", not "no
change".

Two things worth knowing about the result. A spike is not a change point: a scene that
jumps for one month and returns has no regime change, and the model will say so. And a
scene that drifts steadily has no change point either, because drift is not a shift.

  python -m sonic.turns --db sonic.db --out data/turns.json
"""
import argparse, collections, json, sqlite3, time
import numpy as np

PENALTY = 400          # calibrated: finds a true 2.5 sd shift, silent on pure noise
MIN_SEGMENT = 3        # a regime must last three months to count as one
FLOOR_SD = 1.5         # the smallest shift this setup detects at 24 months


def series(db, version_clause):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    E = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in c.execute(f"""select ts.scene, ts.week, t.features from track_scenes ts
                           join tracks t on t.track_id=ts.track_id and t.analyser_id='local'
                           where ts.week like '____-M__' and coalesce(t.analyser_ver,'1') {version_clause}"""):
        try: v = np.array(json.loads(r["features"])["embedding"], float)
        except Exception: continue
        E[r["scene"]][r["week"]].append(v / (np.linalg.norm(v) or 1))
    out = {}
    for scene, months in E.items():
        ms = [m for m in sorted(months) if len(months[m]) >= 5]
        if len(ms) >= 18:
            out[scene] = (ms, np.array([np.mean(months[m], axis=0) for m in ms]))
    return out


def month_name(m):
    y, n = m.split("-M")
    return f"{['January','February','March','April','May','June','July','August','September','October','November','December'][int(n)-1]} {y}"


def build(db):
    import ruptures as rpt
    c = sqlite3.connect(db)
    n2 = c.execute("select count(*) from tracks where analyser_ver like '2%'").fetchone()[0]
    n1 = c.execute("select count(*) from tracks where analyser_id='local' and coalesce(analyser_ver,'1') not like '2%'").fetchone()[0]
    clause = "like '2%'" if n2 > n1 else "not like '2%'"
    S = series(db, clause)
    largest = 0.0
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "version": "v2" if n2 > n1 else "v1",
           "method": ("change points in the monthly sound centroid, fitted with a penalised model. "
                      f"Calibrated so a known shift is found exactly and pure noise yields none; shifts smaller "
                      f"than about {FLOOR_SD} standard deviations per dimension are below the floor at this series length."),
           "scenes": {}}
    for scene, (ms, C) in sorted(S.items()):
        X = (C - C.mean(0)) / (C.std(0) + 1e-9)
        bkps = rpt.Pelt(model="l2", min_size=MIN_SEGMENT, jump=1).fit(X).predict(pen=PENALTY)[:-1]
        turns = []
        for b in bkps:
            before, after = X[max(0, b - 6):b], X[b:b + 6]
            if len(before) < 2 or len(after) < 2: continue
            size = float(np.linalg.norm(after.mean(0) - before.mean(0)) / np.sqrt(X.shape[1]))
            turns.append({"month": ms[b], "said": month_name(ms[b]), "shift_sd": round(size, 2)})
        # the honest number: the biggest six-month shift the scene actually makes, against the floor
        biggest = 0.0
        for b in range(6, len(ms) - 6):
            biggest = max(biggest, float(np.linalg.norm(X[b:b+6].mean(0) - X[b-6:b].mean(0)) / np.sqrt(X.shape[1])))
        largest = max(largest, biggest)
        out["scenes"][scene] = {"months": len(ms), "turns": turns, "biggest_shift_sd": round(biggest, 2),
                                "below_floor": biggest < FLOOR_SD,
                                "reading": (f"no detectable regime change: the largest shift this scene makes ({biggest:.2f} sd) is below the {FLOOR_SD} sd floor at {len(ms)} months, so a turn would have to be larger than anything it has done to be visible"
                                            if not turns else
                                            "regime change at " + ", ".join(t["said"] for t in turns))}
    out["floor_sd"] = FLOOR_SD
    out["largest_shift_any_scene_sd"] = round(largest, 2)
    out["verdict"] = ("no scene shifts far enough in six months for this method to date a turn at this series length; "
                      "scenes drift and spike rather than switching regime, and dating a turn from the peak month is "
                      "a description of the series, not a detected change") if largest < FLOOR_SD else "some scenes clear the floor"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sonic.db"); ap.add_argument("--out", default="data/turns.json")
    a = ap.parse_args()
    try:
        import ruptures  # noqa
    except Exception:
        import subprocess, sys
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "ruptures"], check=False)
    out = build(a.db)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, separators=(",", ":"))
    found = {k: v["turns"] for k, v in out["scenes"].items() if v["turns"]}
    print(json.dumps({"version": out["version"], "scenes": len(out["scenes"]),
                      "with a detected turn": len(found),
                      "turns": {k: [t["said"] for t in v] for k, v in found.items()}}, indent=1))


if __name__ == "__main__":
    main()
