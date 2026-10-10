#!/usr/bin/env python3
"""
Join adsb.lol aircraft metadata onto the OpenSky trajectory backbone.

Why this exists
---------------
Neither source is sufficient alone for airport noise assessment:
  - OpenSky : trajectory + vertical rate, but NO aircraft type
  - adsb.lol: aircraft type (t) + registration (r), but only discrete snapshots

Aircraft noise depends heavily on type (A320 vs B738 differ markedly), so the
two must be joined on ICAO24 to be usable.

Output units are metric throughout (metres, metres/second), matching OpenSky
native units. adsb.lol native values (feet, knots) are converted on ingestion.

Usage:
    python merge_sources.py                 # merge all available dates
    python merge_sources.py --date 20261009
"""
import argparse
import csv
import glob
import os
from datetime import datetime

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE_DIR, "..")
ADSB_DIR = os.path.join(ROOT, "data", "flights", "adsblol")
OSKY_DIR = os.path.join(ROOT, "data", "flights", "opensky")
OUT_DIR = os.path.join(ROOT, "data", "flights", "merged")

FT_TO_M = 0.3048
KT_TO_MS = 0.514444

MERGED_FIELDS = [
    "fetch_ts", "time_position", "icao24", "callsign",
    "aircraft_type", "registration", "category",
    "latitude", "longitude",
    "baro_altitude_m", "geo_altitude_m",
    "velocity_ms", "vertical_rate_ms", "true_track",
    "on_ground", "origin_country",
    "type_source",
]


def norm_icao(v):
    """Normalise an ICAO24 address for joining."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().lower()
    return s or None


def norm_callsign(v):
    """Normalise a callsign for joining (adsb.lol 'flight' == OpenSky 'callsign')."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().upper()
    return s or None


def load_adsb_lookup(date: str):
    """Build type registries from adsb.lol, indexed by ICAO24 and by callsign."""
    paths = ([os.path.join(ADSB_DIR, f"adsblol_{date}.csv")]
             if date else sorted(glob.glob(os.path.join(ADSB_DIR, "adsblol_*.csv"))))
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        return {}, {}, 0

    frames = [pd.read_csv(p, low_memory=False) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    df["_icao"] = df["hex"].map(norm_icao)
    df["_cs"] = df["flight"].map(norm_callsign) if "flight" in df else None
    df = df[df["_icao"].notna() | df["_cs"].notna()]

    by_icao, by_cs = {}, {}

    def record(df_sub, keycol, target):
        for key, grp in df_sub.groupby(keycol):
            if not key:
                continue
            typed = grp[grp["t"].notna()]
            src = typed if len(typed) else grp
            row = src.iloc[-1]
            rec = {
                "aircraft_type": row.get("t"),
                "registration": row.get("r"),
                "category": row.get("category"),
                "flight": row.get("flight"),
            }
            # do not overwrite an entry that already has a type
            old = target.get(key)
            if old is None or (old.get("aircraft_type") is None
                               and rec["aircraft_type"] is not None):
                target[key] = rec

    if "_icao" in df:
        record(df[df["_icao"].notna()], "_icao", by_icao)
    if "_cs" in df:
        record(df[df["_cs"].notna()], "_cs", by_cs)

    return by_icao, by_cs, len(df)


def load_opensky(date: str):
    paths = ([os.path.join(OSKY_DIR, f"opensky_{date}.csv")]
             if date else sorted(glob.glob(os.path.join(OSKY_DIR, "opensky_*.csv"))))
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        return None
    frames = [pd.read_csv(p, low_memory=False) for p in paths]
    return pd.concat(frames, ignore_index=True)


def save_registry(lookup):
    """Persist the cumulative ICAO24 -> type registry so coverage compounds."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "aircraft_types.csv")
    rows = [dict(icao24=k, **v) for k, v in sorted(lookup.items())]
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def merge(date: str):
    # Aircraft type is a STATIC property of the airframe, so the lookup is
    # built from ALL available adsb.lol data, not just the target date.
    # Coverage therefore compounds as collection days accumulate.
    by_icao, by_cs, n_adsb = load_adsb_lookup(None)
    if by_icao:
        print(f"Type registry             : {len(by_icao)} ICAO24 / {len(by_cs)} callsign keys")
        save_registry(by_icao)
    osky = load_opensky(date)

    if osky is None or len(osky) == 0:
        print("No OpenSky data found - nothing to merge.")
        return

    osky["_icao"] = osky["icao24"].map(norm_icao)
    osky["_cs"] = (osky["callsign"].map(norm_callsign)
                   if "callsign" in osky else [None] * len(osky))

    types, regs, cats, srcs = [], [], [], []
    matched = 0
    via_cs = 0
    for icao, cs in zip(osky["_icao"], osky["_cs"]):
        m = by_icao.get(icao) if icao else None
        how = "opensky+adsblol(icao24)"
        if m is None and cs:            # fallback: same flight number
            m = by_cs.get(cs)
            if m is not None:
                how = "opensky+adsblol(callsign)"
                via_cs += 1
        if m:
            matched += 1
            types.append(m["aircraft_type"])
            regs.append(m["registration"])
            cats.append(m["category"])
            srcs.append(how)
        else:
            types.append(None)
            regs.append(None)
            cats.append(None)
            srcs.append("opensky")

    out = pd.DataFrame({
        "fetch_ts": osky["fetch_ts"],
        "time_position": osky.get("time_position"),
        "icao24": osky["_icao"],
        "callsign": osky.get("callsign"),
        "aircraft_type": types,
        "registration": regs,
        "category": cats,
        "latitude": osky.get("latitude"),
        "longitude": osky.get("longitude"),
        "baro_altitude_m": osky.get("baro_altitude"),
        "geo_altitude_m": osky.get("geo_altitude"),
        "velocity_ms": osky.get("velocity"),
        "vertical_rate_ms": osky.get("vertical_rate"),
        "true_track": osky.get("true_track"),
        "on_ground": osky.get("on_ground"),
        "origin_country": osky.get("origin_country"),
        "type_source": srcs,
    })

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = date or datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(OUT_DIR, f"merged_{stamp}.csv")
    out.to_csv(out_path, index=False, quoting=csv.QUOTE_MINIMAL)

    n = len(out)
    cov = 100.0 * matched / n if n else 0.0
    n_typed = int(out["aircraft_type"].notna().sum())

    print(f"OpenSky trajectory rows : {n}")
    print(f"adsb.lol metadata rows  : {n_adsb}")
    print(f"Matched via ICAO24      : {matched - via_cs}")
    print(f"Matched via callsign    : {via_cs}  (fallback)")
    print(f"Total matched           : {matched} ({cov:.1f}%)")
    print(f"Rows with aircraft type : {n_typed} ({100.0*n_typed/n if n else 0:.1f}%)")
    print(f"Output                  : {out_path}")

    if cov < 50:
        print("\nWARNING: low type coverage. Trajectories without a type cannot be")
        print("used for noise modelling - see notes below.")

    # show what is still missing
    miss = out[out["aircraft_type"].isna()]
    if len(miss):
        print(f"\nUntyped sample (first 5 of {len(miss)}):")
        print(miss[["fetch_ts", "icao24", "callsign"]].head(5).to_string(index=False))


def main():
    ap = argparse.ArgumentParser(description="Join adsb.lol type info onto OpenSky tracks")
    ap.add_argument("--date", default=None, help="YYYYMMDD; default = merge all dates")
    args = ap.parse_args()
    print("=== merge_sources: adsb.lol(type) -> OpenSky(track) ===")
    merge(args.date)


if __name__ == "__main__":
    main()
