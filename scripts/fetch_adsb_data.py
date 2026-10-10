#!/usr/bin/env python3
"""
ADS-B Flight Data Collector for BTH MAR Noise Study
====================================================
Fetches historical flight trajectory data from OpenSky Network
for Beijing Capital (ZBAA), Beijing Daxing (ZBAD), and
Tianjin Binhai (ZBTJ) airports.

Usage:
    python fetch_adsb_data.py --start 2025-01-01 --end 2025-01-07
    python fetch_adsb_data.py --airport ZBAA --start 2025-06-01 --end 2025-06-30 --tracks

Dependencies:
    requests, pandas, numpy, python-dateutil
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from dateutil.parser import parse as parse_date

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# ICAO codes for BTH MAR airports
AIRPORTS = {
    "ZBAA": "Beijing Capital (PEK)",
    "ZBAD": "Beijing Daxing (PKX)",
    "ZBTJ": "Tianjin Binhai (TSN)",
}

# OpenSky API endpoints
OPENSKY_BASE = "https://opensky-network.org/api"
FLIGHTS_AIRPORT_URL = f"{OPENSKY_BASE}/flights/airport"
TRACKS_URL = f"{OPENSKY_BASE}/tracks/all"

# Rate limiting: anonymous access allows 1 request per 10 seconds
# Registered users get 1 request per 5 seconds (register at opensky-network.org)
REQUEST_INTERVAL = 10  # seconds

# Output directory
DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "adsb")

# Columns for flight list output
FLIGHT_COLUMNS = [
    "icao24", "callsign", "departure_airport", "arrival_airport",
    "first_seen", "last_seen", "est_departure_airport", "est_arrival_airport"
]

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("adsb-fetcher")

# ---------------------------------------------------------------------------
# API Helpers
# ---------------------------------------------------------------------------

class OpenSkyRateLimiter:
    """Simple rate limiter for OpenSky API calls."""
    
    def __init__(self, interval: float = REQUEST_INTERVAL):
        self.interval = interval
        self._last_call = 0.0
    
    def wait(self):
        elapsed = time.time() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_call = time.time()


def fetch_flights_for_airport(
    airport: str,
    start_ts: int,
    end_ts: int,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> List[Dict]:
    """
    Fetch flight list for a given airport and time window.
    
    Args:
        airport: ICAO airport code (e.g., "ZBAA")
        start_ts: Unix timestamp (seconds since epoch)
        end_ts: Unix timestamp (seconds since epoch)
        username: OpenSky username (optional, for higher rate limits)
        password: OpenSky password (optional)
    
    Returns:
        List of flight dictionaries
    """
    params = {
        "airport": airport,
        "begin": start_ts,
        "end": end_ts,
    }
    
    auth = (username, password) if username and password else None
    
    try:
        resp = requests.get(
            FLIGHTS_AIRPORT_URL,
            params=params,
            auth=auth,
            timeout=60,
        )
        resp.raise_for_status()
        flights = resp.json()
        log.info(f"  Retrieved {len(flights)} flights for {airport} "
                 f"[{datetime.fromtimestamp(start_ts).date()} - "
                 f"{datetime.fromtimestamp(end_ts).date()}]")
        return flights
    except requests.exceptions.RequestException as e:
        log.error(f"  API error for {airport}: {e}")
        return []
    except json.JSONDecodeError as e:
        log.error(f"  JSON decode error for {airport}: {e}")
        return []


def fetch_track(
    icao24: str,
    timestamp: int,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Optional[Dict]:
    """
    Fetch track (trajectory) for a specific aircraft at a given time.
    
    Args:
        icao24: ICAO 24-bit address of the aircraft
        timestamp: Unix timestamp
        username: OpenSky username (optional)
        password: OpenSky password (optional)
    
    Returns:
        Track dictionary or None on failure
    """
    params = {
        "icao24": icao24,
        "time": timestamp,
    }
    
    auth = (username, password) if username and password else None
    
    try:
        resp = requests.get(
            TRACKS_URL,
            params=params,
            auth=auth,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.debug(f"Track fetch failed for {icao24} @ {timestamp}: {e}")
        return None


def timestamp_to_dt(ts: int) -> datetime:
    """Convert Unix timestamp to UTC datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def dt_to_timestamp(dt: datetime) -> int:
    """Convert datetime to Unix timestamp."""
    return int(dt.timestamp())

