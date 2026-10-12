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
    # Cloud runs write one file per commit window, e.g.
    # adsblol_20261010_0213_1234.csv, so match by DATE PREFIX, not exact name.
    pattern = f"adsblol_{date}*.csv" if date else "adsblol_*.csv"
    paths = [p for p in sorted(glob.glob(os.path.join(ADSB_DIR, pattern)))
             if os.path.exists(p)]
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


def dedup_opensky(df):
    """Collapse repeated state vectors so one observation is counted once.

    Two sources of repetition, both measured on the real corpus:

    1. **Within a single channel.** ``/states/all`` reports each aircraft's
       ``time_position`` = when its *position* was last updated, and that does
       not advance on every poll.  Consecutive 30 s polls therefore re-report
       the same position.  Measured 2026-10-12 over 91 files: 41,693 raw rows
       -> 29,935 unique, i.e. ~28% were redundant.
    2. **Across the two channels.** Since 2026-10-12 the campaign runs two
       independent collectors (GitHub Actions + Tencent SCF) against the same
       feeds, so the same observation also lands in two files.

    Left alone, Stage 1's SEL accumulation would count a single overflight
    twice and bias Lden upward -- the only way the second channel can corrupt
    the result.

    ``time_position`` is OpenSky's own absolute Unix timestamp for the last
    position update, so it identifies an observation exactly:
      * two polls that see the SAME time_position -> one observation, collapse;
      * two polls 30 s apart that see DIFFERENT time_positions -> two genuine
        samples, kept (that is resolution, not duplication).

    Never dedup on our own ``fetch_ts``: it differs between channels by design
    and would collapse nothing while looking like it worked.

    Which row of a group survives matters.  Measured across the duplicate
    groups: ``baro_altitude`` is identical in 100% of them (so the altitude the
    noise model needs is never at risk), but ``velocity`` differs in 29%,
    ``vertical_rate`` in 22% and ``geo_altitude`` in 20% -- these are "position
    unchanged, motion state refreshed" updates.  So we keep the row with the
    greatest ``last_contact`` (when the network last heard the aircraft) rather
    than an arbitrary first/last one.  Row order is preserved otherwise, since
    downstream stages read these trajectories in file order.
    """
    if df is None or df.empty:
        return df

    df = df.reset_index(drop=True)
    n0 = len(df)
    icao = df["icao24"].astype(str)
    tp = pd.to_numeric(df.get("time_position"), errors="coerce").round(0)
    key = icao + "|" + tp.astype("Int64").astype(str)

    missing = tp.isna()
    if missing.any():
        # No position timestamp: identical coordinates + altitude are the same
        # observation as far as this pipeline can tell, so key on those.
        fb = icao
        for col in ("longitude", "latitude", "baro_altitude"):
            if col in df.columns:
                fb = fb + "|" + df[col].round(5).astype(str)
        key = key.where(~missing, fb)

    if "last_contact" in df.columns:
        fresh = pd.to_numeric(df["last_contact"], errors="coerce").fillna(-1.0)
    else:
        fresh = pd.Series(-1.0, index=df.index)

    order = pd.DataFrame({"k": key.to_numpy(), "f": fresh.to_numpy(),
                          "i": df.index.to_numpy()})
    # stable sort -> among equal freshness the highest index (latest fetch) wins
    keep = (order.sort_values(["f", "i"], kind="stable")
                 .drop_duplicates(subset="k", keep="last")["i"]
                 .sort_values())
    out = df.iloc[keep.to_numpy()].reset_index(drop=True)

    n1 = len(out)
    if n1 != n0:
        print(f"  opensky dedup: {n0} -> {n1} rows "
              f"({n0 - n1} repeated state vectors collapsed)")
    return out


def load_opensky(date: str):
    pattern = f"opensky_{date}*.csv" if date else "opensky_*.csv"
    paths = [p for p in sorted(glob.glob(os.path.join(OSKY_DIR, pattern)))
             if os.path.exists(p)]
    if not paths:
        return None
    frames = [pd.read_csv(p, low_memory=False) for p in paths]
    return dedup_opensky(pd.concat(frames, ignore_index=True))


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
