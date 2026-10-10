#!/usr/bin/env python3
"""Test whether OpenSky REST API (OAuth2) can return HISTORICAL flights.

If this works, we can backfill past days without keeping the PC on 24/7.
"""
import json
import time
from pathlib import Path

import requests

CRED = Path(r"C:\Users\commi\Downloads\credentials.json")
TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)


def get_token() -> str:
    c = json.loads(CRED.read_text())
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": c["clientId"],
            "client_secret": c["clientSecret"],
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def main():
    token = get_token()
    print("token OK, len =", len(token))
    h = {"Authorization": f"Bearer {token}"}

    # 2 days ago (UTC)
    now = int(time.time())
    end = now - 2 * 86400
    begin = end - 86400
    print(f"window: {begin} -> {end}")

    tests = [
        ("v1 flights/arrival",
         f"https://opensky-network.org/api/flights/arrival"
         f"?airport=ZBAA&begin={begin}&end={end}"),
        ("v1 flights/departure",
         f"https://opensky-network.org/api/flights/departure"
         f"?airport=ZBAA&begin={begin}&end={end}"),
        ("v2 flights/arrival",
         f"https://opensky-network.org/api/v2/flights/arrival"
         f"?airport=ZBAA&begin={begin}&end={end}"),
    ]

    for name, url in tests:
        try:
            r = requests.get(url, headers=h, timeout=60)
            print(f"\n[{name}] HTTP {r.status_code}")
            if r.status_code == 200:
                d = r.json()
                print(f"   -> {len(d)} flights")
                if d:
                    print("   sample:", json.dumps(d[0], ensure_ascii=False)[:300])
            else:
                print("   body:", r.text[:200])
        except Exception as e:
            print(f"\n[{name}] ERROR {type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    main()
