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
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace")
    for line in p.stdout:
        print(line, end="", flush=True); tail.append(line.rstrip()[:300])
    p.wait()
    log.append({"cmd": " ".join(cmd), "rc": p.returncode, "minutes": round((time.time() - t0) / 60, 1),
                "tail": list(tail)})
    return p.returncode

def _write_log(log, push=False):
    try:
        json.dump({"ran": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "log": log}, open("queue/last-run.json", "w"), indent=1)
    except Exception:
        return
    if push and os.environ.get("GITHUB_ACTIONS"):
        # the run may be killed before persist: put the evidence on main now, best effort
        try:
            subprocess.run(["git", "config", "user.name", "sonic-bot"], check=False)
            subprocess.run(["git", "config", "user.email", "sonic@earlysignal.live"], check=False)
            subprocess.run(["git", "add", "queue/last-run.json"], check=False)
            subprocess.run(["git", "commit", "-q", "-m", "queue log (in progress)"], check=False)
            subprocess.run(["git", "fetch", "-q", "origin", "main"], check=False)
            subprocess.run(["git", "rebase", "-q", "origin/main"], check=False)
            r = subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], capture_output=True, text=True)
            if r.returncode: subprocess.run(["git", "rebase", "--abort"], check=False)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-minutes", type=int, default=75)
    ap.add_argument("--max-jobs", type=int, default=1)
    a = ap.parse_args()
    t_start = time.time(); budget = a.budget_minutes * 60
    done_path = "queue/done.json"
    done = json.load(open(done_path)) if os.path.exists(done_path) else []
    done_names = {d["file"] for d in done}
    # a job that has failed twice is parked, not retried forever
    fails = {}
    for d in done:
        if d["file"].endswith("#attempt"): fails[d["file"][:-8]] = fails.get(d["file"][:-8], 0) + 1
    jobs = sorted(f for f in glob.glob("queue/*.json") if os.path.basename(f) not in ("done.json", "last-run.json")
                  and os.path.basename(f) not in done_names)
    print(f"queue: {len(jobs)} pending, budget {a.budget_minutes} min, max {a.max_jobs} job(s) this run", flush=True)
    jobs_run = 0
    log = []; touched_db = False; touched_mixes = False
    import atexit; atexit.register(lambda: _write_log(log))
    for f in jobs:
        if jobs_run >= a.max_jobs:
            print(f"max jobs reached: leaving {os.path.basename(f)} and later for the next run", flush=True); break
        jobs_run += 1
        elapsed = (time.time() - t_start) / 60
        if elapsed > a.budget_minutes - 15:
            print(f"budget nearly spent ({elapsed:.0f} min): leaving {os.path.basename(f)} and later for the next push", flush=True); break
        try:
            job = json.load(open(f))
        except Exception as e:
            log.append({"cmd": f"read {os.path.basename(f)}", "rc": 98, "tail": [repr(e)]}); continue
        mode = job.get("mode"); remaining = int(a.budget_minutes - elapsed - 20)
        try:
            st_ = os.statvfs("."); free_gb = st_.f_bavail * st_.f_frsize / 1e9
            log.append({"cmd": f"before {mode}: free disk {free_gb:.1f} GB", "rc": 0})
            print(f"free disk {free_gb:.1f} GB", flush=True)
        except Exception:
            pass
        print(f"\n=== {os.path.basename(f)}: {job}", flush=True)
        rc = 0
        try:
          if mode == "backfill":
              cmd = [sys.executable, "-m", "sonic.backfill", "fetch", "--from", job["month_from"], "--to", job["month_to"], "--db", "sonic.db", "--analyser", "local"]
              if job.get("scenes"): cmd += ["--scenes", job["scenes"]]
              rc = run(cmd, log); touched_db = True
          elif mode == "metadata":
              rc = run([sys.executable, "-m", "sonic.metadata", "--db", "sonic.db", "--limit", str(job.get("limit", 3000))], log); touched_db = True
          elif mode in ("mixscan", "mixrescan"):
              cmd = [sys.executable, "-m", "sonic.discover", "scan", "--db", "sonic.db", "--max-minutes", "110", "--budget-minutes", str(max(20, min(55, remaining)))]
              cmd += ["--rescan"] if mode == "mixrescan" else ["--per-scene", str(job.get("per_scene", 2))]
              rc = run(cmd, log); touched_db = touched_mixes = True
          elif mode == "reindex":
              rc = run([sys.executable, "-m", "sonic.mixes", "reindex", "--db", "sonic.db", "--limit", str(job.get("limit", 900))], log); touched_mixes = True
          elif mode == "reanalyse":
              rc = run([sys.executable, "-m", "sonic.reanalyse", "--db", "sonic.db",
                        "--limit", str(job.get("limit", 3000)), "--budget-minutes", str(max(20, min(55, remaining)))], log); touched_db = True
          elif mode == "supply":
              rc = run([sys.executable, "-m", "sonic.supply", "--db", "sonic.db", "--fetch",
                        "--months", str(job.get("months", 24)), "--out", "data/supply.json"], log); touched_db = True
          elif mode == "nts":
              rc = run([sys.executable, "-m", "sonic.nts_tracklists", "--db", "sonic.db", "--limit", str(job.get("limit", 200)), "--out", "data/nts-truth.json"], log); touched_db = True
          elif mode == "discogs":
              rc = run([sys.executable, "-m", "sonic.discogs", "--db", "sonic.db", "--limit", str(job.get("limit", 600))], log); touched_db = True
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
        except Exception as e:
            import traceback
            rc = 97
            log.append({"cmd": f"{mode} ({os.path.basename(f)})", "rc": 97, "tail": traceback.format_exc().splitlines()[-6:]})
            print(f"job {mode} raised: {e!r}", flush=True)
        _write_log(log, push=True)
        if rc and fails.get(os.path.basename(f), 0) < 1:
            if os.path.exists("queue/.more"):
            os.remove("queue/.more")
            log.append({"cmd": f"{mode}: work remains, job stays queued", "rc": 0})
            print("job reports remaining work: leaving it in the queue", flush=True)
            _write_log(log, push=True)
            open("queue/.next", "w").write(os.path.basename(f) + "\n")
            break
        done.append({"file": os.path.basename(f) + "#attempt", "rc": rc, "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            json.dump(done, open(done_path, "w"), indent=1)
            print(f"job failed (rc {rc}); it will be retried once", flush=True)
            continue
        done.append({"file": os.path.basename(f), "rc": rc, "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        json.dump(done, open(done_path, "w"), indent=1)
        if rc and mode in ("mixscan", "mixrescan") and (time.time() - t_start) / 60 > a.budget_minutes - 30:
            break
    # joins after the work, so the data files reflect it
    if touched_mixes:
        run([sys.executable, "-m", "sonic.calibrate_plays", "--db", "sonic.db"], log)
    run([sys.executable, "-m", "sonic.chainlink", "--db", "sonic.db", "--out", "data/chainlink.json"], log)
    run([sys.executable, "-m", "sonic.reach", "--db", "sonic.db", "--out", "data/reach.json"], log)
    run([sys.executable, "-m", "sonic.texture", "--db", "sonic.db", "--out", "data/texture.json"], log)
    run([sys.executable, "-m", "sonic.supply", "--db", "sonic.db", "--out", "data/supply.json"], log)
    run([sys.executable, "-m", "sonic.artists", "--db", "sonic.db", "--site", "https://www.earlysignal.live", "--out", "data/artists-latest.json"], log)
    run([sys.executable, "-m", "sonic.venues", "--site", "https://www.earlysignal.live", "--artists", "data/artists-latest.json", "--out", "data/venues-latest.json"], log)
    json.dump({"ran": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "log": log}, open("queue/last-run.json", "w"), indent=1)
    remaining_jobs = [f for f in jobs if os.path.basename(f) not in {d["file"] for d in done}]
    if remaining_jobs and os.environ.get("GITHUB_ACTIONS"):
        open("queue/.next", "w").write(os.path.basename(remaining_jobs[0]) + "\n")
        print(f"{len(remaining_jobs)} job(s) remain: a follow-up run is requested", flush=True)
    print("\nqueue run complete:", json.dumps([{k: v for k, v in l.items() if k != "tail"} for l in log], indent=0), flush=True)

if __name__ == "__main__":
    main()
