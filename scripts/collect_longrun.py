#!/usr/bin/env python3
"""
Long-running BTH ADS-B collector for hosted CI (Gitee Go / GitHub Actions).

Why "long-running" instead of one-shot-per-cron
----------------------------------------------
Polling every 10 minutes via scheduled CI would need ~2016 builds for a
14-day campaign, which blows through free build quotas and still only gives
10-minute resolution.

Instead one job loops internally at a high frequency (default 30 s) for a
few hours, committing periodically. That cuts build count to ~56 for the
whole campaign AND yields much finer trajectory resolution.

Usage:
    python collect_longrun.py
    python collect_longrun.py --interval 30 --max-minutes 26 --commit-every 10

Note on --max-minutes
---------------------
The campaign runs one 26-minute job every 30 minutes (see .github/workflows/
collect.yml).  Trajectory resolution comes from --interval (the in-job poll
period), NOT from the job length, so a short job loses nothing -- while any
run that is killed or delayed can only cost 30 minutes instead of 6 hours.
Measured 2026-10-11 with the old 350-minute/6-hour configuration: only ~55% of
a 27.5-hour stretch actually had data, including one 7h48m outage.

Environment:
    OPENSKY_CLIENT_ID       (optional; adsb.lol needs no credentials)
    OPENSKY_CLIENT_SECRET
    PUSH_URL                https://<user>:<token>@gitee.com/<owner>/<repo>.git
                            If unset, data is collected but NOT committed.
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from cloud_collect import collect_adsblol, collect_opensky, STATS

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def git(*args, check=False):
    return subprocess.run(["git", *args], cwd=ROOT,
                          capture_output=True, text=True, check=check)


def new_batch():
    """Unique stem for the next commit window's files."""
    now = datetime.now(timezone.utc)
    rid = (os.environ.get("GITHUB_RUN_ID") or "local")[-4:]
    return f"{now.strftime('%Y%m%d')}_{now.strftime('%H%M')}_{rid}"


def target_branch():
    """Branch to push to.

    CI checkouts are DETACHED, so `git rev-parse --abbrev-ref HEAD` returns
    the literal string "HEAD". Using that as a refspec ("HEAD:HEAD") makes
    pull and push fail silently - which is exactly how the first GitHub run
    stalled after ~30 minutes: one external push diverged the remote, every
    later push was rejected, and ~2 hours of data died with the runner.

    Prefer the branch name CI hands us; fall back to master.
    """
    for key in ("GITHUB_REF_NAME", "PUSH_BRANCH"):
        v = (os.environ.get(key) or "").strip()
        if v and v != "HEAD":
            return v
    b = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    return b if b and b != "HEAD" else "master"


def commit_and_push(tag):
    """Stage data/, commit, and push. Never fatal - data stays on disk."""
    push_url = os.environ.get("PUSH_URL")
    if not push_url:
        # GitHub Actions: use the built-in GITHUB_TOKEN automatically
        gh_token = os.environ.get("GITHUB_TOKEN")
        gh_repo = os.environ.get("GITHUB_REPOSITORY")
        if gh_token and gh_repo:
            push_url = f"https://x-access-token:{gh_token}@github.com/{gh_repo}.git"
    branch = target_branch()
    git("config", "user.name", "ci-collector")
    git("config", "user.email", "ci@local")
    git("add", "data")

    staged = git("status", "--porcelain")
    if not staged.stdout.strip():
        print(f"  [{tag}] no data change, skip commit")
        return

    cm = git("commit", "-m", f"chore(data): ADS-B snapshot {tag}")
    print(f"  [{tag}] commit rc={cm.returncode} branch={branch}")

    if not push_url:
        print("  PUSH_URL not set - skipping push (data is local only)")
        return

    # Sync with the remote before pushing: another run (or a local push) may
    # have moved the branch. Data files are per-batch, so rebase never
    # conflicts - each run only ever ADDS new files.
    for attempt in range(1, 4):
        rb = git("pull", "--rebase", push_url, branch)
        if rb.returncode != 0:
            git("rebase", "--abort")
            print(f"  [{tag}] rebase rc={rb.returncode} :: "
                  f"{rb.stderr.strip()[:120]}")
        ps = git("push", push_url, f"HEAD:{branch}")
        if ps.returncode == 0:
            print(f"  [{tag}] push rc=0")
            return
        print(f"  [{tag}] push rc={ps.returncode} ({attempt}/3) :: "
              f"{ps.stderr.strip()[:120]}")
        time.sleep(5)
    print(f"  [{tag}] !! push failed 3x - data stays committed locally, "
          f"will retry on the next window")


def main():
    ap = argparse.ArgumentParser(description="Long-running BTH ADS-B collector")
    ap.add_argument("--interval", type=int, default=30,
                    help="seconds between polls (default 30)")
    ap.add_argument("--max-minutes", type=float, default=300,
                    help="stop after this many minutes (default 300)")
    ap.add_argument("--commit-every", type=float, default=10,
                    help="commit every N minutes (default 10)")
    args = ap.parse_args()

    deadline = time.time() + args.max_minutes * 60
    commit_period = args.commit_every * 60
    last_commit = time.time()

    # Each commit window writes its own file, so concurrent runs never collide.
    os.environ["DATA_BATCH"] = new_batch()

    print("=" * 62)
    print(f"long-run collector @ {datetime.now(timezone.utc).isoformat()}")
    print(f"interval={args.interval}s  max={args.max_minutes}min  "
          f"commit every {args.commit_every}min")
    print(f"batch={os.environ['DATA_BATCH']}  branch={target_branch()}")
    print("=" * 62)

    cycles, ok_cycles, total_rows = 0, 0, 0
    adsblol_empty_streak = 0

    while time.time() < deadline:
        cycles += 1
        try:
            a = collect_adsblol()
            b = collect_opensky()
            total_rows += a + b
            if a or b:
                ok_cycles += 1
            print(f"  #{cycles}: adsb.lol={a} opensky={b}")
            # Track a *source-specific* outage: OpenSky keeps working while
            # adsb.lol reports no coverage, so a total-rows test would never
            # notice.  5 consecutive empty cycles = ~2.5 min at the default
            # interval; 120 = an hour.
            if a:
                adsblol_empty_streak = 0
            else:
                adsblol_empty_streak += 1
                if adsblol_empty_streak in (5, 20, 60, 120, 240):
                    mins = adsblol_empty_streak * args.interval / 60.0
                    print(f"  !! adsb.lol empty for {adsblol_empty_streak} "
                          f"consecutive cycles (~{mins:.0f} min) - these "
                          f"windows will have no aircraft types")
        except KeyboardInterrupt:
            print("interrupted")
            break
        except Exception as e:
            print(f"  #{cycles} ERROR {type(e).__name__}: {str(e)[:160]}")

        if time.time() - last_commit >= commit_period:
            commit_and_push(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            last_commit = time.time()
            os.environ["DATA_BATCH"] = new_batch()   # fresh file next window

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(args.interval, remaining))

    # final flush so the last window is never lost
    commit_and_push(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    print("=" * 62)
    print(f"done: cycles={cycles} successful={ok_cycles} rows={total_rows}")
    print(f"source stats: {STATS}")
    if adsblol_empty_streak >= 120:
        print(f"WARNING: adsb.lol produced nothing for the last "
              f"{adsblol_empty_streak} cycles - check feed coverage")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
