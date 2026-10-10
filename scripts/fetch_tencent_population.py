#!/usr/bin/env python3
"""
Tencent Location Big Data Collector for BTH MAR Noise Study
============================================================
Fetches time-varying population density data for noise exposure
assessment in the Beijing-Tianjin-Hebei Multiple-Airport Region.

Data Channels (dual-path):
  [Primary]  Tencent Location Big Data WebServiceAPI (lbs.qq.com)
             Requires: (1) Dev Key from lbs.qq.com, (2) Trial ticket
             Endpoints: getRealTimeAreaTraffic, getAccumulatedAreaTraffic
  [Fallback] xingyun.map.qq.com legacy endpoint (no auth required)
             WARNING: Data quality unverified per CSDN author notes

Output:
  CSV files with hourly population density for user-defined zones
  within the BTH MAR noise study area.

Usage:
    python fetch_tencent_population.py --key YOUR_KEY --zone ZBAA_60dB
    python fetch_tencent_population.py --key YOUR_KEY --zone all --hours 24
    python fetch_tencent_population.py --xingyun --bbox 116.0,39.4,116.8,40.2

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

# BTH MAR bounding box (approximate)
# Beijing Capital (ZBAA):  40.08N, 116.58E
# Beijing Daxing (ZBAD):   39.51N, 116.41E
# Tianjin Binhai (ZBTJ):   39.12N, 117.35E
BTH_BBOX = {
    "name": "Beijing-Tianjin-Hebei MAR",
    "south": 38.5,
    "north": 41.0,
    "west": 115.5,
    "east": 118.5,
}

# Predefined noise-zone bounding boxes (placeholders — replace with actual
# contour-derived polygons after noise modelling is complete).
# Format: {zone_name: {"description": str, "bbox": (west, south, east, north)}}
NOISE_ZONES = {
    "ZBAA_55dB": {
        "description": "Capital Airport 55 dB Lden contour zone (approx.)",
        "bbox": (116.20, 39.80, 117.00, 40.35),
    },
    "ZBAA_60dB": {
        "description": "Capital Airport 60 dB Lden contour zone (approx.)",
        "bbox": (116.35, 39.88, 116.85, 40.20),
    },
    "ZBAD_55dB": {
        "description": "Daxing Airport 55 dB Lden contour zone (approx.)",
        "bbox": (116.10, 39.30, 116.80, 39.75),
    },
    "ZBAD_60dB": {
        "description": "Daxing Airport 60 dB Lden contour zone (approx.)",
        "bbox": (116.20, 39.38, 116.70, 39.65),
    },
    "ZBTJ_55dB": {
        "description": "Tianjin Airport 55 dB Lden contour zone (approx.)",
        "bbox": (117.00, 38.95, 117.70, 39.35),
    },
    "ZBTJ_60dB": {
        "description": "Tianjin Airport 60 dB Lden contour zone (approx.)",
        "bbox": (117.10, 39.00, 117.55, 39.25),
    },
    "overlap_ZBAD_ZBTJ": {
        "description": "ZBAD-ZBTJ overlapping approach corridor",
        "bbox": (116.50, 39.10, 117.30, 39.50),
    },
    "overlap_ZBAA_ZBAD": {
        "description": "ZBAA-ZBAD lateral buffer zone",
        "bbox": (116.00, 39.50, 116.60, 40.10),
    },
}

# API endpoints
XINGYUN_URL = "https://xingyun.map.qq.com/api/getXingyunPoints"
LBS_API_BASE = "https://apis.map.qq.com"

# Output directory
DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "population"
)

# Xingyun data parameters
# Each call returns ~10 MB; data is (lat, lon, weight) × millions of points
# lat/lon are integers = degrees × 100
XINGYUN_COUNT = 4        # 4 quadrants per time step
XINGYUN_RANK = [0, 1, 2, 3]

# WebServiceAPI rate limit: 1 QPS
API_INTERVAL = 1.1  # seconds

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tencent-pop")

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def bbox_to_polygon_str(bbox: Tuple[float, float, float, float]) -> str:
    """
    Convert a bounding box to a closed polygon WKT-like string
    for WebServiceAPI perimeter queries.
    
    Args:
        bbox: (west, south, east, north)
    
    Returns:
        Semicolon-delimited lon,lat pairs forming a closed rectangle.
    """
    w, s, e, n = bbox
    return (
        f"{w},{s};{e},{s};{e},{n};{w},{n};{w},{s}"
    )


def grid_in_bbox(bbox: Tuple[float, float, float, float],
                 resolution_deg: float = 0.01) -> List[Tuple[float, float]]:
    """
    Generate a grid of lat/lon points within a bounding box.
    
    Args:
        bbox: (west, south, east, north)
        resolution_deg: grid spacing in degrees (~1 km at mid-latitudes)
    
    Returns:
        List of (lat, lon) tuples.
    """
    w, s, e, n = bbox
    lats = np.arange(s, n + resolution_deg / 2, resolution_deg)
    lons = np.arange(w, e + resolution_deg / 2, resolution_deg)
    points = []
    for lat in lats:
        for lon in lons:
            points.append((round(lat, 4), round(lon, 4)))
    return points


def xingyun_points_to_df(raw_text: str) -> pd.DataFrame:
    """
    Parse xingyun.map.qq.com API response into a DataFrame.
    
    The response 'locs' field contains comma-separated triplets:
        lat_int, lon_int, count, lat_int, lon_int, count, ...
    where lat_int = latitude × 100, lon_int = longitude × 100.
    
    Args:
        raw_text: JSON text from the xingyun API response.
    
    Returns:
        DataFrame with columns: lat, lon, count
    """
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse xingyun response: {e}")
        return pd.DataFrame()
    
    locs = data.get("locs", "")
    if not locs:
        log.warning("Empty locs field in xingyun response")
        return pd.DataFrame()
    
    parts = locs.split(",")
    n_points = len(parts) // 3
    
    rows = []
    for i in range(n_points):
        try:
            lat_raw = int(parts[0 + 3 * i])
            lon_raw = int(parts[1 + 3 * i])
            cnt = int(parts[2 + 3 * i])
            lat = lat_raw / 100.0
            lon = lon_raw / 100.0
            rows.append({"lat": lat, "lon": lon, "count": cnt})
        except (ValueError, IndexError):
            continue
    
    df = pd.DataFrame(rows)
    return df


def filter_by_bbox(df: pd.DataFrame,
                   bbox: Tuple[float, float, float, float]) -> pd.DataFrame:
    """Filter DataFrame rows to a bounding box."""
    w, s, e, n = bbox
    return df[
        (df["lon"] >= w) & (df["lon"] <= e) &
        (df["lat"] >= s) & (df["lat"] <= n)
    ].copy()


def aggregate_grid(df: pd.DataFrame,
                   resolution_deg: float = 0.01) -> pd.DataFrame:
    """
    Aggregate point data to a regular grid.
    
    Args:
        df: DataFrame with lat, lon, count columns
        resolution_deg: grid cell size in degrees
    
    Returns:
        Aggregated DataFrame with binned lat, lon and summed count.
    """
    if df.empty:
        return df
    df["lat_bin"] = (df["lat"] / resolution_deg).round() * resolution_deg
    df["lon_bin"] = (df["lon"] / resolution_deg).round() * resolution_deg
    agg = df.groupby(["lat_bin", "lon_bin"], as_index=False)["count"].sum()
    agg.columns = ["lat", "lon", "count"]
    return agg

# ---------------------------------------------------------------------------
# Data Channels
# ---------------------------------------------------------------------------

class TencentWebServiceAPI:
    """
    Primary channel: Tencent Location Big Data WebServiceAPI.
    
    Requires:
        1. Developer key from https://lbs.qq.com/
        2. WebServiceAPI feature enabled
        3. Trial ticket submitted for population data access
           (https://cloud.tencent.com/developer/mcp/server/11760)
    
    Reference: https://lbs.qq.com/service/webService/webServiceGuide/overview
    """
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
    
    def get_area_id(self, region_name: str) -> Optional[str]:
        """Get area ID by region name."""
        url = f"{LBS_API_BASE}/ws/geocode/v1/"
        params = {
            "key": self.api_key,
            "keyword": region_name,
            "get_poi": 0,
        }
        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == 0 and data.get("result"):
                return data["result"].get("title")
            return None
        except Exception as e:
            log.error(f"get_area_id error: {e}")
            return None
    
    def get_realtime_traffic(self, polygon: str) -> Optional[Dict]:
        """
        Query real-time population for a polygon area.
        
        Args:
            polygon: Semicolon-delimited lon,lat pairs forming closed polygon.
        
        Returns:
            API response dict or None on failure.
        """
        # NOTE: Exact endpoint may differ; adjust after trial activation.
        # This is based on documented MCP capabilities.
        url = f"{LBS_API_BASE}/ws/district/v1/getrealtimetraffic"
        params = {
            "key": self.api_key,
            "polygon": polygon,
            "output": "json",
        }
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"get_realtime_traffic error: {e}")
            return None
    
    def get_accumulated_traffic(self, polygon: str,
                                date: str) -> Optional[Dict]:
        """
        Query accumulated daily population for a polygon.
        
        Args:
            polygon: Semicolon-delimited lon,lat pairs.
            date: Date string (YYYY-MM-DD).
        
        Returns:
            API response dict or None on failure.
        """
        url = f"{LBS_API_BASE}/ws/district/v1/getaccumulatedtraffic"
        params = {
            "key": self.api_key,
            "polygon": polygon,
            "date": date,
            "output": "json",
        }
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"get_accumulated_traffic error: {e}")
            return None


class XingyunCollector:
    """
    Fallback channel: xingyun.map.qq.com legacy API.
    
    This endpoint is used by the heat.qq.com frontend to fetch raw
    location clustering data. It does not require authentication.
    
    WARNING: Data accuracy is officially unverified. The CSDN author
    notes: "数据从没人说是准确数据，也没说是完整数据，发paper的话建议三思"
    (The data has never been claimed to be accurate or complete;
    think twice before publishing papers using it.)
    
    Use only for preliminary exploration or cross-validation.
    """
    
    def __init__(self):
        self.session = requests.Session()
    
    def fetch_all_quadrants(self) -> pd.DataFrame:
        """
        Fetch all 4 quadrants of xingyun data.
        
        Returns:
            Combined DataFrame with lat, lon, count columns.
        """
        all_dfs = []
        for rank in XINGYUN_RANK:
            payload = {"count": XINGYUN_COUNT, "rank": rank}
            try:
                log.info(f"Fetching xingyun rank={rank}...")
                resp = self.session.post(
                    XINGYUN_URL,
                    data=json.dumps(payload),
                    timeout=120,
                )
                resp.raise_for_status()
                df = xingyun_points_to_df(resp.text)
                log.info(f"  Got {len(df)} points from rank={rank}")
                all_dfs.append(df)
                # Polite delay between requests
                time.sleep(2.0)
            except requests.exceptions.RequestException as e:
                log.error(f"  Rank {rank} failed: {e}")
                continue
        
        if not all_dfs:
            return pd.DataFrame()
        
        combined = pd.concat(all_dfs, ignore_index=True)
        # Deduplicate identical (lat, lon) points by averaging counts
        combined = combined.groupby(["lat", "lon"], as_index=False)["count"].mean()
        return combined

# ---------------------------------------------------------------------------
# Main Workflow
# ---------------------------------------------------------------------------

def collect_xingyun_for_zones(
    output_dir: str = DEFAULT_OUTPUT_DIR,
    zones: Optional[List[str]] = None,
    aggregate: bool = True,
    resolution_deg: float = 0.01,
) -> Dict[str, str]:
    """
    Collect xingyun data and save zone-specific extracts.
    
    Args:
        output_dir: Output directory for CSV files.
        zones: List of zone keys to extract. If None, all zones.
        aggregate: Whether to aggregate to regular grid.
        resolution_deg: Grid resolution in degrees.
    
    Returns:
        Dict mapping zone keys to output file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    if zones is None:
        zones = list(NOISE_ZONES.keys())
    
    collector = XingyunCollector()
    
    log.info("=" * 60)
    log.info("Xingyun Data Collection - BTH MAR Noise Study")
    log.info("=" * 60)
    
    # Fetch full xingyun data
    full_df = collector.fetch_all_quadrants()
    if full_df.empty:
        log.error("No xingyun data retrieved")
        return {}
    
    log.info(f"Total unique points: {len(full_df)}")
    
    if aggregate:
        full_df = aggregate_grid(full_df, resolution_deg)
        log.info(f"After grid aggregation ({resolution_deg}°): {len(full_df)} cells")
    
    # Save zone-specific extracts
    results = {}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Save full extent
    full_path = os.path.join(output_dir, f"population_full_{timestamp}.csv")
    full_df.to_csv(full_path, index=False)
    log.info(f"Saved full dataset: {full_path}")
    results["full"] = full_path
    
    for zone_key in zones:
        if zone_key not in NOISE_ZONES:
            log.warning(f"Unknown zone: {zone_key}, skipping")
            continue
        
        zone_info = NOISE_ZONES[zone_key]
        bbox = zone_info["bbox"]
        
        zone_df = filter_by_bbox(full_df, bbox)
        if zone_df.empty:
            log.warning(f"  {zone_key}: No data points in bbox")
            continue
        
        zone_path = os.path.join(
            output_dir, f"population_{zone_key}_{timestamp}.csv"
        )
        zone_df.to_csv(zone_path, index=False)
        log.info(f"  {zone_key}: {len(zone_df)} grid cells → {zone_path}")
        results[zone_key] = zone_path
    
    return results


def collect_official_api_for_zone(
    api_key: str,
    zone_key: str,
    date: Optional[str] = None,
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> Optional[str]:
    """
    Collect population data for a zone using the official WebServiceAPI.
    
    Args:
        api_key: Tencent LBS developer key.
        zone_key: Zone identifier from NOISE_ZONES.
        date: Date string (YYYY-MM-DD). If None, uses today.
        output_dir: Output directory.
    
    Returns:
        Path to output CSV or None on failure.
    """
    if zone_key not in NOISE_ZONES:
        log.error(f"Unknown zone: {zone_key}")
        return None
    
    os.makedirs(output_dir, exist_ok=True)
    
    client = TencentWebServiceAPI(api_key)
    zone_info = NOISE_ZONES[zone_key]
    bbox = zone_info["bbox"]
    polygon = bbox_to_polygon_str(bbox)
    
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    
    log.info(f"Querying official API for {zone_key} on {date}...")
    
    result = client.get_accumulated_traffic(polygon, date)
    if result is None:
        # Fall back to real-time query
        result = client.get_realtime_traffic(polygon)
    
    if result is None:
        log.error(f"  API query failed for {zone_key}")
        return None
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(
        output_dir, f"official_{zone_key}_{date}_{timestamp}.json"
    )
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    log.info(f"  Saved: {output_path}")
    return output_path


def generate_summary_stats(data_dir: str = DEFAULT_OUTPUT_DIR):
    """Generate summary statistics for collected population data."""
    import glob
    
    log.info("=" * 60)
    log.info("Population Data Summary")
    log.info("=" * 60)
    
    csv_files = sorted(glob.glob(os.path.join(data_dir, "population_*.csv")))
    
    if not csv_files:
        log.info("No population data files found.")
        return
    
    for f in csv_files:
        try:
            df = pd.read_csv(f)
            basename = os.path.basename(f)
            parts = basename.replace("population_", "").split("_")
            zone = parts[0] if parts else "unknown"
            
            if "count" in df.columns:
                total_pop = df["count"].sum()
                mean_density = df["count"].mean()
                max_density = df["count"].max()
                log.info(
                    f"  {zone:20s} | {len(df):5d} cells | "
                    f"Total: {total_pop:10.0f} | "
                    f"Mean: {mean_density:8.2f} | "
                    f"Max: {max_density:8.0f}"
                )
            else:
                log.info(f"  {zone:20s} | {len(df):5d} rows | {basename}")
        except Exception as e:
            log.warning(f"  Could not read {f}: {e}")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Collect Tencent Location Big Data for BTH MAR noise study",
    )
    
    # Channel selection
    channel = parser.add_mutually_exclusive_group(required=True)
    channel.add_argument(
        "--key", type=str, default=None,
        help="Tencent LBS WebServiceAPI developer key"
    )
    channel.add_argument(
        "--xingyun", action="store_true",
        help="Use xingyun.map.qq.com legacy API (no auth needed, data quality unknown)"
    )
    
    # Zone selection
    parser.add_argument(
        "--zone", type=str, default="all",
        help="Noise zone to query (default: all). Options: "
             + ", ".join(list(NOISE_ZONES.keys()) + ["all"])
    )
    
    # Date parameters
    parser.add_argument(
        "--date", type=str, default=None,
        help="Date for accumulated traffic query (YYYY-MM-DD, default: today)"
    )
    parser.add_argument(
        "--hours", type=int, default=0,
        help="Number of hours to run continuous collection (xingyun only, 0 = once)"
    )
    
    # Output
    parser.add_argument(
        "--output-dir", type=str, default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})"
    )
    
    # Utility
    parser.add_argument(
        "--summary", action="store_true",
        help="Print summary of existing data and exit"
    )
    parser.add_argument(
        "--list-zones", action="store_true",
        help="List available noise zones and exit"
    )
    parser.add_argument(
        "--no-aggregate", action="store_true",
        help="Skip grid aggregation (xingyun only, keep raw points)"
    )
    parser.add_argument(
        "--resolution", type=float, default=0.01,
        help="Grid resolution in degrees (default: 0.01 ≈ 1 km)"
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
    
    if args.list_zones:
        print(f"\nAvailable noise zones ({len(NOISE_ZONES)}):")
        print("=" * 60)
        for key, info in NOISE_ZONES.items():
            w, s, e, n = info["bbox"]
            print(f"  {key:20s} | {info['description']}")
            print(f"  {'':20s}   BBox: ({w:.2f}, {s:.2f}) → ({e:.2f}, {n:.2f})")
            print()
        return
    
    if args.summary:
        generate_summary_stats(args.output_dir)
        return
    
    # Determine zones
    if args.zone == "all":
        zones = list(NOISE_ZONES.keys())
    else:
        zones = [args.zone]
        for z in zones:
            if z not in NOISE_ZONES:
                log.error(f"Unknown zone: {z}. Use --list-zones to see options.")
                sys.exit(1)
    
    if args.key:
        # Official WebServiceAPI channel
        log.info(f"Using official WebServiceAPI (key: {args.key[:6]}...)")
        for zone_key in zones:
            collect_official_api_for_zone(
                api_key=args.key,
                zone_key=zone_key,
                date=args.date,
                output_dir=args.output_dir,
            )
    elif args.xingyun:
        # Legacy xingyun channel
        log.info("Using xingyun.map.qq.com legacy API")
        log.warning("=" * 60)
        log.warning("DATA QUALITY WARNING: The xingyun API data has not been")
        log.warning("officially verified for accuracy. See CSDN article for details.")
        log.warning("Use only for preliminary exploration or cross-validation.")
        log.warning("=" * 60)
        
        if args.hours > 0:
            # Continuous collection
            end_time = datetime.now() + timedelta(hours=args.hours)
            log.info(f"Continuous collection for {args.hours} hours (until {end_time})")
            iteration = 0
            while datetime.now() < end_time:
                iteration += 1
                log.info(f"\nIteration {iteration} @ {datetime.now().strftime('%H:%M:%S')}")
                collect_xingyun_for_zones(
                    output_dir=args.output_dir,
                    zones=zones,
                    aggregate=not args.no_aggregate,
                    resolution_deg=args.resolution,
                )
                # Wait for next xingyun cycle (~5 min between refreshes)
                sleep_seconds = min(300, (end_time - datetime.now()).total_seconds())
                if sleep_seconds > 0:
                    log.info(f"Sleeping {sleep_seconds:.0f}s...")
                    time.sleep(sleep_seconds)
        else:
            # Single collection
            collect_xingyun_for_zones(
                output_dir=args.output_dir,
                zones=zones,
                aggregate=not args.no_aggregate,
                resolution_deg=args.resolution,
            )
    
    # Print summary
    generate_summary_stats(args.output_dir)
    log.info("Done!")


if __name__ == "__main__":
    main()