"""Set GitHub Actions repository secrets (libsodium sealed box)."""
import base64
import json
import os
import sys

import requests
from nacl import encoding, public

TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ["GH_REPO"]           # owner/repo
API = f"https://api.github.com/repos/{REPO}"
HDR = {"Authorization": f"Bearer {TOKEN}",
       "Accept": "application/vnd.github+json"}


def set_secret(name: str, value: str):
    pk = requests.get(f"{API}/actions/secrets/public-key", headers=HDR, timeout=30).json()
    key = public.PublicKey(pk["key"].encode(), encoding.Base64Encoder())
    enc = public.SealedBox(key).encrypt(value.encode())
    r = requests.put(
        f"{API}/actions/secrets/{name}", headers=HDR, timeout=30,
        json={"encrypted_value": base64.b64encode(enc).decode(),
              "key_id": pk["key_id"]})
    print(f"  {name}: HTTP {r.status_code}"
          + ("" if r.ok else f" :: {r.text[:200]}"))
    return r.ok


def main():
    creds = json.load(open(os.path.expanduser(
        "~/Downloads/credentials.json")))
    ok = True
    ok &= set_secret("OPENSKY_CLIENT_ID", creds["clientId"])
    ok &= set_secret("OPENSKY_CLIENT_SECRET", creds["clientSecret"])

    # list secrets to confirm values are stored but never readable
    r = requests.get(f"{API}/actions/secrets", headers=HDR, timeout=30).json()
    print("  stored secrets:", [s["name"] for s in r.get("secrets", [])])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
