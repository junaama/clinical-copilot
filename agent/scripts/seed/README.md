# Seed loader for OpenEMR FHIR (Railway production)

Tools for getting a Synthea + hand-authored FHIR seed into the deployed
OpenEMR instance via the production-grade SMART Backend Services flow.

## What lives here

| File | Purpose |
|---|---|
| `bootstrap_oauth.py` | One-time: generates RSA keypair, registers a backend-services client at `/oauth2/default/registration`. |
| `get_token.py` | Reusable: signs a JWT with the private key, exchanges it for a short-lived bearer token. |
| `seed_careteam.py` | Idempotent: creates `dr_smith` user and assigns them to ~half of existing patients via `care_teams` + `care_team_member` DB inserts. |
| `seed_load.py` | (Coming next) Loads Synthea bundles + hand-authored "today" layer + adversarial layer into the FHIR store. |
| `secrets/` | Gitignored. Holds the private key, the `client_id` from registration, and any other credentials. Never commit. |

## Order of operations

1. `python bootstrap_oauth.py` — once, ever (until you rotate keys).
2. Log into OpenEMR admin → API Clients → find the new client → Enable it and grant the requested scopes.
3. `python get_token.py` — sanity-check that the token exchange works.
4. `python seed_load.py` — load the data. Idempotent; safe to re-run.

## Required environment

- `OE_FHIR_BASE_URL` — e.g. `https://openemr-production-c5b4.up.railway.app`
  (The base; the scripts append `/oauth2/default/...` and `/apis/default/fhir/...`.)
