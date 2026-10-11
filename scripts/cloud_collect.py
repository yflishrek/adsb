#!/usr/bin/env python3
"""
Cloud one-shot collector for BTH multi-airport ADS-B data.

Designed to run on GitHub Actions (or any scheduler): each invocation
takes ONE snapshot from adsb.lol and OpenSky, then appends to the daily CSV.

No local PC uptime required - the scheduler provides the "clock".

Environment variables (OpenSky OAuth2, optional for adsb.lol):
    OPENSKY_CLIENT_ID
    OPENSKY_CLIENT_SECRET

Usage:
    python cloud_collect.py
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------- config
BTH_CENTER = {"lat": 39.6, "lon": 116.8}
SEARCH_RADIUS = 80  # nautical miles, covers PEK/PKX/TSN
BBOX = {"lamin": 39.0, "lamax": 40.7, "lomin": 116.0, "lomax": 118.0}

ADSB_URL = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist}"
OPENSKY_STATES = "https://opensky-network.org/api/states/all"
TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE_DIR, "..")
DATA_DIR = os.path.join(ROOT, "data", "flights")

# An empty aircraft list is a *soft* failure: adsb.lol answers HTTP 200 with
# "ac": [] when its feeders have no coverage for the query point, which looks
# exactly like "there was no traffic".  Measured 2026-10-11: 18 consecutive
# 10-minute windows of empty answers (08:37-11:34 Beijing) wrote 18 header-only
# CSVs -- a silent 3-hour hole in the campaign.  Retry inside the cycle.
ADSBLOL_EMPTY_RETRIES = 3
ADSBLOL_RETRY_SLEEP = 3.0          # seconds between retries within one cycle

# Per-run counters, read by collect_longrun for its end-of-run summary.
STATS = {"adsblol_ok": 0, "adsblol_empty": 0, "adsblol_fail": 0,
         "opensky_ok": 0, "opensky_fail": 0, "opensky_skip": 0}

# Schema MUST stay identical to the local collectors so cloud and local
# rows can be appended to the same daily CSV without column drift.
ADSB_FIELDS = [
    "fetch_ts", "zone", "hex", "flight", "r", "t", "category", "lat", "lon",
    "alt_baro", "alt_geom", "gs", "ias", "tas", "mach", "track",
    "mag_heading", "true_heading", "baro_rate", "squawk", "emergency",
    "nav_qnh", "nav_altitude_mcp", "wind_dir", "wind_speed", "oat",
    "seen_pos", "messages", "rssi", "dst", "dir",
]

# CSV column -> adsb.lol API key (only where names differ)
ADSB_MAP = {"wind_dir": "wd", "wind_speed": "ws"}

OPENSKY_FIELDS = [
    "fetch_ts", "icao24", "callsign", "origin_country", "time_position",
    "last_contact", "longitude", "latitude", "baro_altitude",
    "on_ground", "velocity", "true_track", "vertical_rate",
    "geo_altitude", "squawk",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def day_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def file_tag() -> str:
    """Filename stem for this batch.

    Defaults to the UTC date, so every invocation appends to one daily CSV.
    Set DATA_BATCH to write each commit window to its OWN file instead. That
    matters in CI: if two runs ever write the same file, `git pull --rebase`
    hits a conflict at the appended tail and the run can never push again.
    Distinct filenames make concurrent writers conflict-free by construction.
    """
    return os.environ.get("DATA_BATCH") or day_tag()


def append_rows(path: str, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------- adsb.lol
def _adsblol_once(timeout=30):
    """One adsb.lol request.  Returns ``(aircraft_list | None, error | None)``."""
    url = ADSB_URL.format(**BTH_CENTER, dist=SEARCH_RADIUS)
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        # NB: `or []` and not `.get("ac", [])` -- the key is present but null
        # in some responses, which would otherwise yield None and crash the
        # caller's loop instead of counting as "no aircraft".
        return list(resp.json().get("ac") or []), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"


def collect_adsblol():
    """One snapshot from adsb.lol, retrying a *valid but empty* answer."""
    ac_list, err = None, None
    for attempt in range(1, ADSBLOL_EMPTY_RETRIES + 1):
        ac_list, err = _adsblol_once()
        if err is not None:
            print(f"  adsb.lol request FAILED ({attempt}/{ADSBLOL_EMPTY_RETRIES}): "
                  f"{err}")
        elif ac_list:
            break
        else:
            print(f"  adsb.lol returned 0 aircraft "
                  f"({attempt}/{ADSBLOL_EMPTY_RETRIES}) - upstream coverage loss, "
                  f"retrying in {ADSBLOL_RETRY_SLEEP:.0f}s")
        if attempt < ADSBLOL_EMPTY_RETRIES:
            time.sleep(ADSBLOL_RETRY_SLEEP)

    if err is not None:
        STATS["adsblol_fail"] += 1
        return 0
    if not ac_list:
        STATS["adsblol_empty"] += 1
        print(f"  adsb.lol WARNING: still 0 aircraft after "
              f"{ADSBLOL_EMPTY_RETRIES} attempts - the upstream feed for this "
              f"region is down (OpenSky is unaffected)")
        return 0
    STATS["adsblol_ok"] += 1

    ts = now_iso()
    rows = []
    for a in ac_list:
        if not a.get("hex"):
            continue
        row = {"fetch_ts": ts, "zone": "BTH"}
        for f in ADSB_FIELDS:
            if f in row:
                continue
            key = ADSB_MAP.get(f, f)      # wind_dir -> wd, wind_speed -> ws
            v = a.get(key)
            row[f] = v.strip() if isinstance(v, str) else v
        rows.append(row)

    path = os.path.join(DATA_DIR, "adsblol", f"adsblol_{file_tag()}.csv")
    append_rows(path, ADSB_FIELDS, rows)
    print(f"  adsb.lol: {len(rows)} aircraft -> {os.path.relpath(path, ROOT)}")
    return len(rows)


# ---------------------------------------------------------------- OpenSky
def get_opensky_token():
    cid = os.environ.get("OPENSKY_CLIENT_ID")
    sec = os.environ.get("OPENSKY_CLIENT_SECRET")
    if not (cid and sec):
        return None
    try:
        r = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials",
                  "client_id": cid, "client_secret": sec},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["access_token"]
    except Exception as e:
        print(f"  OpenSky token FAILED: {type(e).__name__}: {str(e)[:120]}")
        return None


def collect_opensky():
    token = get_opensky_token()
    if not token:
        STATS["opensky_skip"] += 1
        print("  OpenSky: skipped (no OPENSKY_CLIENT_ID/SECRET)")
        return 0

    try:
        r = requests.get(
            OPENSKY_STATES,
            params=BBOX,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        r.raise_for_status()
        states = r.json().get("states") or []
    except Exception as e:
        STATS["opensky_fail"] += 1
        print(f"  OpenSky FAILED: {type(e).__name__}: {str(e)[:120]}")
        return 0
    STATS["opensky_ok"] += 1

    ts = now_iso()
    rows = []
    for s in states:
        rows.append({
            "fetch_ts": ts,
            "icao24": s[0], "callsign": (s[1] or "").strip() or None,
            "origin_country": s[2], "time_position": s[3],
            "last_contact": s[4], "longitude": s[5], "latitude": s[6],
            "baro_altitude": s[7], "on_ground": s[8], "velocity": s[9],
            "true_track": s[10], "vertical_rate": s[11],
            "geo_altitude": s[13], "squawk": s[14],
        })

    path = os.path.join(DATA_DIR, "opensky", f"opensky_{file_tag()}.csv")
    append_rows(path, OPENSKY_FIELDS, rows)
    print(f"  OpenSky: {len(rows)} aircraft -> {os.path.relpath(path, ROOT)}")
    return len(rows)


def main():
    print(f"=== cloud_collect @ {now_iso()} ===")
    a = collect_adsblol()
    b = collect_opensky()
    print(f"=== done: adsb.lol={a}, opensky={b} ===")
    print(f"=== source stats: {STATS} ===")
    if a == 0 and b == 0:
        print("WARNING: no data collected this run")
    elif a == 0:
        print("WARNING: adsb.lol only -- this window has NO aircraft types "
              "(the noise model needs the 't' field)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
