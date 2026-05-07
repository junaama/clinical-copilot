"""One-time OAuth client bootstrap for the copilot standalone login flow.

Registers a confidential authorization-code + PKCE client at OpenEMR's
``/oauth2/default/registration`` endpoint. This is the client the agent
uses when a user clicks "Log in with OpenEMR" in the Co-Pilot UI — distinct
from the backend-services seeder client (``bootstrap_oauth.py``) and from
the EHR-launch client (``SMART_CLIENT_ID``).

Differences from ``bootstrap_oauth.py``:

- ``token_endpoint_auth_method`` is ``client_secret_post`` (confidential
  authcode), not ``private_key_jwt``. OpenEMR returns a real
  ``client_secret`` we have to store and ship to Railway.
- ``redirect_uris`` is the agent's real callback. PKCE means OpenEMR will
  redirect the browser there with ``?code=...&state=...``.
- Scopes are the ``user/*.rs`` set the standalone flow needs (see
  ``smart_standalone_scopes`` default in ``copilot.config``).

Run once. Re-running creates a *new* client; revoke the old one from the
OpenEMR admin UI if you do.

Usage:
    OE_FHIR_BASE_URL=https://openemr-production-c5b4.up.railway.app \\
    COPILOT_AGENT_URL=https://copilot-agent-production-3776.up.railway.app \\
        python bootstrap_standalone_oauth.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx


SECRETS_DIR = Path(__file__).parent / "secrets"
REGISTRATION_PATH = SECRETS_DIR / "standalone_client_registration.json"

CLIENT_NAME = "AgentForge Co-Pilot Standalone"
CONTACT_EMAIL = "naama.paulemont@challenger.gauntletai.com"

# Must match ``smart_standalone_scopes`` default in
# ``agent/src/copilot/config.py``. Keep these in sync — the agent sends
# this exact scope string on /authorize, and OpenEMR silently drops any
# scope from the issued token that wasn't registered against the client.
# Without ``api:oemr`` the Standard REST API rejects every request with
# "insufficient permissions for the requested resource" (the FHIR-only
# user/* scopes don't unlock /apis/default/api/...).
REQUESTED_SCOPES = " ".join([
    "openid",
    "fhirUser",
    "offline_access",
    "profile",
    "email",
    # API-class scopes — required for token to be honored on the
    # Standard REST and FHIR endpoints respectively.
    "api:oemr",
    "api:fhir",
    # FHIR resources the agent reads
    "user/Patient.rs",
    "user/Observation.rs",
    "user/Condition.rs",
    "user/MedicationRequest.rs",
    "user/Encounter.rs",
    "user/AllergyIntolerance.rs",
    "user/DocumentReference.rs",
    "user/DiagnosticReport.rs",
    "user/ServiceRequest.rs",
    "user/CareTeam.rs",
    "user/Practitioner.rs",
    # Standard-API write scopes (CRUDS / CRS) the agent uses for
    # document upload, allergy/medication/problem updates, patient lookup.
    "user/document.crs",
    "user/allergy.cruds",
    "user/medication.cruds",
    "user/medical_problem.cruds",
    "user/patient.rs",
])


def register_client(base_url: str, redirect_uri: str) -> dict:
    """POST to /oauth2/default/registration. Returns the response JSON.

    Confidential authcode client — OpenEMR's registrar generates the
    ``client_secret`` and returns it in the response body. This is the
    only time the secret is visible; we persist it to disk under
    ``secrets/`` (gitignored) so it can be copied to Railway env.
    """
    payload = {
        "application_type": "private",
        "client_name": CLIENT_NAME,
        "contacts": [CONTACT_EMAIL],
        "scope": REQUESTED_SCOPES,
        "redirect_uris": [redirect_uri],
        "token_endpoint_auth_method": "client_secret_post",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
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

    agent_url = os.environ.get("COPILOT_AGENT_URL")
    if not agent_url:
        print("ERROR: set COPILOT_AGENT_URL (e.g. https://copilot-agent-production-3776.up.railway.app)", file=sys.stderr)
        return 1

    redirect_uri = f"{agent_url.rstrip('/')}/auth/smart/callback"

    if REGISTRATION_PATH.exists():
        print(
            f"ERROR: {REGISTRATION_PATH} already exists — a standalone client "
            "is already registered. Revoke it in the OpenEMR admin UI and "
            "delete the file before bootstrapping a new one.",
            file=sys.stderr,
        )
        return 1

    SECRETS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Registering standalone client at {base_url}/oauth2/default/registration...")
    print(f"  redirect_uri: {redirect_uri}")
    result = register_client(base_url, redirect_uri)

    REGISTRATION_PATH.write_text(json.dumps(result, indent=2))
    REGISTRATION_PATH.chmod(0o600)

    client_id = result.get("client_id", "<missing>")
    client_secret = result.get("client_secret", "<missing>")
    print()
    print("Registration succeeded.")
    print(f"  client_id:     {client_id}")
    print(f"  client_secret: {client_secret}")
    print(f"  full response saved to {REGISTRATION_PATH}")
    print()
    print("NEXT STEPS:")
    print("  1. Log in to OpenEMR as admin.")
    print("  2. Go to: Admin -> System -> API Clients.")
    print(f"  3. Find the client named '{CLIENT_NAME}' and click Edit.")
    print("  4. Enable the client.")
    print("  5. Grant every scope listed in the request (user/*.rs + openid + fhirUser + offline_access).")
    print("  6. Set Railway env on the copilot-agent service:")
    print(f"       SMART_STANDALONE_CLIENT_ID={client_id}")
    print(f"       SMART_STANDALONE_CLIENT_SECRET={client_secret}")
    print(f"       SMART_STANDALONE_REDIRECT_URI={redirect_uri}")
    print("       SESSION_SECRET=<generate: python -c 'import secrets; print(secrets.token_urlsafe(48))'>")
    print("  7. Redeploy: bash scripts/deploy-agent.sh")

    return 0


if __name__ == "__main__":
    sys.exit(main())
