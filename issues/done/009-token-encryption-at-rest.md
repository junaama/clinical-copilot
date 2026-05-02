## Parent PRD

`issues/prd.md`

## What to build

Application-layer AES-GCM encryption for the access token, refresh token, and id token stored in `copilot_token_bundle`, plus any token-bundle reference held on `copilot_session`. After this slice ships, an attacker with read access to the agent's Postgres cannot read tokens; the encryption key lives in an environment variable and is required for the agent backend to start.

This slice covers the encryption note in the PRD's *Schema changes* section and the encryption discipline called out in *Further Notes*. It is split out from `issues/001-standalone-login-shell.md` so that slice can ship first and unblock dependent work; this slice tightens the at-rest story before the audit deliverable lands.

## Acceptance criteria

- [x] New encryption module exposes `encrypt(plaintext: str) -> bytes` and `decrypt(ciphertext: bytes) -> str` using AES-GCM with a per-message nonce. The nonce is stored alongside the ciphertext (e.g., concatenated and base64-encoded into a single column) so decryption needs only the key and the stored value. *(Wire format is `enc1:<base64(nonce(12) || ciphertext || gcm_tag(16))>` — the prefix reserves room for a future `enc2:` rollover. Module: `agent/src/copilot/token_crypto.py`.)*
- [x] The encryption key is read from an environment variable (e.g., `COPILOT_TOKEN_ENC_KEY`). The key must be 32 bytes (256-bit) base64-encoded; mis-shaped keys fail loudly. *(`load_encryptor_from_env` rejects empty / non-base64 / wrong-length values with `TokenEncryptionKeyInvalidError`.)*
- [x] If the key env var is missing on agent startup, the backend fails to start with a clear error message naming the missing variable. There is no implicit fallback to plaintext, no implicit fallback to a derived key. *(Lifespan helper `_load_token_encryptor` in `server.py` runs only when `CHECKPOINTER_DSN` is set; in-memory mode does not require the key. Five startup-hook tests in `tests/test_token_crypto_lifespan.py` cover every failure mode.)*
- [x] `copilot_token_bundle.access_token`, `refresh_token`, and `id_token` columns store ciphertext. Reads decrypt transparently inside the gateway layer; callers above the gateway never see ciphertext. *(Wired into `PostgresSessionStore.put_token_bundle` / `get_token_bundle`; gateway and FastAPI handlers unchanged.)*
- [x] If `copilot_session` carries any token reference or sensitive material directly, that field is encrypted with the same key. *(`SessionRow` carries no token / sensitive material — only the FK-shaped `oe_user_id`, `display_name`, `fhir_user`, and timestamps. Verified by reading the dataclass shape; no encryption needed there.)*
- [x] Migration: any existing rows from `issues/001-standalone-login-shell.md` (which stored plaintext) are encrypted in place by the migration. After migration, no plaintext token remains in the database. *(`encrypt_existing_token_bundles()` runs automatically when `open_session_store(dsn, encryptor=...)` enters; idempotent; mixed-mode rows handled.)*
- [x] Tampered ciphertext is detected on decryption: AES-GCM auth failure raises a typed error and the caller treats the row as invalid (forces a fresh login). The error is logged with conversation/session id but the ciphertext is not logged. *(`get_token_bundle` deletes the unrecoverable row and returns `None`; log line carries `session_id` only.)*
- [x] A documented (not implemented) key-rotation procedure exists in `agentforge-docs/` describing how to rotate the key without invalidating sessions in flight (e.g., dual-key reads during a transition window). Implementation of rotation is out of scope. *(`agentforge-docs/TOKEN-ENCRYPTION.md` — wire format, configuration, migration, threat model, and a five-step dual-key rotation procedure.)*
- [x] Tests: round-trip (encrypt then decrypt returns the original plaintext); mis-shaped key fails on module import or first call; tampered ciphertext raises the typed error; missing env var fails the agent's startup hook; migration test that takes a fixture row of plaintext, runs the migration, and verifies the row is now encrypted and still decryptable. *(18 unit tests in `test_token_crypto.py`, 5 startup tests in `test_token_crypto_lifespan.py`, 6 Postgres-gated integration tests in `test_postgres_session_store.py` covering ciphertext-in-table, migration, idempotence, mixed-mode, tamper-and-drop.)*

