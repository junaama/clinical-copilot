"""Smoke-test the saved access token can write via FHIR + Standard API.

Reads the access_token from secrets/refresh_token.json (the file is named
'refresh_token' for legacy reasons; on this OpenEMR build it actually
holds an access-token-only response since refresh_token grant is not
supported).

Tests three things in order:
  1. FHIR read (sanity)
  2. FHIR write — POST /fhir/Patient
  3. Standard API write — POST /api/patient

Prints PASS/FAIL for each. Exit code is the count of failures.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx


SECRETS = Path(__file__).parent / "secrets"
TOKEN_PATH = SECRETS / "refresh_token.json"
BASE = "https://openemr-production-c5b4.up.railway.app"


def main() -> int:
    if not TOKEN_PATH.exists():
        print(f"ERROR: {TOKEN_PATH} not found. Run oauth_login.py first.", file=sys.stderr)
        return 1

    saved = json.loads(TOKEN_PATH.read_text())
    token = saved.get("access_token")
    if not token:
        print("ERROR: no access_token in saved file.", file=sys.stderr)
        return 1

    # Decode the token's claims so we can see what scopes it actually carries.
    parts = token.split(".")
    pad = "=" * (-len(parts[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    print("Token claims:")
    for k, v in claims.items():
        if k != "jti":
            print(f"  {k}: {v}")
    print()

    H = {"Authorization": f"Bearer {token}"}
    failures = 0

    # --- TEST 1: FHIR read (sanity) ---
    print("=== TEST 1: FHIR read GET /fhir/Patient ===")
    r = httpx.get(
        f"{BASE}/apis/default/fhir/Patient?_count=2",
        headers={**H, "Accept": "application/fhir+json"},
        timeout=30,
    )
    print(f"HTTP {r.status_code}")
    if r.status_code == 200:
        print(f"  total patients: {r.json().get('total')}")
        print("  PASS")
    else:
        print(f"  body: {r.text[:300]}")
        print("  FAIL")
        failures += 1
    print()

    # --- TEST 2: FHIR write ---
    print("=== TEST 2: FHIR write POST /fhir/Patient ===")
    r = httpx.post(
        f"{BASE}/apis/default/fhir/Patient",
        headers={**H, "Content-Type": "application/fhir+json"},
        json={
            "resourceType": "Patient",
            "name": [{"given": ["SeedSmoke"], "family": "FHIRTest"}],
            "gender": "unknown",
            "birthDate": "1980-01-01",
        },
        timeout=30,
    )
    print(f"HTTP {r.status_code}")
    print(f"  Location: {r.headers.get('location')}")
    if r.status_code in (200, 201):
        print("  PASS")
    else:
        print(f"  body: {r.text[:300]}")
        print("  FAIL")
        failures += 1
    print()

    # --- TEST 3: Standard API write ---
    print("=== TEST 3: Standard API write POST /api/patient ===")
    r = httpx.post(
        f"{BASE}/apis/default/api/patient",
        headers={**H, "Content-Type": "application/json"},
        json={
            "fname": "SeedSmoke",
            "lname": "StdAPITest",
            "DOB": "1980-01-01",
            "sex": "Female",
        },
        timeout=30,
    )
    print(f"HTTP {r.status_code}")
    if r.status_code in (200, 201):
        print(f"  body: {r.text[:200]}")
        print("  PASS")
    else:
        print(f"  body: {r.text[:300]}")
        print("  FAIL")
        failures += 1
    print()

    print(f"=== SUMMARY: {3 - failures}/3 passed ===")
    return failures


if __name__ == "__main__":
    sys.exit(main())
