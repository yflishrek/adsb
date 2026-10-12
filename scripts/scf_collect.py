#!/usr/bin/env python3
"""Tencent SCF (S云函数) collector -- the SECOND, independent collection channel.

Why this exists
---------------
GitHub Actions `schedule` is not dependable on this repo: measured 2026-10-12,
a `7,37 * * * *` cron produced only 3 runs in 13.5 hours (~89% of the trigger
events were delayed or dropped), leaving ~6% duty cycle. The compute was fine;
only the scheduler was broken. So we run the SAME collection on a second
scheduler and take the union of the two channels.

Why the GitHub *Data API* and not `git push`
--------------------------------------------
An SCF runtime is a read-only container: there is no `git` binary and no
writable filesystem outside /tmp. Committing therefore goes over plain HTTPS
with four API calls (blob -> tree -> commit -> update ref), the same mechanism
already proven by .workbuddy/push_via_api.py. Concurrency with the GitHub
Actions channel is handled by re-reading the ref and retrying: both channels
only ever ADD new files, so a lost race never conflicts, it only needs a retry.

Environment (set in the SCF console):
    OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET   OpenSky OAuth2 (required)
    GH_TOKEN        fine-grained PAT with Contents: Read and write on GH_REPO
    GH_REPO         e.g. yflishrek/adsb
    GH_BRANCH       default "master"
    GH_API_BASE     default "https://api.github.com" (override for a proxy)
    COLLECT_DATA_DIR default "/tmp/data/flights" (SCF only allows writes here)
Optional tuning:
    SCF_INTERVAL        seconds between polls, default 30
    SCF_MAX_MINUTES     collection window length, default 13
    SCF_COMMIT_EVERY    push every N minutes, default 4

Handler: index.main_handler
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cloud_collect as cc          # noqa: E402  (path set above)

CHANNEL = "scf"
USER_AGENT = "bth-adsb-scf-collector"
# Never start a poll this close to the hard timeout, so the final push lands.
TAIL_RESERVE_S = 60


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {msg}", flush=True)


# ------------------------------------------------------------------ config
def configure():
    """Resolve settings and force DATA_DIR somewhere writable.

    cloud_collect reads DATA_DIR at import time, so on SCF (where the code
    mount is read-only) we must both set the env var and patch the attribute,
    in case COLLECT_DATA_DIR was not configured in the console.
    """
    if not os.environ.get("COLLECT_DATA_DIR"):
        os.environ["COLLECT_DATA_DIR"] = "/tmp/data/flights"
    cc.DATA_DIR = os.environ["COLLECT_DATA_DIR"]
    os.makedirs(os.path.join(cc.DATA_DIR, "adsblol"), exist_ok=True)
    os.makedirs(os.path.join(cc.DATA_DIR, "opensky"), exist_ok=True)

    cfg = {
        "interval": int(os.environ.get("SCF_INTERVAL", "30")),
        "max_minutes": float(os.environ.get("SCF_MAX_MINUTES", "13")),
        "commit_every": float(os.environ.get("SCF_COMMIT_EVERY", "4")),
        "data_dir": cc.DATA_DIR,
        "token": (os.environ.get("GH_TOKEN") or "").strip(),
        "repo": (os.environ.get("GH_REPO") or "").strip(),
        "branch": (os.environ.get("GH_BRANCH") or "master").strip(),
        "api": (os.environ.get("GH_API_BASE") or "https://api.github.com").rstrip("/"),
    }
    if not (cfg["token"] and cfg["repo"]):
        log("!! GH_TOKEN / GH_REPO not set -- collecting to /tmp but NOT pushing; "
            "that data is lost when the container exits")
    return cfg


def remaining_seconds(context):
    """SCF hands us the remaining budget; fall back to the wall clock."""
    try:
        return float(context.get_remaining_time_in_millis()) / 1000.0
    except Exception:
        return None


def batch_tag():
    """Unique-per-window file stem. MUST keep the YYYYMMDD date prefix, because
    merge_sources.py globs `adsblol_<date>*.csv` / `opensky_<date>*.csv`."""
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}_{CHANNEL}"


# ------------------------------------------------------- GitHub Data API
def gh(method, path, cfg, payload=None):
    url = f"{cfg['api']}{path}"
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {cfg['token']}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", USER_AGENT)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise RuntimeError(f"HTTP {e.code} on {method} {path} :: {detail}")


def commit_files(cfg, files, message, attempts=5):
    """Commit {repo_path: bytes} onto the branch via the Git Data API.

    Returns the new commit SHA, or raises. Retries because the GitHub Actions
    channel may push to the same branch between our ref read and our ref write
    (that surfaces as 409/422); since both writers only add new files, simply
    re-reading the ref and rebuilding the commit always succeeds.
    """
    repo, branch = cfg["repo"], cfg["branch"]
    last = None
    for attempt in range(1, attempts + 1):
        try:
            head = gh("GET", f"/repos/{repo}/git/ref/heads/{branch}", cfg)["object"]["sha"]
            base_tree = gh("GET", f"/repos/{repo}/git/commits/{head}", cfg)["tree"]["sha"]

            tree = []
            for path, content in files.items():
                blob = gh("POST", f"/repos/{repo}/git/blobs", cfg, {
                    "content": base64.b64encode(content).decode(),
                    "encoding": "base64",
                })
                tree.append({"path": path, "mode": "100644",
                             "type": "blob", "sha": blob["sha"]})

            new_tree = gh("POST", f"/repos/{repo}/git/trees", cfg,
                          {"base_tree": base_tree, "tree": tree})
            new_commit = gh("POST", f"/repos/{repo}/git/commits", cfg, {
                "message": message, "tree": new_tree["sha"], "parents": [head],
            })
            gh("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", cfg,
               {"sha": new_commit["sha"], "force": False})
            return new_commit["sha"]
        except RuntimeError as e:
            last = e
            log(f"  push attempt {attempt}/{attempts} failed: {e}")
            if attempt == attempts:
                break
            time.sleep(2 * attempt)     # 2,4,6,8 s -- ride out the ref race
    raise last


def pending_files(data_dir, already):
    """Every CSV under data_dir not yet pushed, keyed by its repo path."""
    out = {}
    for sub in ("adsblol", "opensky"):
        d = os.path.join(data_dir, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".csv"):
                continue
            rel = f"data/flights/{sub}/{name}"
            if rel in already:
                continue
            with open(os.path.join(d, name), "rb") as f:
                out[rel] = f.read()
    return out


def flush(cfg, pushed):
    """Push everything new. Returns the count actually committed.

    On success the local copies are deleted. SCF reuses warm containers, so
    without this the /tmp files from the previous invocation would still be
    there next time, get picked up as "new" (the `pushed` set is per-call) and
    be re-committed forever -- an endless stream of no-op commits, plus /tmp
    growth against its 512 MB ceiling. On failure the files are KEPT, so a
    transient API error costs a retry rather than the data.
    """
    if not (cfg["token"] and cfg["repo"]):
        return 0
    files = pending_files(cfg["data_dir"], pushed)
    if not files:
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        sha = commit_files(cfg, files, f"chore(data): ADS-B snapshot {stamp} [scf]")
    except RuntimeError as e:
        log(f"  !! push FAILED, keeping files in /tmp for the next window: {e}")
        return 0
    pushed |= set(files)
    for rel in files:
        # rel is "data/flights/<sub>/<name>"; DATA_DIR already ends at "flights".
        try:
            os.remove(os.path.join(cfg["data_dir"], rel.split("/", 2)[2]))
        except OSError as e:
            log(f"  (could not delete pushed {rel}: {e})")
    log(f"  pushed {len(files)} file(s) -> {sha[:8]}")
    return len(files)


# ------------------------------------------------------------------ main
def main_handler(event, context=None):
    cfg = configure()
    log(f"=== SCF collector: dir={cfg['data_dir']} interval={cfg['interval']}s "
        f"window={cfg['max_minutes']}min push-every={cfg['commit_every']}min ===")

    os.environ["DATA_BATCH"] = batch_tag()      # channel-tagged, collision-free
    deadline = time.time() + cfg["max_minutes"] * 60
    commit_period = cfg["commit_every"] * 60
    last_push = time.time()
    pushed, cycles = set(), 0

    def out_of_budget():
        left = remaining_seconds(context)
        if left is not None and left < TAIL_RESERVE_S:
            log(f"  SCF budget nearly spent ({left:.0f}s left) - stopping polls")
            return True
        return False

    while time.time() < deadline and not out_of_budget():
        cycles += 1
        try:
            a = cc.collect_adsblol()
            b = cc.collect_opensky()
            log(f"  #{cycles}: adsb.lol={a} opensky={b}")
        except Exception as e:                  # never let one bad poll kill the window
            log(f"  #{cycles} ERROR {type(e).__name__}: {str(e)[:160]}")

        if time.time() - last_push >= commit_period:
            flush(cfg, pushed)
            last_push = time.time()
            os.environ["DATA_BATCH"] = batch_tag()   # fresh file next window

        sleep_for = min(cfg["interval"], max(0.0, deadline - time.time()))
        if sleep_for > 0:
            time.sleep(sleep_for)

    flush(cfg, pushed)                          # never lose the last window

    log(f"=== SCF done: cycles={cycles} files_pushed={len(pushed)} stats={cc.STATS} ===")
    return {"cycles": cycles, "pushed": len(pushed), "stats": cc.STATS}


if __name__ == "__main__":
    # Local smoke test: short window, no push unless GH_TOKEN/GH_REPO are set.
    os.environ.setdefault("SCF_MAX_MINUTES", "1")
    os.environ.setdefault("SCF_INTERVAL", "10")
    main_handler({}, None)
