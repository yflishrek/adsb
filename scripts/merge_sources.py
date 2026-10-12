#!/usr/bin/env python3
"""
Build the trajectory backbone for the BTH airport noise study.

Both feeds are trajectory sources
---------------------------------
Earlier revisions treated OpenSky as the track and adsb.lol purely as an
aircraft-type lookup. Measured 2026-10-12 that is wrong: adsb.lol carries a full
state vector (lat/lon 100%, alt_baro 100%, gs/track 97.6%, baro_rate 96.9%), and
OpenSky's receiver network for the Beijing area dropped out completely for hours
(0 aircraft inside the study box while 206 targets remained visible elsewhere
over China; adsb.lol still reported 12 aircraft in the same box). Neither feed
is dependable on its own, and they fail independently, so:

  1. both are loaded as trajectories,
  2. each is restricted to the same study box (their query footprints differ --
     OpenSky is a lat/lon box, adsb.lol an 80 nm RADIUS around the centroid),
  3. an observation window (default 60 s) picks whichever feed saw more of the
     airspace, and
  4. the winner's rows form the backbone for that window.

Ties go to OpenSky, deterministically, and the per-window verdict is written to
``backbone_coverage_<date>.csv`` so the choice is auditable.

Aircraft type is still needed -- noise depends heavily on it (A320 vs B738
differ markedly) -- and is resolved per row: adsb.lol backbone rows already
carry ``t``, OpenSky rows are joined on ICAO24 (falling back to callsign).

Output units are metric throughout (metres, metres/second). adsb.lol's native
feet/knots/feet-per-minute are converted here, once.

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
    "source", "type_source",
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


def dedup_state_vectors(df, fresh_col="last_contact", label="state vectors"):
    """Collapse repeated state vectors so one observation is counted once.

    Two sources of repetition, both measured on the real corpus:

    1. **Within a single channel.** OpenSky's ``/states/all`` reports each
       aircraft's ``time_position`` = when its *position* was last updated, and
       that does not advance on every poll.  Consecutive 30 s polls therefore
       re-report the same position.  Measured 2026-10-12 over 91 files: 41,693
       raw rows -> 29,935 unique, i.e. ~28% were redundant.  adsb.lol behaves
       the same way once its ``seen_pos`` is turned into an absolute position
       time.
    2. **Across channels.** Two independent collectors (Tencent SCF, and
       previously GitHub Actions) poll the same feeds, so the same observation
       also lands in two files.

    Left alone, Stage 1's SEL accumulation would count a single overflight
    twice and bias Lden upward.

    The observation key is ``(icao24, time_position)``:
      * two polls that see the SAME time_position -> one observation, collapse;
      * two polls 30 s apart that see DIFFERENT time_positions -> two genuine
        samples, kept (that is resolution, not duplication).

    Never dedup on our own fetch timestamp: it differs between channels by
    design and would collapse nothing while looking like it worked.

    Which row of a group survives matters.  For OpenSky, ``baro_altitude`` is
    identical in 100% of the duplicate groups (so the altitude the noise model
    needs is never at risk), but ``velocity`` differs in 29%, ``vertical_rate``
    in 22% and ``geo_altitude`` in 20% -- these are "position unchanged, motion
    state refreshed" updates.  So we keep the row with the greatest freshness
    value rather than an arbitrary first/last one.  Row order is preserved
    otherwise, since downstream stages read trajectories in file order.

    `fresh_col` names the per-source freshness column: ``last_contact`` for
    OpenSky (when the network last heard the aircraft), ``fetch_ts_epoch`` for
    adsb.lol (which has no equivalent field).
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

    if fresh_col in df.columns:
        fresh = pd.to_numeric(df[fresh_col], errors="coerce").fillna(-1.0)
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
        print(f"  {label} dedup: {n0} -> {n1} rows "
              f"({n0 - n1} repeated state vectors collapsed)")
    return out


