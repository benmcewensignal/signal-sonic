"""Drain the job queue: every queue/*.json not yet in queue/done.json, oldest first,
inside a wall-clock budget. One push then runs everything pending, and a cancelled
pending run costs nothing because the next push picks up where this stopped.

  python -m sonic.queue_runner --budget-minutes 300
"""
import argparse, collections, json, os, subprocess, sys, time, glob

def run(cmd, log):
    """Run a step, echo it live, and keep the tail of its output in the log: GitHub's
    own logs live on a host we cannot reach, so the repo has to carry the evidence."""
    print(f"\n$ {' '.join(cmd)}", flush=True)
    t0 = time.time()
    tail = collections.deque(maxlen=25)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        print(line, end="", flush=True); tail.append(line.rstrip()[:300])
    p.wait()
    log.append({"cmd": " ".join(cmd), "rc": p.returncode, "minutes": round((time.time() - t0) / 60, 1),
                "tail": list(tail)})
    return p.returncode

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--budget-minutes", type=int, default=300); a = ap.parse_args()
    t_start = time.time(); budget = a.budget_minutes * 60
    done_path = "queue/done.json"
    done = json.load(open(done_path)) if os.path.exists(done_path) else []
    done_names = {d["file"] for d in done}
    jobs = sorted(f for f in glob.glob("queue/*.json") if os.path.basename(f) != "done.json" and os.path.basename(f) not in done_names)
    print(f"queue: {len(jobs)} pending, budget {a.budget_minutes} min", flush=True)
    log = []; touched_db = False; touched_mixes = False
    for f in jobs:
        elapsed = (time.time() - t_start) / 60
        if elapsed > a.budget_minutes - 25:
            print(f"budget nearly spent ({elapsed:.0f} min): leaving {os.path.basename(f)} and later for the next push", flush=True); break
        job = json.load(open(f)); mode = job.get("mode"); remaining = int(a.budget_minutes - elapsed - 20)
        print(f"\n=== {os.path.basename(f)}: {job}", flush=True)
        rc = 0
        if mode == "backfill":
            cmd = [sys.executable, "-m", "sonic.backfill", "fetch", "--from", job["month_from"], "--to", job["month_to"], "--db", "sonic.db", "--analyser", "local"]
            if job.get("scenes"): cmd += ["--scenes", job["scenes"]]
            rc = run(cmd, log); touched_db = True
        elif mode == "metadata":
            rc = run([sys.executable, "-m", "sonic.metadata", "--db", "sonic.db", "--limit", str(job.get("limit", 3000))], log); touched_db = True
        elif mode in ("mixscan", "mixrescan"):
            cmd = [sys.executable, "-m", "sonic.discover", "scan", "--db", "sonic.db", "--max-minutes", "110", "--budget-minutes", str(max(20, remaining))]
            cmd += ["--rescan"] if mode == "mixrescan" else ["--per-scene", str(job.get("per_scene", 2))]
            rc = run(cmd, log); touched_db = touched_mixes = True
        elif mode == "listeners":
            rc = run([sys.executable, "-m", "sonic.listeners", "--db", "sonic.db", "--limit", str(job.get("limit", 800))], log); touched_db = True
        elif mode == "names":
            rc = run([sys.executable, "-m", "sonic.names"], log)
        elif mode == "genres":
            with open("data/beatport-genres.txt", "w") as out:
                r = subprocess.run([sys.executable, "-m", "sonic.beatport", "genres"], stdout=out); rc = r.returncode
            log.append({"cmd": "sonic.beatport genres", "rc": rc})
        elif mode == "artists":
            pass   # joins always run at the end
        else:
            print(f"unknown mode {mode}; skipping", flush=True); rc = 99
        done.append({"file": os.path.basename(f), "rc": rc, "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        json.dump(done, open(done_path, "w"), indent=1)
        if rc and mode in ("mixscan", "mixrescan") and (time.time() - t_start) / 60 > a.budget_minutes - 30:
            break
    # joins after the work, so the data files reflect it
    if touched_mixes:
        run([sys.executable, "-m", "sonic.calibrate_plays", "--db", "sonic.db"], log)
    run([sys.executable, "-m", "sonic.supply", "--db", "sonic.db", "--out", "data/supply.json"], log)
    run([sys.executable, "-m", "sonic.artists", "--db", "sonic.db", "--site", "https://www.earlysignal.live", "--out", "data/artists-latest.json"], log)
    run([sys.executable, "-m", "sonic.venues", "--site", "https://www.earlysignal.live", "--artists", "data/artists-latest.json", "--out", "data/venues-latest.json"], log)
    json.dump({"ran": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "log": log}, open("queue/last-run.json", "w"), indent=1)
    print("\nqueue run complete:", json.dumps(log, indent=0), flush=True)

if __name__ == "__main__":
    main()