# ---------------------------------------------------------------------------
# Data Processing
# ---------------------------------------------------------------------------

def flights_to_dataframe(flights: List[Dict]) -> pd.DataFrame:
    """Convert flight list to a cleaned DataFrame."""
    if not flights:
        return pd.DataFrame(columns=FLIGHT_COLUMNS)
    
    df = pd.DataFrame(flights)
    
    # Select and rename columns
    keep_cols = [c for c in FLIGHT_COLUMNS if c in df.columns]
    df = df[keep_cols]
    
    # Convert timestamps
    if "first_seen" in df.columns:
        df["first_seen_dt"] = pd.to_datetime(df["first_seen"], unit="s", utc=True)
    if "last_seen" in df.columns:
        df["last_seen_dt"] = pd.to_datetime(df["last_seen"], unit="s", utc=True)
    
    # Clean callsign (strip whitespace, uppercase)
    if "callsign" in df.columns:
        df["callsign"] = df["callsign"].str.strip().str.upper()
    
    return df


def split_by_direction(df: pd.DataFrame, airport: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split flight data into departures from and arrivals to the given airport.
    
    Returns:
        (departures, arrivals) DataFrames
    """
    if "est_departure_airport" in df.columns and "est_arrival_airport" in df.columns:
        deps = df[df["est_departure_airport"] == airport].copy()
        arrs = df[df["est_arrival_airport"] == airport].copy()
    elif "departure_airport" in df.columns and "arrival_airport" in df.columns:
        deps = df[df["departure_airport"] == airport].copy()
        arrs = df[df["arrival_airport"] == airport].copy()
    else:
        return df, pd.DataFrame()
    
    return deps, arrs


def generate_flight_summary(df: pd.DataFrame) -> Dict:
    """Generate summary statistics for a flight DataFrame."""
    summary = {
        "total_flights": len(df),
        "unique_callsigns": df["callsign"].nunique() if "callsign" in df.columns else 0,
        "unique_aircraft": df["icao24"].nunique() if "icao24" in df.columns else 0,
        "time_span_days": 0,
    }
    
    if "first_seen_dt" in df.columns and not df.empty:
        summary["time_span_days"] = (
            df["first_seen_dt"].max() - df["first_seen_dt"].min()
        ).days
    
    return summary

# ---------------------------------------------------------------------------
# Main Collection Workflow
# ---------------------------------------------------------------------------

def collect_flights_batch(
    airport: str,
    start_date: datetime,
    end_date: datetime,
    days_per_chunk: int = 1,
    username: Optional[str] = None,
    password: Optional[str] = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    save_tracks: bool = False,
) -> str:
    """
    Collect flight data for an airport over a date range.
    
    Processes data in daily chunks to respect API rate limits and
    enable incremental progress saving.
    
    Returns:
        Path to the output CSV file.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    rate_limiter = OpenSkyRateLimiter()
    all_flights = []
    current_date = start_date
    chunk_count = 0
    
    # Sanitize airport code for filename
    apt_name = airport.replace("/", "_")
    date_str = f"{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"
    output_file = os.path.join(output_dir, f"flights_{apt_name}_{date_str}.csv")
    
    # Check for existing partial output (resume support)
    existing_flights = set()
    if os.path.exists(output_file):
        try:
            existing_df = pd.read_csv(output_file)
            existing_flights = set(zip(
                existing_df.get("first_seen", []),
                existing_df.get("icao24", []),
                existing_df.get("callsign", []),
            ))
            log.info(f"Found existing file with {len(existing_df)} flights, will skip duplicates")
        except Exception:
            log.warning(f"Could not read existing file {output_file}, starting fresh")
    
    total_new = 0
    
    while current_date < end_date:
        chunk_start = current_date
        chunk_end = min(current_date + timedelta(days=days_per_chunk), end_date)
        
        start_ts = dt_to_timestamp(chunk_start)
        end_ts = dt_to_timestamp(chunk_end)
        
        log.info(f"[{apt_name}] Fetching chunk: "
                 f"{chunk_start.date()} to {chunk_end.date()} "
                 f"({(end_ts - start_ts) // 3600}h window)")
        
        rate_limiter.wait()
        flights = fetch_flights_for_airport(airport, start_ts, end_ts, username, password)
        
        if flights:
            # Filter out duplicates
            new_flights = []
            for f in flights:
                key = (f.get("first_seen"), f.get("icao24"), f.get("callsign"))
                if key not in existing_flights:
                    existing_flights.add(key)
                    new_flights.append(f)
            
            all_flights.extend(new_flights)
            total_new += len(new_flights)
            log.info(f"  New: {len(new_flights)} / Total: {len(all_flights)}")
        
        chunk_count += 1
        current_date = chunk_end
        
        # Incremental save every 5 chunks
        if chunk_count % 5 == 0 and all_flights:
            log.info(f"  Incremental save ({len(all_flights)} flights)...")
            df = flights_to_dataframe(all_flights)
            df.to_csv(output_file, index=False)
    
    # Final save
    if all_flights:
        df = flights_to_dataframe(all_flights)
        
        # If existing, merge
        if os.path.exists(output_file):
            try:
                existing_df = pd.read_csv(output_file)
                df = pd.concat([existing_df, df], ignore_index=True)
                df = df.drop_duplicates(subset=["first_seen", "icao24", "callsign"])
            except Exception:
                pass
        
        df.to_csv(output_file, index=False)
        log.info(f"Saved {len(df)} flights to {output_file}")
        
        # Print summary
        summary = generate_flight_summary(df)
        log.info(f"Summary for {apt_name}:")
        for k, v in summary.items():
            log.info(f"  {k}: {v}")
    else:
        log.warning(f"No new flights collected for {apt_name}")
    
    return output_file


def collect_all_airports(
    start_date: datetime,
    end_date: datetime,
    days_per_chunk: int = 1,
    username: Optional[str] = None,
    password: Optional[str] = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    save_tracks: bool = False,
):
    """Collect flight data for all three BTH MAR airports."""
    
    results = {}
    for icao, name in AIRPORTS.items():
        log.info(f"{'='*60}")
        log.info(f"Processing {icao} ({name})")
        log.info(f"{'='*60}")
        
        filepath = collect_flights_batch(
            airport=icao,
            start_date=start_date,
            end_date=end_date,
            days_per_chunk=days_per_chunk,
            username=username,
            password=password,
            output_dir=output_dir,
            save_tracks=save_tracks,
        )
        results[icao] = filepath
        
        # Stagger between airports to be courteous
        if icao != list(AIRPORTS.keys())[-1]:
            log.info("Cooling down before next airport...")
            time.sleep(REQUEST_INTERVAL)
    
    log.info(f"{'='*60}")
    log.info("Collection complete!")
    log.info(f"{'='*60}")
    
    for icao, path in results.items():
        log.info(f"  {icao} ({AIRPORTS[icao]}): {path}")

# ---------------------------------------------------------------------------
# Analysis Utilities
# ---------------------------------------------------------------------------

def load_flights(airport_code: str, data_dir: str = DEFAULT_OUTPUT_DIR) -> pd.DataFrame:
    """Load flight CSV data for a given airport."""
    pattern = f"flights_{airport_code}_*.csv"
    import glob
    files = sorted(glob.glob(os.path.join(data_dir, pattern)))
    
    if not files:
        log.warning(f"No data files found for {airport_code} in {data_dir}")
        return pd.DataFrame()
    
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f)
            if not df.empty:
                dfs.append(df)
                log.info(f"Loaded {len(df)} rows from {os.path.basename(f)}")
        except Exception as e:
            log.warning(f"Could not load {f}: {e}")
    
    if not dfs:
        return pd.DataFrame()
    
    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset=["first_seen", "icao24", "callsign"])
    log.info(f"Combined: {len(combined)} unique flights")
    return combined