## Progress notes

### 2026-05-02 — Token encryption-at-rest landed

**Module: `agent/src/copilot/token_crypto.py`** — `TokenEncryptor`,
`load_encryptor_from_env`, `TokenEncryptionKeyInvalidError`,
`TokenDecryptionError`. AES-256-GCM with 12-byte random nonce per
message; wire format `enc1:<base64(nonce || ciphertext || tag)>`. The
versioned prefix reserves room for a future `enc2:` rollover and lets
the migration distinguish ciphertext from plaintext rows
unambiguously. Empty plaintext is supported (token endpoints sometimes
omit `id_token`).

**Storage wiring: `agent/src/copilot/session.py`** — `PostgresSessionStore`
takes an optional `encryptor` keyword. When set, `put_token_bundle`
encrypts the three token columns on write; `get_token_bundle`
decrypts on read. A row whose ciphertext fails authentication is
deleted (forcing re-login on the next attempt) and the failure is
logged with `session_id` only — the ciphertext never reaches the log
line. Callers above the store see plaintext tokens transparently.

**Migration: `encrypt_existing_token_bundles()`** — runs automatically
on `open_session_store(dsn, encryptor=...)` entry. Idempotent; rows
that already carry the `enc1:` prefix are skipped. Mixed-mode rows
(some columns ciphertext, some plaintext) only re-encrypt the
plaintext columns — already-encrypted columns are left bit-for-bit
unchanged so we don't burn fresh nonces unnecessarily.

**Startup hook: `agent/src/copilot/server.py`** — `_load_token_encryptor`
is called inside `lifespan` immediately before `open_session_store`
when `CHECKPOINTER_DSN` is set. A missing or mis-shaped
`COPILOT_TOKEN_ENC_KEY` raises `TokenEncryptionKeyInvalidError`
naming the env var; the `TestClient` context manager surfaces this
as a clean lifespan failure. In-memory mode (no DSN) does not require
the key — fixture / dev flows keep working without operator setup.

**Config: `agent/src/copilot/config.py`** — `Settings.token_enc_key`
(SecretStr, alias `COPILOT_TOKEN_ENC_KEY`). The Settings field is
optional at the pydantic level so in-memory mode boots without it;
the lifespan-level check enforces presence on the persistence path.

**Docs: `agentforge-docs/TOKEN-ENCRYPTION.md`** — wire format,
configuration / key generation, migration behaviour, operational
behaviour on tamper, threat model, and a five-step dual-key
rotation procedure (documented; implementation deferred).

Key decisions:

- **Versioned wire prefix `enc1:` rather than column-shape change.** No
  DDL — column stays TEXT. A reader can tell ciphertext from
  plaintext at a glance, the migration's idempotence falls out for
  free, and a future `enc2:` (algorithm bump or dual-key) ships
  without breaking `enc1:` rows.

- **Encryption optional at the store layer (encryptor=None →
  passthrough).** Lets the in-memory path stay encryption-free
  without conditional code, and lets the existing in-memory tests
  in `test_session.py` stay unchanged. The Postgres path is the
  only place where production data is at risk, so that's the only
  place where the key is mandatory.

- **Tampered row → delete + None, not raise.** The caller (FastAPI
  handler) treats this identically to "no bundle" — the user is
  routed through `/auth/login`. Raising would force every caller
  above the store to add a try/except that maps to the same
  re-login UX. Deleting the row also makes the next attempt cheap;
  a poisoned row doesn't keep failing forever.

- **`cryptography` moved from `seed` extra to core dependency.**
  Required at agent backend startup whenever `CHECKPOINTER_DSN` is
  set — the package can't be optional. The `seed` extra still
  pulls `pyjwt[crypto]` for the OAuth client-registration script.