def load_opensky(date: str):
    pattern = f"opensky_{date}*.csv" if date else "opensky_*.csv"
    paths = [p for p in sorted(glob.glob(os.path.join(OSKY_DIR, pattern)))
             if os.path.exists(p)]
    if not paths:
        return None
    frames = [pd.read_csv(p, low_memory=False) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    df["_source"] = "opensky"
    return dedup_state_vectors(df, "last_contact", "opensky")


# ------------------------------------------------------- adsb.lol as a track
def _to_num(series):
    return pd.to_numeric(series, errors="coerce")


def load_adsblol_tracks(date: str):
    """Reshape adsb.lol snapshots into the same trajectory schema as OpenSky.

    adsb.lol is not just a type registry: measured 2026-10-12 it carries a full
    state vector (``lat``/``lon`` 100%, ``alt_baro`` 100%, ``gs``/``track``
    97.6%, ``baro_rate`` 96.9%), which makes it a viable trajectory backbone in
    its own right.  That matters because OpenSky's receiver network for the
    Beijing area went completely dark for hours on 2026-10-12 (continuous
    probes returned 0 aircraft inside the study bbox while 206 targets were
    visible elsewhere over China), and a study that depends on one feed
    inherits that hole.

    Unit conversion is mandatory and is done here, once:
      * ``alt_baro`` / ``alt_geom`` are FEET.  Verified per-aircraft against
        OpenSky on the same window: the paired ratio is 3.281 to three decimals
        (e.g. OpenSky 11308 m <-> adsb.lol 37100 ft).
      * ``gs`` is knots, ``baro_rate`` is feet per minute.
      * ``seen_pos`` is "seconds since the position was last updated", so the
        absolute position time is ``fetch_ts - seen_pos`` -- that is what makes
        adsb.lol rows comparable with OpenSky's ``time_position`` and therefore
        dedupable against them.

    ``alt_baro`` also carries the literal string ``"ground"`` rather than a
    number when the aircraft is on the surface.  Coercing that to a number would
    leave NaN indistinguishable from a parse failure, so the flag is kept
    explicitly: such rows get ``on_ground=True`` and no altitude (NaN, i.e. "no
    meaningful altitude"), which is also what excludes them from the study
    domain -- a taxiing aircraft contributes no overflight noise.
    """
    pattern = f"adsblol_{date}*.csv" if date else "adsblol_*.csv"
    paths = [p for p in sorted(glob.glob(os.path.join(ADSB_DIR, pattern)))
             if os.path.exists(p)]
    if not paths:
        return None

    df = pd.concat([pd.read_csv(p, low_memory=False) for p in paths],
                   ignore_index=True)

    raw_alt = df["alt_baro"].astype(str).str.strip().str.lower()
    on_ground = raw_alt.eq("ground")
    alt_ft = _to_num(df["alt_baro"].where(~on_ground))

    # Epoch seconds, computed WITHOUT assuming the datetime64 unit.
    # `pd.to_datetime(...).astype("int64")` is a trap: pandas >= 2.0 infers the
    # unit from the input, and our `fetch_ts` carries six fractional digits, so
    # it comes back as datetime64[us].  The `.astype("int64") / 1e9` that looks
    # right then yields 1,791,705 instead of 1,791,705,489 -- a silent 1000x
    # error that put every adsb.lol row in a 1970 window and made the source
    # comparison meaningless.  Subtracting the epoch is unit-agnostic.
    dt = pd.to_datetime(df["fetch_ts"], errors="coerce", utc=True)
    fetch_epoch = (dt - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    seen = _to_num(df.get("seen_pos"))
    pos_time = (fetch_epoch - seen).round(0)

    out = pd.DataFrame({
        "_source": "adsblol",
        "fetch_ts": df["fetch_ts"],
        "_fresh": fetch_epoch,
        "icao24": df["hex"].map(norm_icao),
        "callsign": df.get("flight").map(norm_callsign) if "flight" in df else None,
        "origin_country": None,
        "time_position": pos_time,
        "last_contact": None,
        "latitude": _to_num(df.get("lat")),
        "longitude": _to_num(df.get("lon")),
        "baro_altitude": alt_ft * FT_TO_M,
        "on_ground": on_ground,
        "velocity": _to_num(df.get("gs")) * KT_TO_MS,
        "true_track": _to_num(df.get("track")),
        "vertical_rate": _to_num(df.get("baro_rate")) * FT_TO_M / 60.0,
        "geo_altitude": _to_num(df.get("alt_geom")) * FT_TO_M,
        "squawk": df.get("squawk"),
        # intrinsic to this source -- no join needed when it is the backbone
        "aircraft_type": df.get("t"),
        "registration": df.get("r"),
        "category": df.get("category"),
    })
    out = out[out["icao24"].notna()]
    return dedup_state_vectors(out, "_fresh", "adsb.lol")


# ------------------------------------------------------- backbone selection
# Footprints differ: adsb.lol answers a 80 nm RADIUS query around the BTH
# centroid (a circle, so it returns targets far to the west at 115.3 E) while
# OpenSky is queried with a lat/lon BOX.  Counting raw rows would therefore
# compare two different areas.  Both are restricted to the study box so the
# "which feed is more complete" verdict is about the same airspace.
BBOX = {"lamin": 39.0, "lamax": 40.7, "lomin": 116.0, "lomax": 118.0}
WINDOW_S = int(os.environ.get("BACKBONE_WINDOW_S") or 60)


def _in_domain(df):
    """Rows with a usable position+altitude inside the study box."""
    if df is None or df.empty:
        return df
    lat, lon, alt = _to_num(df["latitude"]), _to_num(df["longitude"]), _to_num(df["baro_altitude"])
    return df[lat.between(BBOX["lamin"], BBOX["lamax"])
              & lon.between(BBOX["lomin"], BBOX["lomax"])
              & alt.notna() & alt.gt(0)]


def _windows(df, window_s=WINDOW_S):
    """Assign each observation to a time window from its position timestamp."""
    tp = _to_num(df["time_position"])
    return (tp // window_s).astype("Int64")


def select_backbone(osky, adsb, window_s=WINDOW_S):
    """Pick, per time window, whichever feed observed more of the airspace.

    The two feeds fail independently -- OpenSky lost its Beijing receivers
    entirely for hours while adsb.lol kept reporting, and adsb.lol has had its
    own multi-hour coverage gaps (18 empty windows on 2026-10-11).  Choosing a
    fixed backbone therefore guarantees holes; choosing per window does not.

    Returns ``(backbone, report)``:
      * ``backbone`` -- the selected rows, tagged with a ``source`` column.
      * ``report``   -- per-window counts and the winning feed, saved alongside
        the merged output so the choice is auditable rather than implicit.

    Source names are assigned here rather than trusted from the caller, so the
    function cannot silently mislabel a feed if one of the loaders changes.
    """
    frames = []
    for name, f in (("opensky", osky), ("adsblol", adsb)):
        d = _in_domain(f)
        if d is None or d.empty:
            continue
        d = d.copy()
        d["_source"] = name
        d["window"] = _windows(d, window_s)
        frames.append(d)

    if not frames:
        return None, None

    allrows = pd.concat(frames, ignore_index=True)
    counts = (allrows.groupby(["window", "_source"], dropna=False)
                     .size().unstack(fill_value=0))
    for col in ("opensky", "adsblol"):
        if col not in counts:
            counts[col] = 0

    # Ties go to OpenSky: it is the reference feed for vertical rate, and the
    # rule has to be deterministic to be reproducible.
    counts["winner"] = ["opensky" if o >= a else "adsblol"
                        for o, a in zip(counts["opensky"], counts["adsblol"])]

    winner = counts["winner"].to_dict()
    allrows["source"] = allrows["_source"]
    keep = allrows["window"].map(winner) == allrows["source"]
    backbone = allrows[keep].drop(columns=["window"]).reset_index(drop=True)

    report = counts.reset_index().rename(columns={"index": "window"})
    report = report[["window", "opensky", "adsblol", "winner"]]
    report["window_start"] = pd.to_datetime(
        report["window"].astype("Int64") * window_s, unit="s", utc=True)
    return backbone, report


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
    adsb = load_adsblol_tracks(date)
    n_osky = 0 if osky is None else len(osky)
    n_adsb_tr = 0 if adsb is None else len(adsb)
    print(f"Trajectory candidates     : opensky={n_osky} rows "
          f"({0 if osky is None else osky['icao24'].nunique()} aircraft), "
          f"adsb.lol={n_adsb_tr} rows "
          f"({0 if adsb is None else adsb['icao24'].nunique()} aircraft)")

    backbone, report = select_backbone(osky, adsb)
    if backbone is None or backbone.empty:
        print("No trajectory data inside the study box - nothing to merge.")
        return

    n_win = len(report)
    win_osky = int((report["winner"] == "opensky").sum())
    win_adsb = int((report["winner"] == "adsblol").sum())
    print(f"Windows                   : {n_win} total -> "
          f"{win_osky} from OpenSky, {win_adsb} from adsb.lol")

    backbone = backbone.copy()
    backbone["_icao"] = backbone["icao24"].map(norm_icao)
    backbone["_cs"] = (backbone["callsign"].map(norm_callsign)
                       if "callsign" in backbone else None)

    types, regs, cats, srcs = [], [], [], []
    matched = via_cs = 0

    def col(name):
        """Column as a list, or Nones when the backbone lacks it entirely.

        (adsb.lol rows carry aircraft_type/registration; OpenSky rows do not,
        and a date with only one source never gets the column at all.)
        """
        if name in backbone:
            return backbone[name].tolist()
        return [None] * len(backbone)

    own_types, own_regs = col("aircraft_type"), col("registration")
    own_cats = col("category")
    icaos, css = col("_icao"), col("_cs")
    sources = backbone["source"].tolist()

    # Deliberately NOT itertuples(): pandas renames columns whose names start
    # with an underscore to positional placeholders, so row._icao raises.
    for own_t, own_r, own_c, icao, cs, src in zip(
            own_types, own_regs, own_cats, icaos, css, sources):
        # adsb.lol backbone rows already carry the type; nothing to join.
        if isinstance(own_t, str) and own_t.strip():
            types.append(own_t.strip())
            regs.append(own_r)
            cats.append(own_c)
            srcs.append("adsblol")
            continue

        m = by_icao.get(icao) if icao else None
        how = f"{src}+adsblol(icao24)"
        if m is None and cs:            # fallback: same flight number
            m = by_cs.get(cs)
            if m is not None:
                how = f"{src}+adsblol(callsign)"
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
            srcs.append(src)

    out = pd.DataFrame({
        "fetch_ts": backbone["fetch_ts"],
        "time_position": backbone.get("time_position"),
        "icao24": backbone["_icao"],
        "callsign": backbone.get("callsign"),
        "aircraft_type": types,
        "registration": regs,
        "category": cats,
        "latitude": backbone.get("latitude"),
        "longitude": backbone.get("longitude"),
        "baro_altitude_m": backbone.get("baro_altitude"),
        "geo_altitude_m": backbone.get("geo_altitude"),
        "velocity_ms": backbone.get("velocity"),
        "vertical_rate_ms": backbone.get("vertical_rate"),
        "true_track": backbone.get("true_track"),
        "on_ground": backbone.get("on_ground"),
        "origin_country": backbone.get("origin_country"),
        "source": backbone["source"],
        "type_source": srcs,
    })

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = date or datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(OUT_DIR, f"merged_{stamp}.csv")
    out.to_csv(out_path, index=False, quoting=csv.QUOTE_MINIMAL)

    # Persist the per-window decision so "which feed was used, and when" is
    # reproducible from the artefact rather than from a log line.
    rep_path = os.path.join(OUT_DIR, f"backbone_coverage_{stamp}.csv")
    report.to_csv(rep_path, index=False)

    n = len(out)
    cov = 100.0 * matched / n if n else 0.0
    n_typed = int(out["aircraft_type"].notna().sum())
    used = out["source"].value_counts().to_dict()

    print(f"Backbone rows             : {n}")
    print(f"  from OpenSky            : {used.get('opensky', 0)}")
    print(f"  from adsb.lol           : {used.get('adsblol', 0)}")
    print(f"Matched via ICAO24        : {matched - via_cs}")
    print(f"Matched via callsign      : {via_cs}  (fallback)")
    print(f"Total type-joined         : {matched} ({cov:.1f}% of rows needed a join)")
    print(f"Rows with aircraft type   : {n_typed} ({100.0*n_typed/n if n else 0:.1f}%)")
    print(f"Output                    : {out_path}")
    print(f"Coverage report           : {rep_path}")

    if cov < 50 and matched:
        print("\nWARNING: low type coverage. Trajectories without a type cannot be")
        print("used for noise modelling - see notes below.")

    # show what is still missing
    miss = out[out["aircraft_type"].isna()]
    if len(miss):
        print(f"\nUntyped sample (first 5 of {len(miss)}):")
        print(miss[["fetch_ts", "icao24", "callsign", "source"]].head(5).to_string(index=False))


def main():
    ap = argparse.ArgumentParser(
        description="Build the trajectory backbone from OpenSky + adsb.lol")
    ap.add_argument("--date", default=None, help="YYYYMMDD; default = merge all dates")
    args = ap.parse_args()
    print("=== merge_sources: per-window backbone selection (opensky | adsb.lol) ===")
    merge(args.date)


if __name__ == "__main__":
    main()