def print_data_summary(data_dir: str = DEFAULT_OUTPUT_DIR):
    """Print a summary of all collected data."""
    import glob
    
    log.info(f"{'='*60}")
    log.info("Data Summary")
    log.info(f"{'='*60}")
    
    for icao, name in AIRPORTS.items():
        pattern = f"flights_{icao}_*.csv"
        files = sorted(glob.glob(os.path.join(data_dir, pattern)))
        
        if not files:
            log.info(f"{icao} ({name}): No data")
            continue
        
        total_rows = 0
        for f in files:
            try:
                df = pd.read_csv(f)
                total_rows += len(df)
            except Exception:
                pass
        
        # Split by direction
        df = load_flights(icao, data_dir)
        if not df.empty:
            deps, arrs = split_by_direction(df, icao)
            log.info(f"{icao} ({name}): {total_rows} flights total "
                     f"(~{len(deps)} departures, ~{len(arrs)} arrivals)")
        else:
            log.info(f"{icao} ({name}): {total_rows} flights total")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect ADS-B flight data from OpenSky Network for BTH MAR airports",
    )
    
    parser.add_argument(
        "--airport", type=str, default=None,
        choices=["ZBAA", "ZBAD", "ZBTJ", "all"],
        help="Airport ICAO code (default: all three BTH airports)"
    )
    
    parser.add_argument(
        "--start", type=str, default="2025-01-01",
        help="Start date (YYYY-MM-DD, default: 2025-01-01)"
    )
    
    parser.add_argument(
        "--end", type=str, default="2025-01-07",
        help="End date (YYYY-MM-DD, default: 2025-01-07)"
    )
    
    parser.add_argument(
        "--chunk-days", type=int, default=1,
        help="Days per API chunk (default: 1)"
    )
    
    parser.add_argument(
        "--output-dir", type=str, default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})"
    )
    
    parser.add_argument(
        "--username", type=str, default=None,
        help="OpenSky username (optional, for higher rate limits)"
    )
    
    parser.add_argument(
        "--password", type=str, default=None,
        help="OpenSky password (optional)"
    )
    
    parser.add_argument(
        "--summary", action="store_true",
        help="Print summary of existing data and exit"
    )
    
    parser.add_argument(
        "--tracks", action="store_true",
        help="Also fetch detailed track data (WARNING: very slow)"
    )
    
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose logging"
    )
    
    return parser.parse_args()