- **Empty-plaintext round-trip supported.** Token endpoints
  occasionally return no `id_token` or `refresh_token`; we want
  every non-null column to live in the same encrypted shape rather
  than have a special "empty stays empty" branch. AES-GCM handles
  the empty input fine (the auth tag is what makes the ciphertext
  non-empty).

- **`SessionRow` not encrypted.** AC mentions encrypting any
  sensitive material on `copilot_session` — the dataclass carries
  only `oe_user_id`, `display_name`, `fhir_user` (Practitioner
  reference, not a credential), and timestamps. None of those are
  bearer-credentials that yield EHR access. Encrypting them would
  add cost without adding defence.

- **Startup-hook test path stubs `open_session_store` and
  `open_conversation_store`.** The point of the test is to verify
  the key check fires *before* any DB connection, so the hook
  doesn't need a real Postgres. Five tests cover: missing var,
  wrong-length value, non-base64 value, valid-key-and-DSN happy
  path, and in-memory-mode-doesn't-require-key.

Files changed:

- `agent/src/copilot/token_crypto.py` (new) — encryption module
- `agent/src/copilot/session.py` — `PostgresSessionStore` accepts
  `encryptor`; put/get/migrate methods; `open_session_store` runs
  migration on entry
- `agent/src/copilot/server.py` — `_load_token_encryptor` helper;
  lifespan passes encryptor to `open_session_store` on Postgres path
- `agent/src/copilot/config.py` — `Settings.token_enc_key` field
- `agent/pyproject.toml` — `cryptography>=43.0.0` moved from `seed`
  extra to core deps
- `agent/.env.example` — `COPILOT_TOKEN_ENC_KEY` documented
- `agent/tests/test_token_crypto.py` (new, 18 cases) — round-trip,
  fresh-nonce-per-call, empty / unicode plaintext, tampered /
  truncated / wrong-prefix / wrong-key ciphertext, key-validation
  (short / long / non-base64 / empty), env-loader (missing /
  empty / value-not-in-error / custom var name)
- `agent/tests/test_token_crypto_lifespan.py` (new, 5 cases) —
  startup hook fails loudly on missing / wrong-length / non-base64
  key; succeeds with valid key; doesn't require key in memory mode
- `agent/tests/test_postgres_session_store.py` — adds 6 cases
  (ciphertext in column / migration round-trip / migration
  idempotent / tamper-drops-row / mixed-mode rows / direct pool +
  encryptor construction); existing lifespan test now sets the
  env var to remain green
- `agentforge-docs/TOKEN-ENCRYPTION.md` (new) — wire format,
  config, migration, threat model, dual-key rotation procedure

Tests: 311 unit tests pass (was 291; +18 token_crypto + 5
token_crypto_lifespan − 3 ignored set count adjustments) excluding the
Postgres-required files. With Postgres available, all 17 cases in
`test_postgres_session_store.py` pass (was 11; +6 encryption
integration). Ruff clean on changed files; pre-existing ruff errors
in untouched code remain unchanged.

Notes for next iteration:

- Key rotation tooling (`DualKeyEncryptor`, `agent/scripts/rotate_key.py`)
  is the natural follow-up — wire format already reserves the `enc2:`
  prefix and the migration shape composes one-for-one. Docs describe
  the five-step procedure; implementation is deferred.
- EHR-launch flow's in-memory `SmartStores` (separate from
  `SessionGateway`) is not encrypted — but that path is in-process
  only, not persisted to disk, so the at-rest property is moot
  there. When the EHR-launch path is unified onto `SessionGateway`
  (mentioned in the issue 001 commit's notes), it picks up
  encryption automatically.
- A future schema migration that adds session-level secrets (e.g.
  encrypted MFA challenge state) should reuse `TokenEncryptor` —
  the `enc1:` wire format is column-agnostic.

## Blocked by

- Blocked by `issues/001-standalone-login-shell.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 25
