#!/usr/bin/env python3
"""
AviationStack Flight Data Collector for BTH MAR Noise Study
============================================================
Fetches historical flight data for BTH (Beijing-Tianjin-Hebei)
Multi-Airport Region airports via AviationStack REST API.

Target Airports:
  - ZBAA (Beijing Capital International)
  - ZBAD (Beijing Daxing International)
  - ZBTJ (Tianjin Binhai International)

Data Collected per Flight:
  - Flight number (IATA & ICAO)
  - Aircraft type (ICAO code) — critical for noise modeling
  - Departure/arrival airports (ICAO codes)
  - Scheduled/estimated/actual times
  - Flight status
  - Terminal & gate info

Usage:
    # Set your API key (get one free at https://aviationstack.com)
    export AVIATIONSTACK_KEY="your_access_key_here"
    
    # Fetch 2 weeks of data for all BTH airports (default)
    python fetch_aviationstack.py
    
    # Custom date range
    python fetch_aviationstack.py --start 2025-01-01 --end 2025-01-31
    
    # Single airport only
    python fetch_aviationstack.py --airport ZBAA

Free Plan Limits: 500 requests/month
  - ~15-18 requests per day for all 3 airports
  - 2 weeks ≈ 220-250 requests (recommended)
  - 1 month ≈ 450-480 requests (tight but feasible)

Output: JSON + CSV files in D:/PARTD/data/flights/
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

# ─── Configuration ────────────────────────────────────────────────────────────

API_BASE = "https://api.aviationstack.com/v1/flights"

AIRPORTS = {
    "ZBAA": "Beijing Capital International",
    "ZBAD": "Beijing Daxing International",
    "ZBTJ": "Tianjin Binhai International",
}

OUTPUT_DIR = "D:/PARTD/data/flights"

# Default date range: 2 weeks in January 2025
DEFAULT_START = date(2025, 1, 6)   # Monday
DEFAULT_END = date(2025, 1, 19)    # 2 weeks inclusive

# ─── Core API Functions ───────────────────────────────────────────────────────

def fetch_page(access_key, params, retries=3):
    """Fetch a single page of results from AviationStack API."""
    for attempt in range(retries):
        try:
            resp = requests.get(API_BASE, params=params, timeout=45)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"    HTTP {resp.status_code}: {resp.text[:150]}")
                if attempt < retries - 1:
                    time.sleep(3)
                    continue
                return None
            return resp.json()
        except requests.exceptions.Timeout:
            print(f"    Timeout (attempt {attempt+1}/{retries})")
            time.sleep(3)
        except requests.exceptions.RequestException as e:
            print(f"    Request error: {e}")
            if attempt < retries - 1:
                time.sleep(3)
                continue
            return None
    return None


def fetch_flights(access_key, dep_icao=None, arr_icao=None,
                  flight_date=None, limit=100):
    """
    Fetch ALL flights matching criteria with automatic pagination.

    Returns list of flight dicts, or empty list on failure.
    """
    params = {
        "access_key": access_key,
        "limit": min(limit, 100),
    }
    if dep_icao:
        params["dep_icao"] = dep_icao
    if arr_icao:
        params["arr_icao"] = arr_icao
    if flight_date:
        params["flight_date"] = flight_date

    all_results = []
    offset = 0
    total_pages = 0

    while True:
        params["offset"] = offset
        data = fetch_page(access_key, params)
        if data is None:
            break

        results = data.get("data", [])
        all_results.extend(results)
        total_pages += 1

        pagination = data.get("pagination", {})
        total = pagination.get("total", 0)
        count = len(results)

        if total == 0 or offset + count >= total:
            break

        offset += count
        # Polite delay between pages
        time.sleep(0.5)

    return all_results, total_pages


def parse_flight(flight, query_type, query_airport):
    """
    Extract essential fields from a raw AviationStack flight dict.

    Returns a flat dict suitable for CSV/JSON export.
    """
    dep = flight.get("departure", {}) or {}
    arr = flight.get("arrival", {}) or {}
    airline = flight.get("airline", {}) or {}
    flt = flight.get("flight", {}) or {}
    acft = flight.get("aircraft", {}) or {}

    return {
        # Query metadata
        "_query_type": query_type,
        "_query_airport": query_airport,
        "_query_date": flight.get("flight_date", ""),

        # Flight identification
        "flight_date": flight.get("flight_date", ""),
        "flight_status": flight.get("flight_status", ""),
        "flight_iata": flt.get("iata", "") or "",
        "flight_icao": flt.get("icao", "") or "",
        "flight_number": flt.get("number", "") or "",

        # Airline
        "airline_iata": airline.get("iata", "") or "",
        "airline_icao": airline.get("icao", "") or "",
        "airline_name": airline.get("name", "") or "",

        # Aircraft (CRITICAL for noise modeling)
        "aircraft_icao": acft.get("icao", "") or "",
        "aircraft_iata": acft.get("iata", "") or "",
        "aircraft_registration": acft.get("registration", "") or "",

        # Departure
        "dep_airport": dep.get("airport", "") or "",
        "dep_iata": dep.get("iata", "") or "",
        "dep_icao": dep.get("icao", "") or "",
        "dep_terminal": dep.get("terminal", "") or "",
        "dep_gate": dep.get("gate", "") or "",
        "dep_delay": dep.get("delay") or 0,
        "dep_scheduled": dep.get("scheduled", "") or "",
        "dep_estimated": dep.get("estimated", "") or "",
        "dep_actual": dep.get("actual", "") or "",
        "dep_timezone": dep.get("timezone", "") or "",

        # Arrival
        "arr_airport": arr.get("airport", "") or "",
        "arr_iata": arr.get("iata", "") or "",
        "arr_icao": arr.get("icao", "") or "",
        "arr_terminal": arr.get("terminal", "") or "",
        "arr_gate": arr.get("gate", "") or "",
        "arr_delay": arr.get("delay") or 0,
        "arr_scheduled": arr.get("scheduled", "") or "",
        "arr_estimated": arr.get("estimated", "") or "",
        "arr_actual": arr.get("actual", "") or "",
        "arr_timezone": arr.get("timezone", "") or "",
    }


# ─── Export Functions ─────────────────────────────────────────────────────────

def save_json(flights, filepath):
    """Save flights as pretty-printed JSON."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(flights, f, ensure_ascii=False, indent=2)
    return os.path.getsize(filepath)


