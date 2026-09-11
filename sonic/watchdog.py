"""Watchdog for the pipeline.

Two failure modes cost us most of this week, and both needed a human to clear:

  a run stuck past its own timeout holds the concurrency group, so every queued run
  sits pending behind it indefinitely;

  the chain stops — a run ends without dispatching its follow-up — and the queue sits
  full while nothing runs.

This checks for both and fixes them. It runs on a schedule in its own workflow, with
its own concurrency group, so it is never blocked by the thing it is meant to unblock.

  python -m sonic.watchdog --repo benmcewensignal/signal-sonic
"""
import argparse, json, os, sys, time, urllib.error, urllib.request

API = "https://api.github.com"
STALL_MINUTES = 15   # a live run whose timestamp has not moved this long is a zombie
STUCK_MINUTES = 135          # the job cap is 120; allow slack for setup and teardown


def _req(path, token, method="GET", body=None):
    req = urllib.request.Request(f"{API}{path}", method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "User-Agent": "sonic-watchdog"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read()[:200].decode("utf-8", "replace")}


def age_minutes(iso):
    t = time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
    return (time.time() - time.mktime(t) + time.timezone) / 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="benmcewensignal/signal-sonic")
    ap.add_argument("--workflow", default="sonic.yml")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token: print("watchdog: no token"); return 1
    runs = _req(f"/repos/{a.repo}/actions/runs?per_page=20", token).get("workflow_runs", [])
    if not runs: print("watchdog: could not read runs"); return 1
    live = [r for r in runs if r["status"] in ("in_progress", "queued", "pending")
            and r["name"] != "watchdog"]
    actions = []
    # 1. a run past the cap is stuck: cancel it so the group frees
    for r in live:
        started = r.get("run_started_at") or r["created_at"]
        mins = age_minutes(started)
        silent = age_minutes(r.get("updated_at") or started)
        # A run whose timestamp stops advancing is a zombie: GitHub still believes it is alive,
        # so it holds the concurrency group and every queued run waits behind it forever. This
        # cost a whole afternoon: the watchdog saw traffic and reported healthy. A plain cancel
        # does not clear one, so escalate to force-cancel.
        if r["status"] == "in_progress" and silent > STALL_MINUTES:
            actions.append(f"zombie #{r['run_number']} (no update for {silent:.0f} min): force-cancelling")
            if not a.dry_run:
                _req(f"/repos/{a.repo}/actions/runs/{r['id']}/cancel", token, "POST")
                time.sleep(5)
                _req(f"/repos/{a.repo}/actions/runs/{r['id']}/force-cancel", token, "POST")
        elif r["status"] == "in_progress" and mins > STUCK_MINUTES:
            actions.append(f"cancel #{r['run_number']} (running {mins:.0f} min, cap {STUCK_MINUTES})")
            if not a.dry_run:
                _req(f"/repos/{a.repo}/actions/runs/{r['id']}/cancel", token, "POST")
                time.sleep(5)
                _req(f"/repos/{a.repo}/actions/runs/{r['id']}/force-cancel", token, "POST")
        # a run pending far longer than a job takes means it is blocked by something already gone
        elif r["status"] in ("queued", "pending") and mins > STUCK_MINUTES * 2:
            actions.append(f"cancel pending #{r['run_number']} (waiting {mins:.0f} min)")
            if not a.dry_run: _req(f"/repos/{a.repo}/actions/runs/{r['id']}/cancel", token, "POST")
    # 2. nothing running and work outstanding: the chain stopped, so restart it
    still_live = [r for r in live if r["status"] == "in_progress"
                  and age_minutes(r.get("updated_at") or r.get("run_started_at") or r["created_at"]) <= STALL_MINUTES
                  and age_minutes(r.get("run_started_at") or r["created_at"]) <= STUCK_MINUTES]
    if not still_live:
        try:
            done = {d["file"] for d in json.load(open("queue/done.json")) if not d.get("requeued")}
            import glob
            pending = [f for f in glob.glob("queue/*.json")
                       if os.path.basename(f) not in done and os.path.basename(f) not in ("done.json", "last-run.json")]
        except Exception as e:
            pending = []; print(f"watchdog: could not read the queue ({e!r})")
        # idleness must be measured from the pipeline, not from any run: the watchdog's own
        # heartbeat is a run, so counting it means the chain never looks idle and never restarts
        others = [r for r in runs if r["name"] != "watchdog"]
        idle = age_minutes(others[0]["updated_at"]) if others else 999
        print(f"watchdog: {len(pending)} pending, idle {idle:.0f} min", flush=True)
        if pending and idle > 20:
            actions.append(f"restart the chain: {len(pending)} job(s) pending, nothing running for {idle:.0f} min")
            if not a.dry_run:
                r = _req(f"/repos/{a.repo}/actions/workflows/{a.workflow}/dispatches", token, "POST",
                         {"ref": "main", "inputs": {"mode": "queue"}})
                if r.get("_error"): actions.append(f"  dispatch failed: {r['_error']} {r.get('_body','')[:80]}")
    print(json.dumps({"checked": len(runs), "live": len(live), "actions": actions or ["nothing to do"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
