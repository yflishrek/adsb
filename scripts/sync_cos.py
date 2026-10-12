#!/usr/bin/env python3
"""Sync the BTH ADS-B corpus between Tencent COS and the local working copy.

Once collection moved to SCF + COS, COS became the single store of record:
`data/flights/` no longer arrives via `git pull`. This script is the bridge --
run it before any analysis or Stage 1/2 job so the local CSV corpus matches the
bucket.

    python sync_cos.py --pull              # bucket -> local (incremental)
    python sync_cos.py --pull --dry-run    # show what would change
    python sync_cos.py --list              # inventory of the bucket
    python sync_cos.py --upload-history    # local -> bucket (one-off migration)

Remote keys mirror the repository layout exactly
(``data/flights/<source>/<file>.csv``), which is why `merge_sources.py`'s
date-prefix globs keep working untouched.

Credentials: COS_SECRET_ID / COS_SECRET_KEY, plus COS_BUCKET / COS_REGION.
The SCF runtime gets short-lived credentials from its execution role instead,
but that mechanism does not exist off Tencent Cloud, so a local run needs a key
pair -- grant it read-only unless you also intend to run --upload-history.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cos_client import CosClient, CosError          # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE_DIR)
LOCAL_FLIGHTS = os.path.join(ROOT, "data", "flights")
REMOTE_PREFIX = "data/flights/"

SOURCES = ("adsblol", "opensky")


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


def rel(path):
    """Path for logging; never let a cross-mount path break the sync.

    `os.path.relpath` raises ValueError when the two paths live on different
    mounts (e.g. the corpus on D: while the script runs from C:).  That is the
    same trap already fixed in cloud_collect.rel -- logging must not be able to
    abort a data operation.
    """
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


def local_indices():
    """Map remote-key -> local path for every CSV already on disk."""
    found = {}
    for src in SOURCES:
        d = os.path.join(LOCAL_FLIGHTS, src)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name.endswith(".csv"):
                found[f"{REMOTE_PREFIX}{src}/{name}"] = os.path.join(d, name)
    return found


def pull(cos, dry_run=False):
    remote = cos.list_objects(REMOTE_PREFIX)
    print(f"remote: {len(remote)} object(s), {human(sum(s for _, s in remote))}")
    local = local_indices()
    print(f"local : {len(local)} CSV(s) under {rel(LOCAL_FLIGHTS)}")

    new, updated, skipped, failed = 0, 0, 0, 0
    for key, size in sorted(remote):
        if not key.endswith(".csv"):
            continue
        path = local.get(key)
        if path and os.path.exists(path) and os.path.getsize(path) == size:
            skipped += 1
            continue
        action = "update" if path and os.path.exists(path) else "new"
        if dry_run:
            print(f"  would {action:6} {key}  ({human(size)})")
            new += action == "new"
            updated += action == "update"
            continue
        # Rebuild the path from the key so a renamed local tree still syncs.
        dest = os.path.join(LOCAL_FLIGHTS, *key[len(REMOTE_PREFIX):].split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            data = cos.get_object(key)
        except CosError as e:
            print(f"  !! {key}: {e}")
            failed += 1
            continue
        # Write via a temp file so an interrupted run never leaves a short CSV
        # that would later look like a complete window.
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        new += action == "new"
        updated += action == "update"
        print(f"  {action:6} {key}  ({human(len(data))})")

    print()
    print(f"{'dry run -- ' if dry_run else ''}"
          f"downloaded {new} new, refreshed {updated}, up-to-date {skipped}, "
          f"failed {failed}")
    return 1 if failed else 0


def upload_history(cos, dry_run=False):
    """One-off: push the pre-COS (GitHub-era) corpus into the bucket."""
    remote = {k for k, _ in cos.list_objects(REMOTE_PREFIX)}
    local = local_indices()
    todo = sorted(k for k in local if k not in remote)
    print(f"local: {len(local)} CSV(s), of which {len(todo)} are not in the bucket")
    if not todo:
        print("nothing to migrate")
        return 0

    total = 0
    for key in todo:
        path = local[key]
        size = os.path.getsize(path)
        if dry_run:
            print(f"  would upload {key}  ({human(size)})")
            continue
        with open(path, "rb") as f:
            cos.put_object(key, f.read())
        total += size
        print(f"  uploaded {key}  ({human(size)})")
    if not dry_run:
        print(f"\nuploaded {len(todo)} object(s), {human(total)}")
    return 0


def inventory(cos):
    remote = cos.list_objects(REMOTE_PREFIX)
    by_src = {}
    for key, size in remote:
        parts = key.split("/")
        src = parts[2] if len(parts) > 3 else "(other)"
        n, b = by_src.get(src, (0, 0))
        by_src[src] = (n + 1, b + size)
    print(f"bucket {cos.bucket} ({cos.region}), prefix {REMOTE_PREFIX}")
    print(f"total: {len(remote)} object(s), {human(sum(s for _, s in remote))}")
    for src, (n, b) in sorted(by_src.items()):
        print(f"  {src:10} {n:6} object(s)  {human(b)}")
    if remote:
        keys = sorted(k for k, _ in remote)
        print(f"  first: {keys[0]}")
        print(f"  last : {keys[-1]}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Sync data/flights between COS and local")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pull", action="store_true", help="COS -> local (incremental)")
    g.add_argument("--upload-history", action="store_true",
                   help="local -> COS, only objects not already there")
    g.add_argument("--list", action="store_true", help="print a bucket inventory")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        cos = CosClient.from_env()
    except CosError as e:
        print(f"cos config error: {e}")
        print("\nSet COS_BUCKET, COS_REGION, COS_SECRET_ID, COS_SECRET_KEY.")
        return 2
    print(f"credential source: {cos.cred_source}")

    if args.list:
        return inventory(cos)
    if args.pull:
        return pull(cos, args.dry_run)
    return upload_history(cos, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
