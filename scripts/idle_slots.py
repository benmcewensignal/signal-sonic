"""How wide the next measuring pass may be: the account's idle job slots, the backlog, and a ceiling.
  python scripts/idle_slots.py <backlog> <per_shard> <max_shards>
Prints two lines for $GITHUB_OUTPUT: n=<shards> and shards=<JSON list of shard numbers>, n=0 when nothing is waiting.

Jobs running or queued across the four pipeline repositories share one account limit (40 at once on this plan; 34 have
run together with none queued). A pass takes what is idle, keeping RESERVE for the crawl's next run, the watchdog and
the queue, so it fills the gaps between separation passes rather than holding slots separation is waiting for. It is
never narrower than one shard when records are waiting: one shard queued behind a separation pass still finishes."""
import json
import math
import subprocess
import sys

CAP, RESERVE = 40, 4
REPOS = ("signal-sonic", "signal-sonic-features", "signal-sonic-audio", "signalgood")


def api(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:200])
    return json.loads(r.stdout)


def busy():
    n = 0
    for repo in REPOS:
        for status in ("in_progress", "queued"):
            for run in api(f"repos/benmcewensignal/{repo}/actions/runs?status={status}&per_page=100").get("workflow_runs", []):
                jobs = api(f"repos/benmcewensignal/{repo}/actions/runs/{run['id']}/jobs?per_page=100").get("jobs", [])
                n += sum(1 for j in jobs if j.get("status") != "completed")
    return n


def main():
    backlog, per, most = int(sys.argv[1]), max(1, int(sys.argv[2])), max(1, int(sys.argv[3]))
    if backlog <= 0:
        print("n=0"); print("shards=[]")
        print("nothing waiting to be measured", file=sys.stderr); return
    try:
        b = busy(); idle = CAP - b - RESERVE
    except Exception as e:
        b, idle = None, 4      # cannot tell: a modest pass rather than none
        print(f"could not count running jobs ({e}); taking 4", file=sys.stderr)
    n = max(1, min(most, idle, math.ceil(backlog / per)))
    print(f"n={n}"); print(f"shards={json.dumps(list(range(n)))}")
    print(f"backlog {backlog}, {b if b is not None else '?'} jobs running or queued of {CAP}, {max(idle, 0)} idle after a reserve of {RESERVE}: {n} shards of up to {per}", file=sys.stderr)


if __name__ == "__main__":
    main()
