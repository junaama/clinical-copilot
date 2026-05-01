"""One-time interactive OAuth login for the seed loader.

OpenEMR's `client_credentials` grant is restricted to SMART Bulk FHIR (read
only by spec — the `api:oemr` / `api:fhir` / `user/*.write` scopes are
stripped at token issuance). To get a token capable of writing seed data,
the loader needs a user-context token, which means the `authorization_code`
grant. Confidential clients can use `private_key_jwt` to authenticate at
the token endpoint, and `offline_access` scope returns a long-lived
refresh token.

Run once. Prints an authorize URL, you log in as admin in the browser,
the redirect lands at https://localhost/seed-loader-noop?code=... (which
will fail to load because nothing is listening — that's fine, the code
is in the URL bar). Paste the entire redirected URL back into the script.
The script exchanges the code for tokens and saves the refresh token to
secrets/refresh_token.json. From then on, get_token.py with --user uses
the refresh token to mint short-lived access tokens without browser
interaction.

Usage:
    OE_FHIR_BASE_URL=https://openemr-production-c5b4.up.railway.app \\
        python oauth_login.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets as _secrets
import sys
import time
import urllib.parse
from pathlib import Path

import httpx
import jwt


SECRETS_DIR = Path(__file__).parent / "secrets"
PRIVATE_KEY_PATH = SECRETS_DIR / "private_key.pem"
REGISTRATION_PATH = SECRETS_DIR / "client_registration.json"
REFRESH_TOKEN_PATH = SECRETS_DIR / "refresh_token.json"
PENDING_AUTH_PATH = SECRETS_DIR / "pending_auth.json"

KEY_ID = "seed-loader-1"

# Must match exactly what was registered in bootstrap_oauth.py.
REDIRECT_URI = "https://localhost/seed-loader-noop"

# Scopes for the user-context (write-capable) token. `offline_access` is
# what makes OpenEMR return a refresh token. user/<Resource>.write is the
# v1 syntax OpenEMR's user-context advertises. api:oemr unlocks the
# Standard API (everything not exposed via FHIR write).
WRITE_SCOPES = " ".join([
    "openid",
    "fhirUser",
    "offline_access",
    "api:oemr",
    "api:fhir",
    # FHIR writes (Patient, Practitioner are the main ones we need)
    "user/Patient.write", "user/Patient.read",
    "user/Practitioner.write", "user/Practitioner.read",
    "user/Organization.write", "user/Organization.read",
    # FHIR reads — for verifying loaded state
    "user/Encounter.read",
    "user/Condition.read",
    "user/Observation.read",
    "user/MedicationRequest.read",
    "user/DocumentReference.read",
])


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _generate_pkce() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) for PKCE S256."""
    verifier = _b64url_no_pad(_secrets.token_bytes(48))
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _discover(base_url: str) -> dict:
    url = f"{base_url.rstrip('/')}/oauth2/default/.well-known/openid-configuration"
    r = httpx.get(url, timeout=15.0)
    r.raise_for_status()
    return r.json()


def _discover_smart(base_url: str) -> dict:
    """Fetch the SMART configuration for the FHIR base.

    The SMART `aud` parameter on the authorize URL must match the issuer
    OpenEMR self-advertises here, character-for-character. On Railway
    that means http:// (TLS terminated at edge), not https://.
    """
    url = f"{base_url.rstrip('/')}/apis/default/fhir/.well-known/smart-configuration"
    r = httpx.get(url, timeout=15.0)
    r.raise_for_status()
    return r.json()


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


