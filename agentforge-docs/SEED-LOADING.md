# Seeding production OpenEMR with synthetic patient data

**Last verified:** 2026-05-01
**Target:** `https://openemr-production-c5b4.up.railway.app` (Railway service `openemr`)

This is the operational runbook for getting realistic patient data into the deployed OpenEMR before a demo. The agent itself never writes; this document covers the offline seeding step.

---

## TL;DR

```bash
railway ssh --service openemr \
  '. /root/devtoolsLibrary.source && prepareVariables && importRandomPatients 50 true'
```

That's the whole recipe. ~25 minutes for 50 patients on first run, ~20 minutes on subsequent runs (Java + Synthea jar are cached).

The second argument **must** be `true` — see [Why dev mode is required](#why-dev-mode-is-required) below.

---

## What's currently loaded in prod

As of 2026-05-01, three patients are loaded as a smoke test:

| pid | Name | DOB | Sex | Conditions | Encounters | MedRequests | Observations |
|---:|---|---|---|---:|---:|---:|---:|
| 1 | Eunice Stiedemann | 1972-10-02 | F | 43 | 40 | 14 | 79 |
| 2 | Lucius Hartmann | 1978-05-12 | M | 40 | 57 | 22 | 76 |
| 3 | Verlie Cronin | 1970-08-16 | F | 26 | 22 | 2 | 110 |

All visible via FHIR. FHIR Patient IDs are the OpenEMR-minted UUIDs (e.g., `a1abeabb-0127-494a-9561-5d89a7a86474`).

---

## How it works

OpenEMR's production image ships a shell library at `/root/devtoolsLibrary.source` that includes an `importRandomPatients` function. The function:

1. **Installs Java if missing** — `apk add openjdk17-jre` (Alpine package manager).
2. **Downloads Synthea** — `synthea-with-dependencies.jar` from the `synthetichealth/synthea` GitHub releases (`master-branch-latest` tag). Cached at `/root/synthea/synthea-with-dependencies.jar` for subsequent runs.
3. **Generates N patients as CCDA** — `java -jar synthea-with-dependencies.jar --exporter.fhir.export false --exporter.ccda.export true --generate.only_alive_patients true -p N`. Output goes to `/root/synthea/output/ccda/`.
4. **Imports via OpenEMR's CCDA importer** — `php contrib/util/ccda_import/import_ccda.php --sourcePath=/root/synthea/output/ccda --site=default --openemrPath=/var/www/localhost/htdocs/openemr --isDev=true`.
5. **Creates UUID registry entries** so the imported patients are visible via the FHIR API.

The function is documented at line 234 of `/root/devtoolsLibrary.source` (signature: `importRandomPatients <count> <isDev>`).

---

## Step-by-step

### Prerequisites

- `railway` CLI installed and authenticated (`railway login` once).
- The local repo linked to the Gauntlet AI Railway project (`railway link`, select `openemragent`).
- Container has internet egress (it does — needed to download Synthea jar).

### 1. Run the import

```bash
cd /Users/macbook/dev/Gauntlet/week1/openemragent

railway ssh --service openemr \
  '. /root/devtoolsLibrary.source && prepareVariables && importRandomPatients 50 true'
```

You'll see Synthea generate each patient (`N -- Name (age y/o sex) Town, Massachusetts`), followed by `System has successfully imported CCDA number: N` for each, then `Started uuid creation` / `Completed uuid creation`. End state: `Completed run for following number of random patients: N`.

Total time scales linearly with patient count: roughly 10 seconds per patient end-to-end (generation + CCDA parse + DB inserts + UUID registry).

### 2. Verify via FHIR

Mint a system-context read token and hit the Patient list:

```bash
cd agent
OE_FHIR_BASE_URL=https://openemr-production-c5b4.up.railway.app \
  uv run python scripts/seed/get_token.py --system

TOKEN=$(jq -r .access_token scripts/seed/secrets/last_token.json)
curl -s -H "Authorization: Bearer $TOKEN" -H "Accept: application/fhir+json" \
  "https://openemr-production-c5b4.up.railway.app/apis/default/fhir/Patient?_count=10" \
  | jq '.total, .entry[].resource | {id, name: .name[0]}'
```

If you see `total: 50` and a list of patient names with UUIDs, the seed is in.

### 3. (Optional) Verify the clinical depth

```bash
PID=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "https://openemr-production-c5b4.up.railway.app/apis/default/fhir/Patient?_count=1" \
  | jq -r '.entry[0].resource.id')

for resource in Condition Encounter MedicationRequest Observation; do
  N=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "https://openemr-production-c5b4.up.railway.app/apis/default/fhir/$resource?patient=$PID&_count=200" \
    | jq '.entry | length')
  echo "$resource: $N"
done
```

Expected: dozens to hundreds per resource per patient (Synthea generates lifetime histories from birth to current age).

---

## Why dev mode is required

The CCDA import script (`contrib/util/ccda_import/import_ccda.php`) has two modes:

- `--isDev=false` → CCDAs land in the `documents` table tagged for **manual review**. An admin must open OpenEMR's UI, walk through each one, and click "Match patient or create new" to actually create the patient. Ships zero rows in `patient_data` after a headless run. We hit this on the first attempt: 3 documents in `documents` table, 0 patients in `patient_data`.

- `--isDev=true` → bypasses the manual review queue and creates patients directly. This is what the OpenEMR dev tooling itself uses for `add-random-patients`.

For a synthetic-data deployment, `true` is the right choice. For a real-PHI deployment, `false` is the right choice.

---

## Reset / re-seed

If you want to start over with a different patient count:

```bash
# Wipe everything (patients, encounters, observations, etc.) but keep auth/admin
railway ssh --service openemr 'mariadb --skip-ssl -u openemr --password="$MYSQL_PASS" \
  -h "$MYSQL_HOST" -P "$MYSQL_PORT" openemr -e "
DELETE FROM patient_data;
DELETE FROM form_encounter;
DELETE FROM forms;
DELETE FROM lists;
DELETE FROM prescriptions;
DELETE FROM form_vitals;
DELETE FROM uuid_registry WHERE table_name IN (\"patient_data\",\"form_encounter\",\"lists\",\"prescriptions\",\"form_vitals\");
DELETE FROM documents WHERE mimetype = \"text/xml\" AND name LIKE \"%.xml\";
"'

# Then re-run the import
railway ssh --service openemr \
  '. /root/devtoolsLibrary.source && prepareVariables && importRandomPatients 50 true'
```

(The wipe SQL is approximate — there are ~20 tables touched by patient creation. For a true clean slate, use OpenEMR's `dev-reset-install-demodata` devtool, but that's only available on the dev image, not production.)

---

## Known limitations of this approach

### 1. Filtered FHIR queries currently return 0

The agent's tools use filters like `Condition?patient={id}&clinical-status=active` and `Observation?patient={id}&category=vital-signs&date=ge{since}`. Against the Synthea-loaded data these all return empty even though unfiltered queries find the rows. Either the imported `clinicalStatus` values don't match what the filter expects, or OpenEMR's FHIR layer doesn't honor those SearchParameters — diagnosis pending.

Workaround for now: query without the filter and post-filter in Python on the agent side.

### 2. `_summary=count` not implemented

OpenEMR's FHIR module doesn't honor the `_summary=count` parameter. Always returns 0 or the full bundle, not a count-only response. UC-1 Stage 1's lightweight probe (`ARCHITECTURE.md` §10) needs to use `_count=1` and read the bundle's `total` field instead.

### 3. No DocumentReference (clinical notes)

`importRandomPatients` runs Synthea with `--exporter.fhir.export false`. Synthea's templated DocumentReferences therefore aren't emitted. CCDA-imported patients have rich structured data but **no narrative notes**.

For UC-2's "what happened overnight" demo (Eduardo's 03:14 hypotensive event, the held lisinopril, the cross-cover physician note), the narrative layer needs to be authored separately. There's no working write path for that yet — see the `## Outstanding issues` section of `agentforge-docs/SEED-DATA-TODO.md`.

