"""Application-layer AES-GCM encryption for token bundles at rest.

``copilot_token_bundle`` stores access tokens, refresh tokens, and id tokens
in TEXT columns. An attacker with read access to Postgres should not be able
to read those values; this module is the layer that makes that true.

Wire format (stored in the column):

    enc1:<base64( nonce || ciphertext || gcm_tag )>

- ``enc1:`` is a static version prefix. A migrating reader can distinguish a
  ciphertext row from a plaintext row written by issue 001 by looking for it.
  Future format bumps (algorithm change, dual-key rollover) get their own
  prefix without breaking ``enc1:`` rows.
- The nonce is 12 bytes (96 bits — the AES-GCM standard) and is generated
  fresh per ``encrypt`` call from ``os.urandom``. Reusing a (key, nonce)
  pair with AES-GCM is a catastrophic break, so the nonce must NEVER be
  derived from the plaintext or any session id.
- ``cryptography``'s ``AESGCM`` implementation appends the 16-byte auth tag
  to the ciphertext on encrypt and consumes it on decrypt. We treat the
  whole thing as opaque bytes after the nonce.

The key is 32 bytes (AES-256), supplied as a base64 string in the env var
``COPILOT_TOKEN_ENC_KEY``. ``load_encryptor_from_env`` is the only sanctioned
way to construct an encryptor in production — direct use of the constructor
exists for tests that need raw key bytes.

Errors:

- ``TokenEncryptionKeyInvalidError`` — the key is missing, empty, not
  base64, or the wrong length. Raised at module-loader time so misconfig
  fails the agent backend on startup, not on the first /chat request.
- ``TokenDecryptionError`` — AES-GCM auth-tag failure (tampered or
  wrong-key) or a malformed ciphertext (no prefix, too short to contain
  a nonce). Callers above the storage layer should treat the row as
  invalid (force re-login) and never log the raw ciphertext.
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 96-bit nonce per NIST SP 800-38D §8.2 — the recommended size for AES-GCM
# when using random nonces with this key for fewer than ~2^32 messages.
NONCE_LENGTH: Final[int] = 12

# AES-256 key length. The module accepts only 32 bytes — anything else is a
# misconfiguration that should fail loudly.
KEY_LENGTH: Final[int] = 32

# Stable wire-format prefix. A reader sees ``enc1:`` and knows the rest of
# the column is base64( nonce || ciphertext || tag ); anything else is
# either pre-encryption plaintext or a corrupt row.
WIRE_PREFIX: Final[str] = "enc1:"


class TokenEncryptionKeyInvalidError(RuntimeError):
    """Raised when the encryption key is missing, empty, or mis-shaped.

    The error message names the env variable but never echoes the raw
    value — bad keys are still secret.
    """


class TokenDecryptionError(RuntimeError):
    """Raised when ciphertext fails authentication or is malformed.

    AES-GCM is authenticated encryption: an ``InvalidTag`` from the
    underlying primitive means either the ciphertext was tampered with,
    the wrong key is in use, or the row was never encrypted at all.
    Callers should treat the row as invalid (force re-login) rather
    than try to recover.
    """


class TokenEncryptor:
    """AES-GCM encryptor with a single 32-byte key.

    ``encrypt`` and ``decrypt`` operate on Python ``str`` — UTF-8 encoded
    on the way in, UTF-8 decoded on the way out — so callers in the
    storage layer can pass the column value straight through without
    worrying about bytes vs str.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_LENGTH:
            raise TokenEncryptionKeyInvalidError(
                f"AES-256 key must be exactly {KEY_LENGTH} bytes; got {len(key)}"
            )
        self._aesgcm = AESGCM(key)

    @classmethod
    def from_base64_key(cls, key_b64: str) -> TokenEncryptor:
        """Construct from a base64-encoded 32-byte key.

        Raises ``TokenEncryptionKeyInvalidError`` if the value is empty,
        not valid base64, or does not decode to exactly 32 bytes.
        """
        if not key_b64:
            raise TokenEncryptionKeyInvalidError(
                "encryption key is empty; expected base64-encoded 32-byte key"
            )
        try:
            key = base64.b64decode(key_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TokenEncryptionKeyInvalidError(
                "encryption key is not valid base64"
            ) from exc
        return cls(key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt and return the wire-format string ``enc1:<base64>``.

        Generates a fresh nonce per call. Empty plaintext is supported —
        token endpoints occasionally omit ``id_token`` or
        ``refresh_token`` and we still want to round-trip the empty
        column.
        """
        nonce = os.urandom(NONCE_LENGTH)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        body = base64.b64encode(nonce + ciphertext).decode("ascii")
        return f"{WIRE_PREFIX}{body}"

    def decrypt(self, wire: str) -> str:
        """Decrypt a wire-format string. Raises ``TokenDecryptionError``.

        Raises:
            TokenDecryptionError: the value is missing the ``enc1:``
                prefix, is too short to contain a nonce, fails base64
                decoding, or fails AES-GCM authentication.
        """
        if not wire.startswith(WIRE_PREFIX):
            raise TokenDecryptionError(
                "ciphertext missing wire-format prefix"
            )
        body = wire[len(WIRE_PREFIX):]
        try:
            blob = base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TokenDecryptionError("ciphertext is not valid base64") from exc
        if len(blob) <= NONCE_LENGTH:
            raise TokenDecryptionError(
                "ciphertext is shorter than the nonce length"
            )
        nonce, ct = blob[:NONCE_LENGTH], blob[NONCE_LENGTH:]
        try:
            plaintext = self._aesgcm.decrypt(nonce, ct, None)
        except InvalidTag as exc:
            # Do NOT include the ciphertext or any portion of it in the
            # error message — operators paste exception strings into
            # tickets and we don't want to leak ciphertext that way.
            raise TokenDecryptionError(
                "ciphertext failed authentication"
            ) from exc
        return plaintext.decode("utf-8")

    def looks_like_ciphertext(self, value: str) -> bool:
        """True iff ``value`` carries the encryption wire-format prefix.

        Used by the migration to skip rows that are already encrypted —
        the migration is idempotent because a second pass is a no-op.
        """
        return value.startswith(WIRE_PREFIX)


def load_encryptor_from_env(
    env: Mapping[str, str],
    var: str = "COPILOT_TOKEN_ENC_KEY",
) -> TokenEncryptor:
    """Build a ``TokenEncryptor`` from a base64-encoded key in ``env[var]``.

    The agent backend calls this once at startup. A missing or
    mis-shaped key raises ``TokenEncryptionKeyInvalidError`` so the
    process fails to start rather than continuing with plaintext
    storage. The error message names ``var`` but never echoes the
    value, so a misconfigured key doesn't end up in logs.
    """
    raw = env.get(var)
    if not raw:
        raise TokenEncryptionKeyInvalidError(
            f"required env var {var} is not set; expected base64-encoded "
            "32-byte AES-256 key"
        )
    try:
        return TokenEncryptor.from_base64_key(raw)
    except TokenEncryptionKeyInvalidError as exc:
        # Re-raise with the env var name in the message; the underlying
        # exception (which intentionally does not contain the value)
        # remains chained as ``__cause__``.
        raise TokenEncryptionKeyInvalidError(
            f"env var {var} is invalid: {exc}"
        ) from exc