def save_csv(flights, filepath):
    """Save flights as CSV with all flattened fields."""
    if not flights:
        with open(filepath, "w", encoding="utf-8") as f:
            f.truncate(0)
        return 0

    fieldnames = list(flights[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flights)
    return os.path.getsize(filepath)


# ─── Summary & Statistics ─────────────────────────────────────────────────────

def print_summary(flights):
    """Print a summary of collected data."""
    print("\n" + "=" * 65)
    print("  DATA COLLECTION SUMMARY")
    print("=" * 65)

    total = len(flights)
    print(f"\n  Total flights collected: {total}")

    # By airport
    by_airport = defaultdict(int)
    for f in flights:
        apt = f["_query_airport"]
        by_airport[apt] += 1
    print(f"\n  By Airport:")
    for apt in ["ZBAA", "ZBAD", "ZBTJ"]:
        count = by_airport.get(apt, 0)
        pct = count / total * 100 if total else 0
        name = AIRPORTS.get(apt, apt)
        print(f"    {apt} ({name}): {count:>5}  ({pct:>5.1f}%)")

    # By flight status
    by_status = defaultdict(int)
    for f in flights:
        by_status[f.get("flight_status", "unknown")] += 1
    print(f"\n  By Flight Status:")
    for status, count in sorted(by_status.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total else 0
        print(f"    {status}: {count:>5}  ({pct:>5.1f}%)")

    # By direction
    by_dir = defaultdict(int)
    for f in flights:
        by_dir[f["_query_type"]] += 1
    print(f"\n  By Direction:")
    for d in ["departure", "arrival"]:
        count = by_dir.get(d, 0)
        pct = count / total * 100 if total else 0
        print(f"    {d}: {count:>5}  ({pct:>5.1f}%)")

    # Aircraft type diversity
    ac_types = set()
    for f in flights:
        ac = f.get("aircraft_icao", "")
        if ac:
            ac_types.add(ac)
    print(f"\n  Unique aircraft types (ICAO): {len(ac_types)}")

    # Date range
    dates = sorted(set(f["flight_date"] for f in flights if f["flight_date"]))
    if dates:
        print(f"  Date range: {dates[0]} ~ {dates[-1]} ({len(dates)} days)")

    print("=" * 65)


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch BTH airport flight data from AviationStack API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 2 weeks default
  python fetch_aviationstack.py

  # Custom range
  python fetch_aviationstack.py --start 2025-01-01 --end 2025-01-31

  # Single airport
  python fetch_aviationstack.py --airport ZBAA

  # Dry run (estimate API usage without fetching)
  python fetch_aviationstack.py --dry-run
        """,
    )
    parser.add_argument(
        "--start",
        type=str,
        default=DEFAULT_START.isoformat(),
        help="Start date (YYYY-MM-DD, default: 2025-01-06)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=DEFAULT_END.isoformat(),
        help="End date inclusive (YYYY-MM-DD, default: 2025-01-19)",
    )
    parser.add_argument(
        "--airport",
        type=str,
        choices=list(AIRPORTS.keys()) + ["ALL"],
        default="ALL",
        help="Target airport (default: ALL BTH airports)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate API usage without making actual requests",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Parse dates
    try:
        start_date = date.fromisoformat(args.start)
        end_date = date.fromisoformat(args.end)
    except ValueError as e:
        print(f"Error: Invalid date format — {e}")
        sys.exit(1)

    if start_date > end_date:
        print("Error: start date must be before end date")
        sys.exit(1)

    # Select airports
    if args.airport == "ALL":
        airports = list(AIRPORTS.keys())
    else:
        airports = [args.airport]

    # Calculate date range
    num_days = (end_date - start_date).days + 1
    dates = [start_date + timedelta(days=i) for i in range(num_days)]

    # ── Dry Run ──
    if args.dry_run:
        print("\n" + "=" * 60)
        print("  DRY RUN — Estimated API Usage")
        print("=" * 60)
        print(f"\n  Date range: {start_date} ~ {end_date} ({num_days} days)")
        print(f"  Airports:   {', '.join(airports)}")
        print(f"\n  Per Day:")
        print(f"    Queries per airport: 2 (departures + arrivals)")
        print(f"    Estimated pages per query:")
        print(f"      ZBAA: ~4 pages (est. 400 flights/direction)")
        print(f"      ZBAD: ~3 pages (est. 300 flights/direction)")
        print(f"      ZBTJ: ~1 page  (est. 100 flights/direction)")
        print(f"\n  Estimated totals:")
        total_req = 0
        for apt in airports:
            pages_per_dir = {"ZBAA": 4, "ZBAD": 3, "ZBTJ": 1}
            req = num_days * 2 * pages_per_dir.get(apt, 2)
            total_req += req
            print(f"    {apt}: ~{req} requests")
        print(f"\n  {'='*40}")
        print(f"  TOTAL: ~{total_req} API requests")
        print(f"  Free plan limit: 500/month")
        if total_req <= 500:
            print(f"  ✅ Within free plan limit!")
        else:
            print(f"  ⚠️  EXCEEDS free plan limit by {total_req - 500}")
            print(f"  Tip: Reduce date range or query fewer airports")
        print(f"  {'='*40}")
        return

    # ── Real Execution ──
    access_key = os.environ.get("AVIATIONSTACK_KEY", "")
    if not access_key:
        print("=" * 60)
        print("  ERROR: API key not found!")
        print("=" * 60)
        print()
        print("  You need to set the AVIATIONSTACK_KEY environment variable.")
        print()
        print("  1. Go to https://aviationstack.com and sign up (free)")
        print("  2. Get your API Access Key from the dashboard")
        print("  3. Set it as an environment variable:")
        print()
        print("     Windows (PowerShell):")
        print("       $env:AVIATIONSTACK_KEY = \"your_key_here\"")
        print()
        print("     Windows (CMD):")
        print("       set AVIATIONSTACK_KEY=your_key_here")
        print()
        print("     Linux/macOS:")
        print("       export AVIATIONSTACK_KEY=\"your_key_here\"")
        print()
        sys.exit(1)

    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Need requests library
    global requests
    try:
        import requests
    except ImportError:
        print("Error: 'requests' library not found. Run:")
        print("  pip install requests")
        sys.exit(1)

    # Collection
    all_flights = []
    total_api_calls = 0
    total_pages = 0

    print(f"\n{'='*65}")
    print(f"  BTH Flight Data Collection")
    print(f"  Period: {start_date} ~ {end_date} ({num_days} days)")
    print(f"  Airports: {', '.join(airports)}")
    print(f"{'='*65}\n")

    for current_date in dates:
        date_str = current_date.isoformat()
        print(f"[{date_str}] ", end="", flush=True)

        daily_count = 0
        daily_calls = 0

        for apt in airports:
            # ── Departures ──
            print(f"{apt}→", end="", flush=True)
            deps, pages = fetch_flights(
                access_key, dep_icao=apt, flight_date=date_str
            )
            for f in deps:
                f["_query_type"] = "departure"
                f["_query_airport"] = apt
            all_flights.extend(deps)
            daily_count += len(deps)
            daily_calls += pages
            total_pages += pages

            time.sleep(0.3)

            # ── Arrivals ──
            print(f"↓{apt} ", end="", flush=True)
            arrs, pages = fetch_flights(
                access_key, arr_icao=apt, flight_date=date_str
            )
            for f in arrs:
                f["_query_type"] = "arrival"
                f["_query_airport"] = apt
            all_flights.extend(arrs)
            daily_count += len(arrs)
            daily_calls += pages
            total_pages += pages

            time.sleep(0.3)

        total_api_calls += daily_calls
        print(f"| {daily_count} flights ({daily_calls} API calls)")

    # Parse all flights into flat records
    print("\nParsing flight records...")
    parsed = [parse_flight(f, f["_query_type"], f["_query_airport"])
              for f in all_flights]

    # Remove duplicates (flight may appear as both departure and arrival query)
    unique = {}
    for f in parsed:
        key = (f["flight_icao"], f["dep_icao"], f["arr_icao"],
               f["flight_date"], f["dep_scheduled"])
        if key not in unique:
            unique[key] = f
    parsed = list(unique.values())

    print(f"After dedup: {len(parsed)} unique flights")

    # ── Save ──
    output_stem = f"bth_flights_{start_date}_to_{end_date}"
    json_path = os.path.join(OUTPUT_DIR, f"{output_stem}.json")
    csv_path = os.path.join(OUTPUT_DIR, f"{output_stem}.csv")

    json_size = save_json(parsed, json_path)
    csv_size = save_csv(parsed, csv_path)

    # ── Summary ──
    print_summary(parsed)

    print(f"\n  Files saved:")
    print(f"    JSON:  {json_path}  ({json_size / 1024:.1f} KB)")
    print(f"    CSV:   {csv_path}  ({csv_size / 1024:.1f} KB)")
    print(f"\n  Total API calls made: {total_pages}")
    print(f"  Remaining this month: ~{500 - total_pages}")
    print()


if __name__ == "__main__":
    main()