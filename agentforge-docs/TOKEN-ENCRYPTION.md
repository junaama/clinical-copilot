# Token encryption at rest

The agent backend persists OAuth bundles (`access_token`, `refresh_token`,
`id_token`) in `copilot_token_bundle`. These columns are encrypted at the
application layer using AES-256-GCM so a database read does not yield
working tokens.

This document is the operator's reference. The implementation lives in
`agent/src/copilot/token_crypto.py` and the storage wiring in
`agent/src/copilot/session.py`.

## Wire format

Each encrypted column stores:

```
enc1:<base64( nonce(12) || ciphertext || gcm_tag(16) )>
```

The `enc1:` prefix identifies the format version. A reader sees this prefix
and knows the rest of the column is ciphertext; absence of the prefix
identifies a row written before issue 009 shipped (legacy plaintext) and
triggers the one-shot migration. Future format bumps (algorithm change,
dual-key rollover) get their own prefix without breaking `enc1:` rows.

The nonce is 12 bytes per NIST SP 800-38D §8.2 and is generated fresh per
encrypt call from `os.urandom`. **Reusing a (key, nonce) pair with AES-GCM
is a catastrophic break** — see `test_encrypt_uses_fresh_nonce_per_call` in
`agent/tests/test_token_crypto.py`.

## Configuration

The key is supplied via the env var `COPILOT_TOKEN_ENC_KEY`. It must be 32
bytes (AES-256), base64-encoded. The agent backend fails to start when
`CHECKPOINTER_DSN` is set but `COPILOT_TOKEN_ENC_KEY` is missing, empty, not
base64, or the wrong length.

Generate a key:

```bash
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

Store it in your secret manager and inject it into the agent's environment.

In-memory mode (no `CHECKPOINTER_DSN`) does not persist tokens to disk and
intentionally does not require the key — dev and fixture flows work
without operator setup. This is asserted by
`test_lifespan_does_not_require_key_in_memory_mode`.

## Migration from plaintext

`open_session_store(dsn, encryptor=...)` runs `encrypt_existing_token_bundles()`
on entry. The migration:

1. Selects every row in `copilot_token_bundle`.
2. For each column, checks for the `enc1:` prefix.
3. Encrypts and rewrites only the columns that are still plaintext.

The migration is idempotent — a second pass is a no-op. Mixed-mode rows
(one column ciphertext, two plaintext) are handled correctly: the
already-encrypted column is left bit-for-bit unchanged, no fresh nonce is
applied. See `test_migration_leaves_already_encrypted_columns_untouched`.

## Operational behavior

A row whose ciphertext fails authentication (tampered, wrong key, or
unencrypted-where-ciphertext-was-expected) is **deleted** by
`get_token_bundle` and reported as missing. The user is forced through a
re-login on the next request rather than receiving an opaque crash. See
`test_tampered_ciphertext_drops_row_and_returns_none`.

The auth-failure log line carries `session_id` (for correlation) but never
the ciphertext or any portion of it. Exception strings end up in support
tickets and we do not want raw token material there.

## Key rotation (procedure — not implemented)

> **This procedure is documented but not implemented.** A rotation tool is
> out of scope for issue 009. The wire format (`enc1:` prefix) reserves
> room for a future `enc2:` so a rotation can be staged without breaking
> existing rows.

The rotation goal is to swap the data-encrypting key (DEK) without
invalidating in-flight sessions. The recommended path is **dual-key
reads during a transition window**:

1. **Provision the new key** (`COPILOT_TOKEN_ENC_KEY_V2`) alongside the
   existing one. Roll it out to all replicas via your secret manager.
2. **Update the encryptor** to a `DualKeyEncryptor` (to be added) that:
   - Encrypts new writes with the V2 key, emitting `enc2:<base64>`.
   - On read, dispatches by prefix: `enc1:` → V1 key, `enc2:` → V2 key.
3. **Run the rewrite migration**: a one-shot job that selects every row
   carrying `enc1:`, decrypts with V1, re-encrypts with V2, writes back
   `enc2:`. Idempotent — already-`enc2:` rows skip.
4. **Verify**: query the table for rows still carrying `enc1:`. Expected:
   zero. If any remain (e.g. session minted between step 2 and step 3),
   they will be rotated on next refresh because the bundle is rewritten
   on every refresh round-trip.
5. **Retire V1**: remove `COPILOT_TOKEN_ENC_KEY` from the secret manager
   and the deploy config; the DualKeyEncryptor falls back to single-key
   mode when only one variable is present. Optionally, keep V1 around
   for a rollback window.

A rotation that invalidates in-flight sessions (drop V1 immediately) is
also possible and trivially simple to implement — every active session is
forced to re-authenticate. The dual-key procedure above only matters when
that disruption is unacceptable.

The rotation tool itself (script under `agent/scripts/`, plus the
`DualKeyEncryptor` class) is the natural follow-up slice; this document
exists so the rotation story is part of the at-rest review even before
rotation ships.

## Threat model

Encrypts against:
- Database read by an attacker with `SELECT` on `copilot_token_bundle`.
- Database backup / export ending up in the wrong hands.
- A misconfigured replica / read-only mirror.

Does NOT defend against:
- An attacker with read access to both Postgres AND the env var (or the
  agent process memory). The key and the ciphertext together yield
  plaintext by design.
- A compromised agent process that holds the key in memory.
- A compromised OAuth client at the OpenEMR side — the tokens stored in
  this database are still valid bearer tokens at the EHR.

This is application-layer encryption, not a key-management system. For
production deployments, the key should live in a managed secret store
(AWS KMS, GCP KMS, HashiCorp Vault, or equivalent) and be retrieved at
agent startup, not embedded in `.env` files committed to source control.