### 4. Synthea data is geographically Massachusetts-flavored

Default Synthea config targets MA (towns, providers, demographics distributions). Not a problem for a clinical demo, but if you need geographic diversity, edit `synthea.properties` before running.

---

## Why this path (and not OAuth-based seeding)

The originally planned approach was: register a SMART Backend Services OAuth client, mint a token with `system/<Resource>.write` scopes, POST FHIR resources directly. We invested significant effort in this and hit three structural blockers in OpenEMR:

- `client_credentials` grant is hardcoded for SMART Bulk FHIR (read-only by design — `src/Common/Auth/OpenIDConnect/Grant/CustomClientCredentialsGrant.php`). System scopes are advertised as `.rs` only.
- `password` grant issues tokens but doesn't create the `TrustedUser` record the resource server requires (`BearerTokenAuthorizationStrategy.php` line 169) — every API call returns 401.
- `authorization_code` grant works but the deployed OpenEMR doesn't issue refresh tokens, and even when the dance succeeds, the `user/<Resource>.write` scopes are filtered out unless they're on the registered client (and registering them requires the admin to manually re-approve).

Full dead-end notes: `agentforge-docs/SEED-DATA-TODO.md` → "What we tried that didn't work."

OpenEMR's auth model is designed for read-only backend services and interactive SMART apps. There is no working pattern for non-interactive bulk WRITES via the public API. Using the bundled `importRandomPatients` function is the path real OpenEMR power users take, and it routes through OpenEMR's internal services — same validation, same audit hooks, same UUID registry. **The result is more correct than what an external API client would produce.**

---

## File references

| File | Purpose |
|---|---|
| `/root/devtoolsLibrary.source` (in container) | The shell function library; `importRandomPatients` is at line 234 |
| `/var/www/localhost/htdocs/openemr/contrib/util/ccda_import/import_ccda.php` | The PHP CCDA importer |
| `agent/scripts/seed/get_token.py` | Mints a system-context FHIR read token for verification |
| `agent/scripts/seed/secrets/` | Local-only client credentials for the system token; gitignored |
| `agentforge-docs/SYNTHEA-AUDIT.md` | Synthea data-quality gap analysis (separate from this runbook) |
| `agentforge-docs/SEED-DATA-TODO.md` | Outstanding seed-data tasks and open questions |
