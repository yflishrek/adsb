#!/usr/bin/env python3
"""Stall detector for the BTH ADS-B campaign.

Why this exists
---------------
GitHub Actions emailed "the run failed" when run #3 was killed by the runner
1h22m into a 5h50m job.  That message names the symptom (a failed run) and not
the condition we care about (no data for N minutes), so it is easy to read as
"collection has stopped" when it has not -- and, worse, easy to miss when a run
is *dropped* outright, which produces no email at all because nothing ran.

This script asks the only question that matters: how old is the newest data?

The collector commits data every ``--commit-every`` minutes while it runs, so
the age of the most recent commit that touched ``data/flights/`` is a direct
measure of liveness.  Exit non-zero when it is stale, and the workflow failure
email becomes a correct, actionable alarm.

Usage
-----
    python scripts/check_freshness.py                 # git history, default 40 min
    python scripts/check_freshness.py --max-age-min 35
    python scripts/check_freshness.py --report        # never fails
    python scripts/check_freshness.py --ref origin/master   # audit the remote
    python scripts/check_freshness.py --cos           # audit the COS bucket

Since 2026-10-12 collection runs on Tencent SCF and the data goes to COS, so the
git-based mode only sees the retired GitHub channel. `--cos` is the live check:
object keys carry the batch timestamp (``opensky_20261012_053012_scf.csv``), so
the newest key is a direct measure of liveness and needs no extra API fields.

Run from anywhere inside the repository.
"""
import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA_PATHS = ["data/flights/adsblol", "data/flights/opensky"]

# Cloud batches look like `opensky_20261012_053012_scf.csv` (HHMMSS + channel),
# while GitHub-era files are `opensky_20261011_0727_7843.csv` (HHMM on the clock,
# then a run-id tail) and the earliest ones just `opensky_20261011.csv`.  All
# three shapes end up in the bucket once the historical corpus is migrated, so
# all three must parse -- ordered most precise first, because a looser pattern
# would otherwise match only a suffix and yield a wrong timestamp.
KEY_PATTERNS = [
    (re.compile(r"_(\d{8})_(\d{6})(?:_[0-9a-z]+)?\.csv$", re.I), "%Y%m%d%H%M%S"),
    (re.compile(r"_(\d{8})_(\d{4})(?:_[0-9a-z]+)?\.csv$", re.I), "%Y%m%d%H%M"),
    (re.compile(r"_(\d{8})\.csv$", re.I), "%Y%m%d"),
]


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO_ROOT,
                          capture_output=True, text=True)


def newest_commit_epoch(path, ref=None):
    """Unix time of the newest commit touching ``path``, or None."""
    cmd = ["log", "-1", "--format=%ct"]
    if ref:
        cmd.append(ref)
    cmd += ["--", path]
    r = git(*cmd)
    ts = r.stdout.strip()
    if not ts.isdigit():
        return None
    return int(ts)


def data_commits(n=8, ref=None):
    """The newest `chore(data)` commits: [(epoch, subject), ...]."""
    cmd = ["log", f"-{n}", "--format=%ct|%s"]
    if ref:
        cmd.append(ref)
    cmd += ["--", "data/flights/"]
    out = []
    for line in git(*cmd).stdout.splitlines():
        if "|" not in line:
            continue
        ts, subj = line.split("|", 1)
        if ts.isdigit():
            out.append((int(ts), subj.strip()))
    return out