def main():
    args = parse_args()
    
    if args.verbose:
        log.setLevel(logging.DEBUG)
    
    if args.summary:
        print_data_summary(args.output_dir)
        return
    
    try:
        start_date = parse_date(args.start).replace(tzinfo=timezone.utc)
        end_date = parse_date(args.end).replace(tzinfo=timezone.utc)
    except Exception as e:
        log.error(f"Invalid date format: {e}")
        sys.exit(1)
    
    if end_date <= start_date:
        log.error("End date must be after start date")
        sys.exit(1)
    
    if (end_date - start_date).days > 60:
        log.warning(
            f"Requested period is {(end_date - start_date).days} days. "
            f"This may take a very long time. Consider reducing the range."
        )
    
    airports_to_collect = [args.airport] if args.airport and args.airport != "all" else list(AIRPORTS.keys())
    
    for icao in airports_to_collect:
        if icao not in AIRPORTS:
            log.error(f"Unknown airport: {icao}")
            continue
        
        log.info(f"\n{'='*60}")
        log.info(f"Collecting data for {icao} ({AIRPORTS[icao]})")
        log.info(f"Period: {start_date.date()} to {end_date.date()}")
        log.info(f"{'='*60}")
        
        collect_flights_batch(
            airport=icao,
            start_date=start_date,
            end_date=end_date,
            days_per_chunk=args.chunk_days,
            username=args.username,
            password=args.password,
            output_dir=args.output_dir,
            save_tracks=args.tracks,
        )
    
    # Print final summary
    print_data_summary(args.output_dir)


if __name__ == "__main__":
    main()