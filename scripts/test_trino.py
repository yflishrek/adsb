#!/usr/bin/env python3
"""Quick OpenSky Trino connectivity + historical data test."""
from datetime import datetime, timezone

from pyopensky.trino import Trino

print("Connecting to OpenSky Trino ...")
try:
    t = Trino()
    print("Trino object created OK")
except Exception as e:
    print("FAILED to create Trino:", type(e).__name__, e)
    raise SystemExit(1)

# tiny connectivity probe (OpenSky blocks arbitrary SQL, so failure is non-fatal)
try:
    df0 = t.query("SELECT 1 AS ok")
    print("probe query OK ->", df0.to_dict("records"))
except Exception as e:
    print("probe FAILED (non-fatal):", type(e).__name__, str(e)[:160])

# real historical probe: ZBAA departures, 1 day in Jan 2025
start = datetime(2025, 1, 6, tzinfo=timezone.utc)
stop = datetime(2025, 1, 7, tzinfo=timezone.utc)
print(f"\nQuerying ZBAA departures {start} -> {stop} ...")
try:
    df = t.flightlist(start=start, stop=stop, departure_airport="ZBAA")
    print("flightlist rows:", len(df))
    print("columns:", list(df.columns))
    print(df.head(5).to_string())
except Exception as e:
    print("flightlist FAILED:", type(e).__name__, e)
    raise SystemExit(1)

print("\nHISTORICAL ACCESS CONFIRMED")
