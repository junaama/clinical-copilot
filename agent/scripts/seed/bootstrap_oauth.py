"""One-time OAuth client bootstrap for the seed loader.

Generates an RSA keypair, builds a JWKS containing the public key, and
registers a SMART Backend Services client at OpenEMR's
`/oauth2/default/registration` endpoint. The private key stays on disk
(in `secrets/`) and is used later to sign JWT client-assertions when
exchanging for short-lived bearer tokens.

Run once. Re-running creates a new client; revoke old ones from the
OpenEMR admin UI if you do that.

Usage:
    OE_FHIR_BASE_URL=https://openemr-production-c5b4.up.railway.app \\
        python bootstrap_oauth.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


SECRETS_DIR = Path(__file__).parent / "secrets"
PRIVATE_KEY_PATH = SECRETS_DIR / "private_key.pem"
PUBLIC_KEY_PATH = SECRETS_DIR / "public_key.pem"
REGISTRATION_PATH = SECRETS_DIR / "client_registration.json"

KEY_ID = "seed-loader-1"
CLIENT_NAME = "AgentForge Seed Loader"
CONTACT_EMAIL = "naama.paulemont@challenger.gauntletai.com"

# We request the scopes the seeder will actually need to write each
# resource type, plus reads for verification. The admin still has to
# grant these from the UI before they take effect — registration alone
# is not authorization.
# Scopes are validated against the deployed instance's
# /.well-known/openid-configuration scopes_supported list. This OpenEMR
# build's FHIR module advertises read-only system scopes (.rs) plus
# bulk-export operations. Writes go through:
#   - the FHIR module for Patient/Practitioner/Organization (api:fhir)
#   - the Standard REST API for everything else (api:oemr) — encounters,
#     vitals, problems, medications, SOAP notes, etc.
# MedicationAdministration is not advertised at all on this build; we
# encode the held-lisinopril scenario via a SOAP note narrative.
REQUESTED_SCOPES = " ".join([
    "openid",
    "api:oemr",
    "api:fhir",
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


def _b64url_uint(value: int) -> str:
    """Encode an integer as base64url with no padding (per RFC 7518 §6.3.1)."""
    byte_len = (value.bit_length() + 7) // 8
    raw = value.to_bytes(byte_len, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_keypair() -> rsa.RSAPrivateKey:
    """Generate a 2048-bit RSA private key (sufficient for RS384)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def write_keypair(private_key: rsa.RSAPrivateKey) -> None:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    PRIVATE_KEY_PATH.write_bytes(private_pem)
    PRIVATE_KEY_PATH.chmod(0o600)

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    PUBLIC_KEY_PATH.write_bytes(public_pem)


def build_jwks(private_key: rsa.RSAPrivateKey) -> dict:
    numbers = private_key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS384",
                "use": "sig",
                "kid": KEY_ID,
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }


def register_client(base_url: str, jwks: dict) -> dict:
    """POST to /oauth2/default/registration. Returns the response JSON."""
    payload = {
        "application_type": "private",
        "client_name": CLIENT_NAME,
        "contacts": [CONTACT_EMAIL],
        "scope": REQUESTED_SCOPES,
        # Backend Services never redirects, but OpenEMR's registrar still
        # requires the field. Must be HTTPS — production mode rejects http.
        "redirect_uris": ["https://localhost/seed-loader-noop"],
        "token_endpoint_auth_method": "private_key_jwt",
        "jwks": jwks,
    }

    url = f"{base_url.rstrip('/')}/oauth2/default/registration"
    response = httpx.post(
        url,
        json=payload,
        headers={"Accept": "application/json"},
        timeout=30.0,
    )
    if response.status_code >= 400:
        print(f"Registration failed: HTTP {response.status_code}", file=sys.stderr)
        print(f"Response headers: {dict(response.headers)}", file=sys.stderr)
        print(f"Response body: {response.text}", file=sys.stderr)
        print(f"Request payload: {json.dumps(payload, indent=2)}", file=sys.stderr)
        response.raise_for_status()
    return response.json()


def main() -> int:
    base_url = os.environ.get("OE_FHIR_BASE_URL")
    if not base_url:
        print("ERROR: set OE_FHIR_BASE_URL", file=sys.stderr)
        return 1

    if REGISTRATION_PATH.exists():
        print(
            f"ERROR: {REGISTRATION_PATH} already exists — a client is already "
            "registered. Revoke it in the OpenEMR admin UI and delete the "
            "file before bootstrapping a new one.",
            file=sys.stderr,
        )
        return 1

    if PRIVATE_KEY_PATH.exists():
        print(f"Reusing existing keypair at {PRIVATE_KEY_PATH}")
        private_pem = PRIVATE_KEY_PATH.read_bytes()
        loaded = serialization.load_pem_private_key(private_pem, password=None)
        if not isinstance(loaded, rsa.RSAPrivateKey):
            print(f"ERROR: {PRIVATE_KEY_PATH} is not an RSA key.", file=sys.stderr)
            return 1
        private_key = loaded
    else:
        print("Generating RSA keypair...")
        private_key = generate_keypair()
        write_keypair(private_key)
        print(f"  private key -> {PRIVATE_KEY_PATH}")
        print(f"  public key  -> {PUBLIC_KEY_PATH}")

    jwks = build_jwks(private_key)

    print(f"Registering client at {base_url}/oauth2/default/registration...")
    result = register_client(base_url, jwks)

    REGISTRATION_PATH.write_text(json.dumps(result, indent=2))
    REGISTRATION_PATH.chmod(0o600)

    client_id = result.get("client_id", "<missing>")
    print()
    print("Registration succeeded.")
    print(f"  client_id: {client_id}")
    print(f"  full response saved to {REGISTRATION_PATH}")
    print()
    print("NEXT STEPS:")
    print("  1. Log in to OpenEMR as admin.")
    print("  2. Go to: Admin -> System -> API Clients.")
    print(f"  3. Find the client named '{CLIENT_NAME}' and click Edit.")
    print("  4. Enable the client.")
    print("  5. Grant every scope listed in the request (system/*.rs + api:oemr + api:fhir).")
    print("  6. Run get_token.py to verify the token exchange works.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
