"""Mint a short-lived bearer token.

Two modes (architectural reasons in the README):

    --system   client_credentials grant (the default).
               Read-only by OpenEMR design; for FHIR reads only.
               No prior login required (uses the registered keypair).

    --user     refresh_token grant.
               Write-capable. Required for the seed loader.
               Requires `oauth_login.py` to have been run once.

Both modes save the response to ./secrets/last_token.json.

Usage:
    OE_FHIR_BASE_URL=https://openemr-production-c5b4.up.railway.app \\
        python get_token.py [--system | --user]

Library:
    from get_token import mint_token_system, mint_token_user
    token = mint_token_user(base_url)["access_token"]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets as _secrets
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import jwt


SECRETS_DIR = Path(__file__).parent / "secrets"
PRIVATE_KEY_PATH = SECRETS_DIR / "private_key.pem"
REGISTRATION_PATH = SECRETS_DIR / "client_registration.json"
REFRESH_TOKEN_PATH = SECRETS_DIR / "refresh_token.json"
LAST_TOKEN_PATH = SECRETS_DIR / "last_token.json"

KEY_ID = "seed-loader-1"

# Read-only scope set for the system-context (client_credentials) token.
SYSTEM_SCOPE = " ".join([
    "openid",
    "system/Patient.rs",
    "system/Practitioner.rs",
    "system/PractitionerRole.rs",
    "system/CareTeam.rs",
    "system/Encounter.rs",
    "system/Condition.rs",
    "system/Observation.rs",
    "system/MedicationRequest.rs",
    "system/ServiceRequest.rs",
    "system/DiagnosticReport.rs",
    "system/DocumentReference.rs",
])


def _load_credentials() -> tuple[str, bytes]:
    if not REGISTRATION_PATH.exists():
        raise FileNotFoundError(
            f"{REGISTRATION_PATH} not found — run bootstrap_oauth.py first."
        )
    if not PRIVATE_KEY_PATH.exists():
        raise FileNotFoundError(
            f"{PRIVATE_KEY_PATH} not found — run bootstrap_oauth.py first."
        )
    registration = json.loads(REGISTRATION_PATH.read_text())
    return registration["client_id"], PRIVATE_KEY_PATH.read_bytes()


def _discover_token_endpoint(base_url: str) -> str:
    url = f"{base_url.rstrip('/')}/oauth2/default/.well-known/openid-configuration"
    response = httpx.get(url, timeout=15.0)
    response.raise_for_status()
    return response.json()["token_endpoint"]


def _build_client_assertion(client_id: str, private_key: bytes, aud: str) -> str:
    now = int(time.time())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": aud,
        "iat": now,
        "exp": now + 300,
        "jti": _secrets.token_urlsafe(24),
    }
    headers = {"kid": KEY_ID, "typ": "JWT"}
    return jwt.encode(claims, private_key, algorithm="RS384", headers=headers)


def _post_token(base_url: str, payload: dict, *, advertised_aud: str) -> dict[str, Any]:
    post_url = f"{base_url.rstrip('/')}/oauth2/default/token"
    response = httpx.post(
        post_url,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        print(f"Token request failed: HTTP {response.status_code}", file=sys.stderr)
        print(f"Response body: {response.text}", file=sys.stderr)
        print(f"JWT aud claim: {advertised_aud}", file=sys.stderr)
        print(f"POST URL: {post_url}", file=sys.stderr)
        response.raise_for_status()
    return response.json()


def mint_token_system(base_url: str, scope: str = SYSTEM_SCOPE) -> dict[str, Any]:
    """client_credentials grant. Read-only by OpenEMR design."""
    client_id, private_key = _load_credentials()
    advertised_token_endpoint = _discover_token_endpoint(base_url)
    assertion = _build_client_assertion(client_id, private_key, advertised_token_endpoint)
    payload = {
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "scope": scope,
    }
    return _post_token(base_url, payload, advertised_aud=advertised_token_endpoint)


def mint_token_user(base_url: str) -> dict[str, Any]:
    """refresh_token grant. Write-capable. Requires oauth_login.py first."""
    if not REFRESH_TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"{REFRESH_TOKEN_PATH} not found — run oauth_login.py first."
        )
    client_id, private_key = _load_credentials()
    advertised_token_endpoint = _discover_token_endpoint(base_url)
    saved = json.loads(REFRESH_TOKEN_PATH.read_text())
    refresh_token = saved.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(f"No refresh_token in {REFRESH_TOKEN_PATH}")

    assertion = _build_client_assertion(client_id, private_key, advertised_token_endpoint)
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
    }
    result = _post_token(base_url, payload, advertised_aud=advertised_token_endpoint)

    # OpenEMR rotates refresh tokens — persist the new one if returned.
    if "refresh_token" in result and result["refresh_token"] != refresh_token:
        saved.update(result)
        REFRESH_TOKEN_PATH.write_text(json.dumps(saved, indent=2))
        REFRESH_TOKEN_PATH.chmod(0o600)
    return result


def _print_summary(result: dict[str, Any], mode: str) -> None:
    LAST_TOKEN_PATH.write_text(json.dumps(result, indent=2))
    LAST_TOKEN_PATH.chmod(0o600)
    access_token = result.get("access_token", "")
    print(f"Token mint succeeded ({mode}).")
    print(f"  token_type: {result.get('token_type')}")
    print(f"  expires_in: {result.get('expires_in')} seconds")
    print(f"  granted scope: {result.get('scope')}")
    print(
        f"  access_token: {access_token[:20]}…{access_token[-10:]}  "
        f"(full token in {LAST_TOKEN_PATH})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--system", action="store_true", help="client_credentials (read-only)")
    mode.add_argument("--user", action="store_true", help="refresh_token (write-capable)")
    args = parser.parse_args()

    base_url = os.environ.get("OE_FHIR_BASE_URL")
    if not base_url:
        print("ERROR: set OE_FHIR_BASE_URL", file=sys.stderr)
        return 1

    # Default to --system if neither flag is given (backwards compatible).
    if args.user:
        result = mint_token_user(base_url)
        _print_summary(result, mode="user / refresh_token")
    else:
        result = mint_token_system(base_url)
        _print_summary(result, mode="system / client_credentials")
    return 0


if __name__ == "__main__":
    sys.exit(main())