def epoch_from_key(key):
    """Recover the batch timestamp from an object key, or None."""
    name = key.rsplit("/", 1)[-1]
    for pattern, fmt in KEY_PATTERNS:
        m = pattern.search(name)
        if not m:
            continue
        try:
            return int(datetime.strptime("".join(m.groups()), fmt)
                       .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def cos_report(max_age_min, report_only):
    """Audit the COS bucket -- the store the live collector actually writes."""
    try:
        from cos_client import CosClient, CosError
    except ImportError as e:
        print(f"cannot import cos_client: {e}")
        return 0 if report_only else 1

    try:
        cos = CosClient.from_env()
    except CosError as e:
        print(f"COS not configured: {e}")
        print("Set COS_BUCKET, COS_REGION, COS_SECRET_ID, COS_SECRET_KEY.")
        return 0 if report_only else 1

    keys = [k for k, _ in cos.list_objects("data/flights/") if k.endswith(".csv")]
    now = time.time()
    print(f"now (UTC)              : {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}")
    print(f"source                 : cos://{cos.bucket}/data/flights/")
    print(f"objects                : {len(keys)}")

    stamped = [(epoch_from_key(k), k) for k in keys]
    stamped = [(t, k) for t, k in stamped if t is not None]
    if not stamped:
        print("!! no object key carried a parseable timestamp")
        return 0 if report_only else 1

    stamped.sort()
    newest, newest_key = stamped[-1]
    age_min = (now - newest) / 60.0
    print(f"newest batch           : "
          f"{datetime.fromtimestamp(newest, timezone.utc):%Y-%m-%dT%H:%M:%SZ} "
          f"({age_min:.1f} min ago)")
    print(f"  {newest_key}")
    print(f"threshold              : {max_age_min:.0f} min")

    print("\nrecent batches (UTC):")
    for t, k in list(reversed(stamped))[:8]:
        print(f"  {datetime.fromtimestamp(t, timezone.utc):%m-%d %H:%M:%S}Z  "
              f"{(now - t) / 60.0:8.1f} min ago  {k.split('/')[-1]}")

    if age_min <= max_age_min:
        print(f"\nOK - the collector is live ({age_min:.1f} min < {max_age_min:.0f} min)")
        return 0
    print(f"\nSTALLED - no new ADS-B object for {age_min:.1f} min "
          f"(limit {max_age_min:.0f} min)")
    print("Check the SCF console -> bth-adsb-scf-collector -> 日志, and its 定时触发器.")
    return 0 if report_only else 1


def main():
    ap = argparse.ArgumentParser(description="BTH ADS-B staleness check")
    ap.add_argument("--max-age-min", type=float, default=40.0,
                    help="fail if the newest data commit is older than this "
                         "(default 40, i.e. the 30-min cadence plus slack)")
    ap.add_argument("--report", action="store_true",
                    help="print the report but always exit 0")
    ap.add_argument("--ref", default=None,
                    help="audit this ref instead of HEAD (e.g. origin/master)")
    ap.add_argument("--cos", action="store_true",
                    help="audit the COS bucket (the live store) instead of git")
    args = ap.parse_args()

    if args.cos:
        return cos_report(args.max_age_min, args.report)

    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        print("not inside a git repository")
        return 0 if args.report else 1

    epochs = [t for t in (newest_commit_epoch(p, args.ref) for p in DATA_PATHS)
              if t is not None]
    now = time.time()

    if not epochs:
        print("!! no commit touching data/flights/ was found in this checkout")
        print("   (a shallow clone needs fetch-depth >= 50)")
        return 0 if args.report else 1

    newest = max(epochs)
    age_min = (now - newest) / 60.0
    stamp = datetime.fromtimestamp(newest, timezone.utc)

    print(f"now (UTC)              : {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}")
    print(f"ref                    : {args.ref or 'HEAD'}")
    print(f"newest data commit     : {stamp:%Y-%m-%dT%H:%M:%SZ} "
          f"({age_min:.1f} min ago)")
    print(f"threshold              : {args.max_age_min:.0f} min")

    recent = data_commits(8, args.ref)
    if recent:
        print("\nrecent data commits (UTC):")
        for ts, subj in recent:
            print(f"  {datetime.fromtimestamp(ts, timezone.utc):%m-%d %H:%M:%S}Z  "
                  f"{(now - ts) / 60.0:8.1f} min ago  {subj[:52]}")

    if age_min <= args.max_age_min:
        print(f"\nOK - the collector is live ({age_min:.1f} min < "
              f"{args.max_age_min:.0f} min)")
        return 0

    print(f"\nSTALLED - no new ADS-B data commit for {age_min:.1f} min "
          f"(limit {args.max_age_min:.0f} min)")
    print("NOTE: git history only reflects the RETIRED GitHub channel. The live")
    print("      collector writes to COS -- use --cos for the real check.")
    print("Check: Settings -> Actions -> bth-adsb-collect")
    print("  * no run at all   -> the scheduled trigger was delayed or dropped;"
          " re-run the job manually")
    print("  * a run failed    -> the runner was probably killed mid-job;"
          " restart the job")
    return 0 if args.report else 1


if __name__ == "__main__":
    sys.exit(main())
