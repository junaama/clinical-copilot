"""Token encryption module — unit tests.

Covers the symmetric AES-GCM module that wraps token columns in
``copilot_token_bundle``. The module's contract:

- ``TokenEncryptor.encrypt(plaintext)`` → wire-format string with a nonce
  embedded so decryption needs only the key plus the stored value.
- ``TokenEncryptor.decrypt(ciphertext)`` → original plaintext, or raises
  ``TokenDecryptionError`` when the auth tag fails.
- ``load_encryptor_from_env(env, var)`` → builds an encryptor from a
  base64-encoded 32-byte key, raising ``TokenEncryptionKeyInvalidError``
  for missing or mis-shaped values. The error message names the env var
  but never echoes the value.

The integration with ``PostgresSessionStore`` lives in the postgres-gated
test file; this file only exercises the pure crypto.
"""

from __future__ import annotations

import base64

import pytest

from copilot.token_crypto import (
    TokenDecryptionError,
    TokenEncryptionKeyInvalidError,
    TokenEncryptor,
    load_encryptor_from_env,
)


def _b64_key(num_bytes: int) -> str:
    """Helper: emit a base64-encoded all-zero key of the given length."""
    return base64.b64encode(b"\x00" * num_bytes).decode("ascii")


# ---------- Roundtrip ----------


def test_encrypt_decrypt_roundtrip_returns_original_plaintext() -> None:
    enc = TokenEncryptor.from_base64_key(_b64_key(32))
    plaintext = "ya29.a0AbVbY6S-some-realistic-token-shape"
    ciphertext = enc.encrypt(plaintext)
    assert enc.decrypt(ciphertext) == plaintext


def test_encrypt_emits_versioned_prefix() -> None:
    """Wire format is ``enc1:<base64>`` so a migrating reader can
    distinguish ciphertext from a plaintext row written by issue 001."""
    enc = TokenEncryptor.from_base64_key(_b64_key(32))
    ciphertext = enc.encrypt("hello")
    assert ciphertext.startswith("enc1:")


def test_encrypt_uses_fresh_nonce_per_call() -> None:
    """Two encryptions of the same plaintext must produce distinct
    ciphertexts. AES-GCM is deterministic given a (key, nonce) pair —
    nonce reuse with the same key is a catastrophic break."""
    enc = TokenEncryptor.from_base64_key(_b64_key(32))
    a = enc.encrypt("same plaintext")
    b = enc.encrypt("same plaintext")
    assert a != b
    # Both round-trip back to the same plaintext.
    assert enc.decrypt(a) == "same plaintext"
    assert enc.decrypt(b) == "same plaintext"


def test_roundtrip_handles_empty_string() -> None:
    """Some token endpoints omit ``id_token`` or ``refresh_token`` entirely;
    we store the empty string in those columns and need to round-trip it."""
    enc = TokenEncryptor.from_base64_key(_b64_key(32))
    assert enc.decrypt(enc.encrypt("")) == ""


def test_roundtrip_handles_unicode() -> None:
    enc = TokenEncryptor.from_base64_key(_b64_key(32))
    plaintext = "tøken·with·μnicodè-characters✓"
    assert enc.decrypt(enc.encrypt(plaintext)) == plaintext


# ---------- Tampered ciphertext ----------


def test_tampered_ciphertext_raises_typed_error() -> None:
    enc = TokenEncryptor.from_base64_key(_b64_key(32))
    ciphertext = enc.encrypt("sensitive-token")
    # Flip a bit in the base64-encoded payload (after the prefix).
    prefix, body = ciphertext.split(":", 1)
    tampered_body = body[:-2] + ("A" if body[-1] != "A" else "B") + body[-1]
    tampered = f"{prefix}:{tampered_body}"
    with pytest.raises(TokenDecryptionError):
        enc.decrypt(tampered)


def test_truncated_ciphertext_raises_typed_error() -> None:
    """A ciphertext that's shorter than the nonce length is malformed —
    surface as ``TokenDecryptionError`` so the caller treats the row as
    invalid rather than crashing with an opaque ``ValueError``."""
    enc = TokenEncryptor.from_base64_key(_b64_key(32))
    with pytest.raises(TokenDecryptionError):
        enc.decrypt("enc1:")


def test_missing_prefix_raises_typed_error() -> None:
    """A row without the ``enc1:`` prefix is treated as not-yet-encrypted
    plaintext at the migration boundary; here at the decrypt boundary it
    is invalid — the caller should not silently accept arbitrary bytes."""
    enc = TokenEncryptor.from_base64_key(_b64_key(32))
    with pytest.raises(TokenDecryptionError):
        enc.decrypt("just-plaintext-no-prefix")


def test_wrong_key_raises_typed_error() -> None:
    enc_a = TokenEncryptor.from_base64_key(_b64_key(32))
    enc_b = TokenEncryptor.from_base64_key(
        base64.b64encode(b"\x01" * 32).decode("ascii")
    )
    ciphertext = enc_a.encrypt("a-token")
    with pytest.raises(TokenDecryptionError):
        enc_b.decrypt(ciphertext)


# ---------- Key validation ----------


def test_short_key_raises_invalid_key_error() -> None:
    with pytest.raises(TokenEncryptionKeyInvalidError):
        TokenEncryptor.from_base64_key(_b64_key(16))  # 128-bit, not 256


def test_long_key_raises_invalid_key_error() -> None:
    with pytest.raises(TokenEncryptionKeyInvalidError):
        TokenEncryptor.from_base64_key(_b64_key(48))


def test_non_base64_key_raises_invalid_key_error() -> None:
    with pytest.raises(TokenEncryptionKeyInvalidError):
        TokenEncryptor.from_base64_key("not!base64@@@")


def test_empty_key_raises_invalid_key_error() -> None:
    with pytest.raises(TokenEncryptionKeyInvalidError):
        TokenEncryptor.from_base64_key("")


# ---------- Env loader ----------


def test_load_from_env_happy_path() -> None:
    env = {"COPILOT_TOKEN_ENC_KEY": _b64_key(32)}
    enc = load_encryptor_from_env(env)
    assert enc.decrypt(enc.encrypt("x")) == "x"


def test_load_from_env_missing_var_fails_loudly() -> None:
    with pytest.raises(TokenEncryptionKeyInvalidError) as exc_info:
        load_encryptor_from_env({})
    # The error message names the env var — operator can act on it
    # without having to read the source.
    assert "COPILOT_TOKEN_ENC_KEY" in str(exc_info.value)


def test_load_from_env_empty_var_fails_loudly() -> None:
    with pytest.raises(TokenEncryptionKeyInvalidError):
        load_encryptor_from_env({"COPILOT_TOKEN_ENC_KEY": ""})


def test_load_from_env_does_not_log_value_in_error() -> None:
    """A mis-shaped key must surface the variable name but never the
    raw value (an operator could otherwise paste the bad key into
    chat / a ticket)."""
    bad_value = "deadbeef-NOT-a-real-key-but-still-secret"
    with pytest.raises(TokenEncryptionKeyInvalidError) as exc_info:
        load_encryptor_from_env({"COPILOT_TOKEN_ENC_KEY": bad_value})
    assert bad_value not in str(exc_info.value)


def test_load_from_env_custom_var_name() -> None:
    env = {"MY_OTHER_KEY": _b64_key(32)}
    enc = load_encryptor_from_env(env, var="MY_OTHER_KEY")
    assert enc.decrypt(enc.encrypt("x")) == "x"