def _exchange_code(
    *,
    base_url: str,
    code: str,
    state_returned: str,
    expected_state: str,
    code_verifier: str,
    client_id: str,
    private_key: bytes,
    token_endpoint: str,
) -> dict:
    if state_returned != expected_state:
        raise ValueError(
            f"State mismatch — possible CSRF. Expected {expected_state}, got {state_returned}"
        )
    post_token_url = f"{base_url.rstrip('/')}/oauth2/default/token"
    assertion = _build_client_assertion(client_id, private_key, token_endpoint)
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
    }
    response = httpx.post(
        post_token_url,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        print(f"Token exchange failed: HTTP {response.status_code}", file=sys.stderr)
        print(f"Response body: {response.text}", file=sys.stderr)
        print(f"client_id: {client_id}", file=sys.stderr)
        print(f"aud (token JWT): {token_endpoint}", file=sys.stderr)
        print(f"POST URL: {post_token_url}", file=sys.stderr)
        response.raise_for_status()
    return response.json()


def _save_tokens(result: dict) -> None:
    """Save the token response. Refresh tokens are optional on this OpenEMR build.

    OpenEMR's discovery advertises `grant_types_supported: [client_credentials,
    authorization_code]` — `refresh_token` is NOT supported, so no refresh token
    is ever returned regardless of whether `offline_access` is requested. We
    persist whatever the server gave us; the loader will mint a fresh access
    token via a new browser dance at the start of each seed run (access tokens
    are valid for ~1 hour, well past the seed run's duration).
    """
    if "refresh_token" not in result:
        print(
            "Note: server did not issue a refresh_token (this OpenEMR build does "
            "not support refresh_token grant). The access_token below is valid "
            "for the duration shown; mint a new one when it expires.",
            file=sys.stderr,
        )
    REFRESH_TOKEN_PATH.write_text(json.dumps(result, indent=2))
    REFRESH_TOKEN_PATH.chmod(0o600)


def _resume_from_paste_file(paste_file: Path, base_url: str) -> int:
    """Finish the dance using the URL stashed in a file + the on-disk pending state."""
    if not PENDING_AUTH_PATH.exists():
        print(
            f"ERROR: {PENDING_AUTH_PATH} not found. The PKCE verifier was lost — "
            "you must re-run oauth_login.py without --paste-file to start a fresh "
            "authorize round.",
            file=sys.stderr,
        )
        return 1

    pending = json.loads(PENDING_AUTH_PATH.read_text())
    pasted = paste_file.read_text().strip()
    if not pasted:
        print(f"ERROR: {paste_file} is empty.", file=sys.stderr)
        return 1

    parsed = urllib.parse.urlparse(pasted)
    qs = urllib.parse.parse_qs(parsed.query or pasted)
    if "code" not in qs:
        print(f"ERROR: no `code` in {paste_file}.", file=sys.stderr)
        return 1
    code = qs["code"][0]
    state_returned = qs.get("state", [""])[0]

    registration = json.loads(REGISTRATION_PATH.read_text())
    client_id = registration["client_id"]
    private_key = PRIVATE_KEY_PATH.read_bytes()
    discovery = _discover(base_url)

    print(f"Exchanging code (length {len(code)}) for tokens...")
    result = _exchange_code(
        base_url=base_url,
        code=code,
        state_returned=state_returned,
        expected_state=pending["state"],
        code_verifier=pending["code_verifier"],
        client_id=client_id,
        private_key=private_key,
        token_endpoint=discovery["token_endpoint"],
    )
    _save_tokens(result)
    PENDING_AUTH_PATH.unlink()

    print()
    print("Login succeeded.")
    print(f"  granted scope: {result.get('scope')}")
    print(f"  refresh_token saved to: {REFRESH_TOKEN_PATH}")
    return 0


def main() -> int:
    parser = __import__("argparse").ArgumentParser()
    parser.add_argument(
        "--paste-file",
        help=(
            "Read the redirect URL from a file instead of stdin. Use this when "
            "the terminal won't accept a long paste. Requires that an authorize "
            "URL was already printed in a prior run (the PKCE state lives in "
            "secrets/pending_auth.json)."
        ),
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        help=(
            "Reprint the previously-issued authorize URL (read from "
            "secrets/pending_auth.json). Use this when the auth code has "
            "expired but the PKCE state is still good — saves regenerating "
            "the verifier."
        ),
    )
    args = parser.parse_args()

    if args.print_url:
        if not PENDING_AUTH_PATH.exists():
            print(
                f"ERROR: {PENDING_AUTH_PATH} not found. Nothing to reprint.",
                file=sys.stderr,
            )
            return 1
        pending = json.loads(PENDING_AUTH_PATH.read_text())
        url = pending.get("authorize_url")
        if not url:
            print(
                "ERROR: no authorize_url in pending_auth.json (file is from an "
                "older script version). Run oauth_login.py without flags to "
                "start fresh.",
                file=sys.stderr,
            )
            return 1
        print("Open this URL, approve, copy redirected URL, run --paste-file fast:")
        print()
        print(url)
        return 0

    base_url = os.environ.get("OE_FHIR_BASE_URL")
    if not base_url:
        print("ERROR: set OE_FHIR_BASE_URL", file=sys.stderr)
        return 1

    if not REGISTRATION_PATH.exists():
        print("ERROR: run bootstrap_oauth.py first.", file=sys.stderr)
        return 1

    if args.paste_file:
        return _resume_from_paste_file(Path(args.paste_file), base_url)

    if REFRESH_TOKEN_PATH.exists():
        print(
            f"WARNING: {REFRESH_TOKEN_PATH} already exists. Continuing will overwrite it.",
            file=sys.stderr,
        )
        if input("Continue? [y/N] ").strip().lower() != "y":
            return 1

    registration = json.loads(REGISTRATION_PATH.read_text())
    client_id = registration["client_id"]
    private_key = PRIVATE_KEY_PATH.read_bytes()

    discovery = _discover(base_url)
    smart = _discover_smart(base_url)
    authorize_endpoint = discovery["authorization_endpoint"]
    token_endpoint = discovery["token_endpoint"]
    fhir_issuer = smart["issuer"]  # OpenEMR's self-advertised FHIR base — used as `aud`

    # When you POST through Railway HTTPS edge, the URL we hit is https://;
    # OpenEMR's discovery may still advertise http:// (TLS terminated at edge).
    # Use the advertised URL for `aud` and for the authorize-URL we hand to
    # the browser (browser-side this is fine because Railway will redirect
    # http→https automatically for any human navigation).
    post_token_url = f"{base_url.rstrip('/')}/oauth2/default/token"

    code_verifier, code_challenge = _generate_pkce()
    state = _secrets.token_urlsafe(16)

    authorize_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": WRITE_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # `aud` is the FHIR-base issuer SMART advertises. Must match
        # exactly — different scheme (http vs https) is a hard reject.
        "aud": fhir_issuer,
    }
    authorize_url = f"{authorize_endpoint}?{urllib.parse.urlencode(authorize_params)}"

    # Persist the PKCE verifier, state, AND the authorize URL BEFORE
    # printing the URL. That way: (a) a paste failure (long URL,
    # terminal limits) is recoverable via --paste-file; and (b) if the
    # auth code expires before paste, we can re-issue the same URL with
    # --print-url instead of re-running the whole authorize round.
    PENDING_AUTH_PATH.write_text(
        json.dumps(
            {
                "code_verifier": code_verifier,
                "state": state,
                "client_id": client_id,
                "authorize_url": authorize_url,
                "issued_at": int(time.time()),
            },
            indent=2,
        )
    )
    PENDING_AUTH_PATH.chmod(0o600)

    print()
    print("=" * 78)
    print("STEP 1: Open this URL in your browser, log in as admin, and approve.")
    print("=" * 78)
    print()
    print(authorize_url)
    print()
    print("=" * 78)
    print("STEP 2: After approving, your browser will redirect to a URL that")
    print(f"        starts with {REDIRECT_URI}?code=...&state=...")
    print("        The page will fail to load — that's expected.")
    print()
    print("        OPTION A: Copy the FULL redirected URL from the address bar")
    print("                  and paste it below.")
    print()
    print("        OPTION B: If your terminal won't accept the paste (long URL),")
    print("                  save it to a file and re-run with --paste-file:")
    print("                    pbpaste > /tmp/redirect.txt")
    print("                    OE_FHIR_BASE_URL=... python oauth_login.py \\")
    print("                       --paste-file /tmp/redirect.txt")
    print("=" * 78)
    print()
    print("(PKCE state saved to secrets/pending_auth.json — safe to Ctrl-C now.)")
    print()

    try:
        pasted = input("Paste the redirected URL (or Ctrl-C to use --paste-file later): ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        print("Aborted. To finish later, run:")
        print("  python oauth_login.py --paste-file <path-to-saved-url>")
        return 1

    if not pasted:
        print("ERROR: no input. To finish later, use --paste-file.", file=sys.stderr)
        return 1

    parsed = urllib.parse.urlparse(pasted)
    qs = urllib.parse.parse_qs(parsed.query or pasted)
    if "code" not in qs:
        print(f"ERROR: no `code` in input. Got: {pasted[:120]}", file=sys.stderr)
        return 1
    code = qs["code"][0]
    state_returned = qs.get("state", [""])[0]

    print(f"\nGot authorization code (length {len(code)}). Exchanging for tokens...")
    result = _exchange_code(
        base_url=base_url,
        code=code,
        state_returned=state_returned,
        expected_state=state,
        code_verifier=code_verifier,
        client_id=client_id,
        private_key=private_key,
        token_endpoint=token_endpoint,
    )
    _save_tokens(result)
    PENDING_AUTH_PATH.unlink()

    print()
    print("Login succeeded.")
    print(f"  token_type: {result.get('token_type')}")
    print(f"  expires_in: {result.get('expires_in')} seconds (access token)")
    print(f"  granted scope: {result.get('scope')}")
    print(f"  refresh_token saved to: {REFRESH_TOKEN_PATH}")
    print()
    print("Next: use `get_token.py --user` to mint write-capable access tokens")
    print("from the refresh token. No more browser steps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
