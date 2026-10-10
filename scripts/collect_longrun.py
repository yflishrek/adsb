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
    python collect_longrun.py --interval 30 --max-minutes 300 --commit-every 10

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

from cloud_collect import collect_adsblol, collect_opensky

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def git(*args, check=False):
    return subprocess.run(["git", *args], cwd=ROOT,
                          capture_output=True, text=True, check=check)


def commit_and_push(tag):
    """Stage data/, commit, and push. Never fatal - data stays on disk."""
    push_url = os.environ.get("PUSH_URL")
    if not push_url:
        # GitHub Actions: use the built-in GITHUB_TOKEN automatically
        gh_token = os.environ.get("GITHUB_TOKEN")
        gh_repo = os.environ.get("GITHUB_REPOSITORY")
        if gh_token and gh_repo:
            push_url = f"https://x-access-token:{gh_token}@github.com/{gh_repo}.git"
    git("config", "user.name", "ci-collector")
    git("config", "user.email", "ci@local")
    git("add", "data")

    staged = git("status", "--porcelain")
    if not staged.stdout.strip():
        print(f"  [{tag}] no data change, skip commit")
        return

    cm = git("commit", "-m", f"chore(data): ADS-B snapshot {tag}")
    print(f"  [{tag}] commit rc={cm.returncode}")

    if not push_url:
        print("  PUSH_URL not set - skipping push (data is local only)")
        return

    # rebase first in case a concurrent job pushed
    rb = git("pull", "--rebase", push_url, "HEAD:" + current_branch())
    if rb.returncode != 0:
        print(f"  pull/rebase failed: {rb.stderr.strip()[:200]}")
    ps = git("push", push_url, "HEAD:" + current_branch())
    print(f"  [{tag}] push rc={ps.returncode}"
          + ("" if ps.returncode == 0 else f" :: {ps.stderr.strip()[:200]}"))


def current_branch():
    r = git("rev-parse", "--abbrev-ref", "HEAD")
    return (r.stdout.strip() or "master")


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

    print("=" * 62)
    print(f"long-run collector @ {datetime.now(timezone.utc).isoformat()}")
    print(f"interval={args.interval}s  max={args.max_minutes}min  "
          f"commit every {args.commit_every}min")
    print("=" * 62)

    cycles, ok_cycles, total_rows = 0, 0, 0

    while time.time() < deadline:
        cycles += 1
        try:
            a = collect_adsblol()
            b = collect_opensky()
            total_rows += a + b
            if a or b:
                ok_cycles += 1
            print(f"  #{cycles}: adsb.lol={a} opensky={b}")
        except KeyboardInterrupt:
            print("interrupted")
            break
        except Exception as e:
            print(f"  #{cycles} ERROR {type(e).__name__}: {str(e)[:160]}")

        if time.time() - last_commit >= commit_period:
            commit_and_push(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            last_commit = time.time()

        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(args.interval, remaining))

    # final flush so the last window is never lost
    commit_and_push(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    print("=" * 62)
    print(f"done: cycles={cycles} successful={ok_cycles} rows={total_rows}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
