# Data Quality Audit — Raw Findings

Audit target: OpenEMR codebase at `/Users/macbook/dev/Gauntlet/week1/openemragent/`.
Schema source: `sql/database.sql` (15,395 lines, schema v_database 538). Seed source:
`sql/example_patient_data.sql` (14 INSERTs into `patient_data` only) and
`sql/example_patient_users.sql` (2 provider users). Test fixtures live in
`tests/Tests/Fixtures/*.php` and are only loaded by PHPUnit, not by `setup.php`.

The Clinical Co-Pilot agent will read patient records that are mostly *empty
shells* in the default install, with about a dozen demographics rows and **no
seeded encounters, vitals, problems, allergies, medications, immunizations, labs,
or notes**. Anything the agent quotes will need to either come from a custom
seed/test fixture, the clinic's real data, or a synthetic backfill.

## Summary

- **The seed has demographics only.** `sql/example_patient_data.sql` inserts 14
  rows into `patient_data` and nothing else. There are zero seed rows in
  `lists`, `forms`, `form_encounter`, `form_vitals`, `prescriptions`,
  `immunizations`, `procedure_*`, `pnotes`, `history_data`, `form_clinical_notes`.
  The agent cannot demo retrieval-from-record without first loading fixtures or
  generating data.
- **`lists` is the most concept-overloaded table the agent will read.** A single
  row's `type` column (no enum, free-text varchar(255), `sql/database.sql:7675`)
  decides whether it's a problem, allergy, medication, surgery, dental, medical
  device, health concern, or IPPF-specific category. Same row shape, different
  semantics; the agent must always filter `WHERE type='medical_problem'`,
  `'medication'`, `'allergy'`, etc.
- **Medications live in two parallel tables** (`prescriptions` and
  `lists` with `type='medication'`), and `PrescriptionService::getBaseSql`
  (`src/Services/PrescriptionService.php:91-260`) UNIONs them. A single drug
  the patient is on may exist in both. Field coverage is asymmetric:
  `lists`-style meds have NULL `unit`, `interval`, `route`, `quantity`, `dosage`,
  `rxnorm_drugcode`. The agent cannot assume a "Lisinopril 10mg" record came
  with structure.
- **Coded problem/allergy diagnoses are stored as a delimited string, not as
  rows.** `lists.diagnosis varchar(255)` (`sql/database.sql:7687`) holds
  `ICD10:E11.9;SNOMED-CT:73211009` style multi-system codes parsed by
  `BaseService::addCoding()` (`src/Services/BaseService.php:551-573`). Both
  ICD10 and SNOMED can coexist on one row; neither is canonical. Agent must
  parse the string and decide which system to quote.
- **Soft-delete is inconsistent.** Some tables use `activity tinyint(4)`
  (1=active), others `active`, others `deleted tinyint`. `lists.activity`
  is nullable (`sql/database.sql:7688`), `forms.deleted` defaults 0
  (`sql/database.sql:2470`), `prescriptions.active` defaults 1
  (`sql/database.sql:8725`). No `deleted_at` timestamp anywhere. Old "current"
  rows can persist forever without ever being marked inactive.
- **There is no single source of truth for a problem.** `issue_types` lists
  problem categories (`sql/database.sql:3478-3490`); the same patient problem
  can exist in `lists` (with `type='medical_problem'`), be linked to encounters
  via `issue_encounter` (`sql/database.sql:3437`), and have its codes echoed
  in `lists.diagnosis`, `lists.title`, `form_clinical_notes.code`, and
  `forms.issue_id`. Five places, no enforced consistency.
- **The agent's biggest free-text dumping grounds:** `pnotes.body longtext`
  (`sql/database.sql:8673`), `form_soap.subjective/objective/assessment/plan`
  (`sql/database.sql:2404-2407`), `form_dictation.dictation longtext`
  (`sql/database.sql:2010`), `lists.comments longtext`
  (`sql/database.sql:7689`), and 100+ `history_*` and `usertext*` columns on
  `history_data`. These are the richest source of clinical narrative AND the
  highest hallucination risk surface.
- **Demographics are PSR-broken.** `patient_data` has 14 different fields for
  sex/gender/orientation/pronouns (`sex`, `sex_identified`, `gender_identity`,
  `sexual_orientation`, `pronoun`), most of which are TEXT not enum. In the
  seed, 4 of 14 patients have `sex=''`, one has title='Mr.' but sex='Female',
  language values are lowercase-english but the lookup expects 'English'
  (capital E). The agent cannot trust `patient_data.sex` alone.

## patient_data

`sql/database.sql:8334-8472`. The patient identity row. Single row per patient
keyed by both `id` (autoincrement) and `pid` (the public/legacy ID, which is
what every other table joins on). UUID is BINARY(16) and can be NULL until a
backfill job runs.

### PHI-bearing columns (selected)

| Column | Type | NULL? | Notes |
|---|---|---|---|
| `fname` / `lname` / `mname` | varchar(255) | NOT NULL default '' | Free-text. No length validation.|
| `DOB` | date | nullable | The only date-typed identity field; rest of patient_data is varchar/text. |
| `ss` | varchar(255) | NOT NULL default '' | SSN. Free-text — seed shows both `'456789123'` and `'920-24-2256'` formats. No mask, no validation. |
| `street` / `street_line_2` / `city` / `state` / `postal_code` / `country_code` | varchar(255) / TINYTEXT | NOT NULL default '' | All free-text. Seed has `state='California'` and `state='CA'` for different patients. |
| `phone_home` / `phone_biz` / `phone_contact` / `phone_cell` | varchar(255) | NOT NULL default '' | Free-text. Mixed formats: `(619) 555-2222`, `(619) 555-7823 x251`, `(5555) 555-1111`. |
| `email` / `email_direct` | varchar(255) | NOT NULL default '' | No format validation. |
| `sex` | varchar(255) COMMENT 'Sex at birth' | NOT NULL default '' | Free-text. Seed values: `'Male'`, `'Female'`, `'Unknown'`, `''`. **`sex_identified` (TEXT, sql:8465) is the OMOP/USCDI "current sex" field. Two columns, two semantics.** |
| `gender_identity` / `sexual_orientation` / `pronoun` | TEXT | nullable | Stored as `option_id` keyed to `list_options.gender_identity / sexual_orientation / pronoun` — but no FK; bad data possible. |
| `language` | varchar(255) | NOT NULL default '' | Should reference `list_options.option_id where list_id='language'`. **Seed inserts `'english'` (lowercase) but the option_id is `'English'` (capital E, sql:11580 area).** Lookup will fail. |
| `race` / `ethnicity` | varchar(255) | NOT NULL default '' | FK semantics to `list_options.race`/`ethnicity` but the seed bypasses this and inserts `ethnoracial='Latina'` instead, which is not a valid `option_id`. |
| `ethnoracial` | varchar(255) | NOT NULL default '' | Mystery legacy field. Seed populates this with values like `'Latina'`, `'Latino'`. Not a list-options key. |
| `interpreter` | varchar(255) | NOT NULL default '' | Originally a yes/no flag, now a free-text "additional notes" field per `COMMENT` at sql:8371. `interpreter_needed TEXT` (sql:8372) is the new version. **Two columns, semantically conflicting.** |
| `mothersname` | varchar(255) | NOT NULL default '' | |
| `guardiansname` / `guardianrelationship` / `guardiansex` / `guardianaddress` / `guardiancity` / ... | TEXT | nullable | 11 separate `guardian*` columns. |
| `contact_relationship` | varchar(255) | NOT NULL default '' | Seed shows the **name of the contact**, not the relationship — e.g., `'Joe'`, `'Marion Shaw'`, `'Mike Hart'`. Column is misnamed. |
| `deceased_date` | datetime | nullable | Seed shows `NULL` and `'2018-03-15'` and `'2021-07-08'`. **Critical: agent must respect this when summarizing "current" status.** |
| `name_history` | TINYTEXT | nullable | Free-text. Real history goes in `patient_history` (sql:8478). |
| `last_updated` | DATETIME | NOT NULL default CURRENT_TIMESTAMP ON UPDATE | But child tables (lists, prescriptions, vitals) each have their own `last_updated` / `modifydate` / `update_date` — they're not synced. |

### Findings

- **Dual sex columns** — `sql/database.sql:8360,8465`. `sex` ("at birth") and
  `sex_identified` ("Patient reported current sex") are both TEXT-equivalent;
  neither is enum. A trans patient can have `sex='Male'` and
  `sex_identified='Female'`, or either field can be blank. Agent must read
  both and disambiguate.
- **Duplicate name fields** — `sql/database.sql:8340-8342, 8449-8451, 8461`.
  `fname/mname/lname`, plus `birth_fname/birth_mname/birth_lname`, plus
  `preferred_name`. Older system has `name_history` TINYTEXT, newer has
  separate `patient_history` table.
- **`title` is sometimes a name prefix and sometimes empty** — `sql/database.sql:8337`.
  Maps to `list_options.titles` (Mr./Mrs./Ms./Dr.) but stored as the literal
  title string, not the option_id. Seed has `title='Mr.'` but `sex='Female'`
  for pid=22 (Ilias Jenane).
- **Empty-string-not-NULL convention** — Most `varchar(255) NOT NULL default ''`
  columns. `WHERE email IS NOT NULL` returns rows that have empty email.
  Always also check `<> ''`.
- **`pid` vs `uuid` vs `id`** — `sql/database.sql:8335,8336,8380`. `id` is
  internal autoincrement, `pid` is the long-running cross-table foreign key
  (every other table joins on `pid`, never `id`), `uuid` is the FHIR-friendly
  external identifier. `uuid` is `binary(16)` and can be NULL.
- **`pubpid` is the human-facing ID** — `sql/database.sql:8379`. Seed values:
  `'10'`, `'24555'`, `'789456'`, `'8'`, `'1001'`, `'17'`, `'18'`, `'22'`,
  `'30'`, `'25'`, `'26'`, `'40'`, `'34'`, `'35'`. No format. Free-text.
- **`care_team_provider/care_team_facility/care_team_status` are TEXT**
  — `sql/database.sql:8426-8428`. Pipe-separated lists of user_ids, not joinable
  via SQL FK. Provider and facility may be deleted while still referenced here.
- **`industry`, `imm_reg_status`, `tribal_affiliations`, `nationality_country`,
  `dupscore`** are all TEXT/varchar with no real validation.
- **`hipaa_*` consent fields** — `sql/database.sql:8385-8390`. `varchar(3)` storing
  `'YES'`/`'NO'`/empty. Six different consent flags. Agent should not assume
  empty means consent.
- **`soap_import_status TINYINT(4)`** — `sql/database.sql:8424`. Magic-number
  field documenting import provenance. 1 = Prescription Press, 2 = Prescription
  Import, 3 = Allergy Press, 4 = Allergy Import. Agent should treat any
  patient with non-null `soap_import_status` as having unverified third-party
  data.

## history_data

`sql/database.sql:2916-3011`. Single row per patient keyed by `pid` (no
`UNIQUE` on pid; multiple history rows per patient are technically possible).
Almost everything is unstructured.

### Largest free-text columns

All `longtext` (no length cap):
- `coffee`, `tobacco`, `alcohol`, `sleep_patterns`, `exercise_patterns`,
  `seatbelt_use`, `counseling`, `hazardous_activities`, `recreational_drugs`
  (sql:2919-2927) — social history
- `history_mother`, `history_father`, `history_siblings`, `history_offspring`,
  `history_spouse` (sql:2944-2953) — family history
- `relatives_cancer`, `relatives_tuberculosis`, `relatives_diabetes`,
  `relatives_high_blood_pressure`, `relatives_heart_problems`, `relatives_stroke`,
  `relatives_epilepsy`, `relatives_mental_illness`, `relatives_suicide`
  (sql:2954-2962) — 9 longtext fields keyed off relative type only

Companion `dc_*` columns (`dc_mother`, `dc_father`, etc., sql:2945-2953) — also
text. Likely "decade-of-condition" annotations.

### Findings

- **`last_*` exam dates are varchar(255), not date** — `sql/database.sql:2928-2943`.
  Sixteen "last_X_exam" fields (`last_breast_exam`, `last_mammogram`,
  `last_colonoscopy`, `last_ldl`, `last_psa`, etc.) are varchar(255). They can
  hold "2019", "March 2019", "unknown", or empty. Agent cannot date-compare them.
- **Family-history columns are unbounded text per relative** — `sql/database.sql:2944-2953`.
  No structure means a patient with `history_mother = "diabetes type 2,
  hypertension at 50, breast ca at 67, deceased at 71"` is stored as one
  string. Agent must extract conditions itself; cannot reliably index.
- **Surgery history mixes datetime columns and free-text exams** —
  `sql/database.sql:2963-2971`. Specific surgeries (`cataract_surgery`,
  `tonsillectomy`, `cholecystestomy`, `heart_surgery`, `hysterectomy`,
  `hernia_repair`, `hip_replacement`, `knee_replacement`, `appendectomy`)
  are datetime — but if a patient had a surgery NOT in this list, it goes
  in free-text `additional_history` or `lists` with `type='surgery'`.
  **Two surgeries can be in two places.**
- **20 `usertext*` and 5 `userdate*` columns** — `sql/database.sql:2980-3006`.
  Customer-customizable fields. Their meaning depends on the deployment's
  layout. Agent has no way to know what `usertext17` means without reading
  `layout_options` for that field.
- **`exams text`, `additional_history text`** — sql:2979,2978. Catch-all
  unstructured columns.

## lists (problems, allergies, meds, surgeries, dental, IPPF) and the type discriminator

`sql/database.sql:7671-7712`. **The single most concept-overloaded table the
agent will read.** All clinical "ongoing items" share this shape, separated
only by `type varchar(255) DEFAULT NULL` (sql:7675). No FK or enum.

### `type` discriminator values

From `issue_types` table seed (`sql/database.sql:3478-3490`) and active services:

| `type` value | Meaning | Service that filters on it |
|---|---|---|
| `medical_problem` | Problem list / condition | `ConditionService` (sql at `src/Services/ConditionService.php:80,224,272,312`) |
| `health_concern` | Non-medical / SDoH concern | `issue_types` only |
| `medication` | Active medication (community) | `PatientIssuesService`, `PrescriptionService` (UNIONs lists+prescriptions) |
| `allergy` | Allergy/intolerance | `AllergyIntoleranceService` (filters `type='allergy'`, `src/Services/AllergyIntoleranceService.php:101`) |
| `medical_device` | Implant / device | `DeviceService` |
| `surgery` | Past surgical procedure | (read by `lists` API directly) |
| `dental` | Dental issue | (read by `lists` API directly) |
| `ippf_gcac` | IPPF "Abortions" category | only when category=`ippf_specific` |
| `contraceptive` | IPPF contraception | only when category=`ippf_specific` |

### Findings

- **`lists.type` has NO foreign key** — `sql/database.sql:7675`. Validation lives
  in `PatientIssuesService::validateIssueType()` (`src/Services/PatientIssuesService.php:162-168`),
  which queries `issue_types` at runtime. Direct INSERTs (legacy code, custom
  modules, FHIR import) can put any value here — `'illness'`, `'problem'`,
  `'cond'` — and the agent must treat unknown types as garbage.
- **`lists.title varchar(255)` is the free-text label** — `sql/database.sql:7677`.
  Comes alongside `lists.diagnosis varchar(255)` (the coded version,
  sql:7687). They are *not* required to agree. A row can have
  `title='Diabetes'` and `diagnosis='ICD10:E11.9'`, or `title='Penicillin G'`
  and `diagnosis='RXCUI:733'` (per the test fixture allergy-intolerance.php:31).
- **`lists.diagnosis` is a delimited string of code references, not a row per
  code** — `sql/database.sql:7687`, parsed in `BaseService::addCoding()`
  (`src/Services/BaseService.php:557`: `explode(";", $diagnosis)`). Format is
  `SYSTEM:CODE;SYSTEM:CODE`. Both ICD10 and SNOMED-CT can be on one row.
  Neither is canonical — `code_types` (sql:10618-10635) flags both `ICD10`
  and `SNOMED` as `ct_diag=1` and `ct_problem=1`.
- **`lists.activity tinyint(4) default NULL`** — `sql/database.sql:7688`. The
  soft-delete flag. **NULLs and 0s both exist; only 1 = active.** No
  `deleted_at`. Status changes leave no audit trail at the row level (separate
  `lists_touch` table at sql:7744 only tracks last touch by pid+type).
- **`lists.outcome int(11) default 0`** — `sql/database.sql:7693`. Different
  semantic from `activity`. References `list_options.option_id` for outcome
  (resolved/unresolved/improved/etc.). A "resolved" condition has `outcome>0`
  but might still have `activity=1`. Agent must check both.
- **`lists.subtype varchar(31)`** — sql:7676. Used for allergy subtype
  ("medication", "food", "environmental"). Empty string for non-allergy rows.
- **`lists.verification VARCHAR(36)`** — sql:7700. Allergy/condition
  verification status — references `list_options.allergyintolerance-verification`
  (`unconfirmed`, `confirmed`, `refuted`, `entered-in-error`, sql:6871-6874).
  **An "unconfirmed" or "entered-in-error" allergy is still a row in `lists`.**
  Agent must filter on this when listing allergies.
- **`lists.modifydate timestamp ON UPDATE CURRENT_TIMESTAMP`** — sql:7704.
  Updates on every change. **`lists.date` (sql:7674) is when the issue was
  recorded, `begdate` (sql:7680) is when it started, `enddate` (sql:7681) is
  when it ended.** Three different "dates" per row, all DATETIME. Many rows
  have only `date` populated.
- **`lists.severity_al VARCHAR(50)`** — sql:7705. Free text severity for
  allergies, but stored only on this row, not in a coded field.
- **`lists.reaction varchar(255)`** — sql:7699. Free text reaction. Test
  fixture sets it to `'hives'`. Could also be a SNOMED code in production.
- **`lists.list_option_id VARCHAR(100)`** — sql:7707. Generic FK to
  `list_options` used for newer features. Doesn't displace the older
  `lists.diagnosis` string.
- **`lists.user varchar(255)`** — sql:7691. Stores the *username string* of
  the recording user, not the `users.id`. Joins to `users.username`. Old
  records can reference deleted/renamed users.

### lists_medication — a sidecar, not a replacement

`sql/database.sql:7716-7735`. Joined 1:1 to `lists` via `list_id`. Holds
medication-specific fields that `lists` doesn't:
- `drug_dosage_instructions` longtext — free-text dosing
- `usage_category` (community/inpatient/outpatient/discharge,
  list_options at sql:12307-12310)
- `request_intent` (proposal/plan/order/original-order, etc.,
  sql:12314-12321)
- `medication_adherence`, `medication_adherence_information_source`,
  `medication_adherence_date_asserted`
- `prescription_id` BIGINT(20) — optional FK to `prescriptions.id` linking
  the lists-style med to a real prescription (when both exist).
- `is_primary_record TINYINT(1) DEFAULT 1` — 1 = primary, 0 = "reported by"
  (e.g., self-reported med).
- `reporting_source_record_id` — fk to `users.id` for the person who reported.

### Findings

- **`lists_medication.id` is independent of `lists.id`** — sql:7718. The med row
  in `lists` and the side-car in `lists_medication` are two separate primary
  keys. UPDATE on the wrong one creates orphaned data.
- **`lists_medication.prescription_id` is the link back to `prescriptions.id`**
  — sql:7728. Used by `PrescriptionService` to deduplicate
  (`src/Services/PrescriptionService.php:260`:
  `WHERE type = 'medication' AND lists_medication.prescription_id IS NULL`).
  If `prescription_id` is NULL the same med may be reported twice in the
  UNION view.
- **Self-reported vs prescribed** — `is_primary_record=0` rows are typically
  patient-reported, never validated. Agent should label these distinctly.
- **`usage_category_title VARCHAR(255) NOT NULL`** — sql:7722. Stores the
  *title* (display string) alongside the option_id. Same field appears on
  `prescriptions.usage_category_title` (sql:8741). Two copies of the lookup
  result get saved per row, denormalized.

## forms + form_* (LBF vs static)

`sql/database.sql:2460-2478`. The `forms` table is a thin index for any data
captured during an encounter. Each row points to a specific `form_*` table via
`formdir longtext` (the dirname/registry key) and `form_id bigint(20)` (the
PK in that target table).

### `forms` columns

- `id` bigint — PK
- `encounter` bigint — joins to `form_encounter.encounter`
- `pid` bigint — patient
- `form_name` longtext — display name (e.g., "SOAP", "Vitals")
- `form_id` bigint — FK to the row in `form_<type>` table
- `formdir` longtext — slug used to find the table (e.g., `'vitals'` →
  `form_vitals`, `'clinical_notes'` → `form_clinical_notes`)
- `deleted tinyint(4) DEFAULT 0` — sql:2470. Soft delete.
- `provider_id bigint NOT NULL DEFAULT 0` — sql:2474.
- `issue_id bigint NOT NULL DEFAULT 0` — sql:2473. **References `lists.id`**
  to optionally link a form to a problem/case.
- `therapy_group_id INT(11)` — sql:2472.

### Top form_* tables (registry-active forms)

From `INSERT INTO registry` at `sql/database.sql:8820-8837`:

| formdir | form table | Static or LBF | Schema |
|---|---|---|---|
| `newpatient` | `form_encounter` | static | sql:2022 |
| `vitals` | `form_vitals` | static | sql:2418 |
| `soap` | `form_soap` | static | sql:2396 |
| `clinical_notes` | `form_clinical_notes` | static | sql:1972 |
| `ros` | `form_ros` | static | sql:2243 |
| `reviewofs` | `form_reviewofs` | static (huge wide table, ~200 columns) | sql:2117 |
| `dictation` | `form_dictation` | static | sql:2002 |
| `procedure_order` | `procedure_order` (no `form_` prefix) | static | sql:10369 |
| `observation` | `form_observation` | static | sql:12861 |
| `care_plan` | `form_care_plan` | static | sql:12806 |
| `functional_cognitive_status` | `form_functional_cognitive_status` | static | sql:12839 |
| `clinical_instructions` | `form_clinical_instructions` | static | sql:12909 |
| `eye_mag` | many `form_eye_*` (17 tables) | static | sql:12982+ |
| `questionnaire_assessments` | `questionnaire_response` | LBF-ish | sql:14340 |

### LBF (Layout-Based Forms)

- **`lbf_data`** (`sql/database.sql:10236-10241`):
  `(form_id, field_id, field_value LONGTEXT)`. Generic key-value store for
  user-defined LBFs. The schema for what fields exist is in `layout_options`
  (sql:3655) keyed by `form_id` (e.g., `'DEM'` for the demographic layout,
  `'LBFxxx'` for custom forms).
- **`lbt_data`** (sql:10250-10255): same shape, but for transactions.

### Findings

- **`forms` is the mandatory join hop** — sql:2460. To find any encounter's
  notes, the agent does `SELECT * FROM forms WHERE pid=? AND encounter=? AND
  deleted=0`, then `formdir` tells you which `form_*` table to query for the
  body. Skipping the `forms` row means missing soft-deleted forms.
- **`form_clinical_notes.code` + `codetext` + `description`** — `sql/database.sql:1983-1985`.
  The "code" is a SNOMED-CT/LOINC string (e.g., `'SNOMED-CT:168731009'`),
  `codetext` is the lookup display, `description` is free-text. Three sources
  of truth per note.
- **`form_clinical_notes.clinical_notes_type` and `clinical_notes_category`**
  — sql:1987,1988. Both are varchar(100). They typify a note ("Reason for
  Visit", "Chief Complaint", "Discharge Summary", etc.). No FK.
- **`form_soap.subjective/objective/assessment/plan` are TEXT, free-text** —
  sql:2404-2407. The traditional SOAP note. **No coded fields.** Agent
  treats this as pure narrative.
- **`form_vitals` uses DECIMAL(12,6) for everything** — sql:2429-2434. Weight,
  height, temperature, pulse, respiration. **Default `'0.00'` not NULL.** A
  row with `weight=0` likely means "not recorded" — agent must not quote 0
  weight as a clinical fact. Pediatric subset (`ped_*`) uses DECIMAL(6,2).
- **`form_vitals.bps/bpd varchar(40)`** — sql:2427,2428. Blood pressure stored as
  *strings*, presumably to allow "120/80" or "<60" entries, but the agent
  cannot reliably arithmetic them.
- **`form_dictation.dictation longtext`** — sql:2010. Speech-to-text dictation
  output. Likely full of transcription errors when present.
- **`form_reviewofs` has ~200 columns each `varchar(5)`** — sql:2117 onward.
  Each column is a yes/no/unknown answer. `'Y'`/`'N'`/`''`. The agent cannot
  enumerate column names dynamically without metadata; column meaning is in
  the column name itself.
- **`form_care_plan.code/codetext/description`** — sql:12815-12817. Same
  pattern as `form_clinical_notes`.
- **`form_observation.ob_value` and `ob_value_code_description`** — sql:12874,12892.
  An observation's value can be a number (`'120'`), a coded value
  (`'SNOMED-CT:449868002'`), or a string. Agent must inspect `result_data_type`
  and `code_type` to interpret. Has `parent_observation_id` for nested
  observations (sql:12889).
- **38 `form_*` tables in schema, only ~17 in active registry.** Schema lives
  on for migration compatibility; the agent should not assume every
  `form_*` table has data (or even rows beyond seed).

## form_encounter

`sql/database.sql:2022-2062`. The encounter envelope. One row per visit. Joined
from `forms.encounter` to `form_encounter.encounter`.

### Findings

- **`form_encounter.encounter` is a separate column from `id`** — sql:2030.
  Other tables join on `encounter` (the business key), not `id` (the PK).
  This is a recurring OpenEMR pattern.
- **`form_encounter.reason longtext`** — sql:2026. Free-text reason. Test
  fixture: `'test-fixture-Complains of nausea, loose stools and weakness.'`
- **`form_encounter.facility longtext`** — sql:2027. **The facility *name*
  stored as longtext** — alongside `facility_id int(11)` (sql:2028). The
  ID may be 0 with a name-only string in `facility`, or vice versa.
- **`form_encounter.date datetime`** + **`date_end DATETIME`** + **`onset_date
  datetime`** + **`last_update timestamp`** — sql:2025,2031,2054,2056. Four
  date columns, none of them required. Agent should sort encounters by `date`
  but reach for `date_end` for visit duration if available.
- **`provider_id`, `supervisor_id`, `referring_provider_id`,
  `ordering_provider_id`** — sql:2039,2040,2053,2057. All `INT(11) DEFAULT 0`.
  Zero means "no provider assigned"; not a valid FK.
- **`encounter_type_code varchar(31)` + `encounter_type_description text`** —
  sql:2051,2052. Per the comment "not all types are categories" — i.e., the
  `pc_catid` (sql:2034) and the `encounter_type_code` are not the same
  hierarchy.
- **`pc_catid` references `openemr_postcalendar_categories`** — sql:2034.
  Default 5. The category drives reason codes for billing.
- **`class_code VARCHAR(10) NOT NULL DEFAULT 'AMB'`** — sql:2047. FHIR
  `Encounter.class` value (AMB=ambulatory, IMP=inpatient, EMER=emergency).
- **`parent_encounter_id`** — sql:2046. Self-referential. Used for grouping
  encounters (e.g., follow-up to an initial visit).
- **No `deleted` flag on form_encounter directly** — but `forms.deleted=1`
  on the corresponding `forms` row is the soft-delete signal for the
  encounter envelope.

## prescriptions and lists_medication

`prescriptions` schema: `sql/database.sql:8698-8751`. Side-car
`lists_medication` discussed above.

### Findings

- **Two parallel medication stores, UNIONed by `PrescriptionService`** —
  `src/Services/PrescriptionService.php:91-260`. The same med may appear in
  both. The service tries to dedupe with
  `WHERE type = 'medication' AND lists_medication.prescription_id IS NULL`
  (line 260), but legacy data without that link will produce duplicates.
- **`prescriptions.drug varchar(150)` is free-text** — sql:8709.
  `prescriptions.drug_id int(11) DEFAULT 0` (sql:8710) is the FK to `drugs`,
  but **per the comment in PrescriptionService.php:192-193**:
  "drug_id in my databases appears to always be 0 so I'm not sure I can grab
  anything here.. I know WENO doesn't populate this value..."
  In practice the agent must work from the free-text `drug` column.
- **`prescriptions.rxnorm_drugcode varchar(25)`** — sql:8711. RxNorm code,
  optional. Often empty. The fallback inside the SQL is to use `drugs.drug_code`
  if available (sql:8710 join + line 160-163 of PrescriptionService).
- **`prescriptions.dosage varchar(100)` + `quantity varchar(31)` +
  `unit int(11)` + `route varchar(100)` + `interval int(11)` + `form int(3)`**
  — sql:8713-8718. Mixed: free-text strings AND list_options FKs. `unit` and
  `interval` map to `list_options` (where list_id='drug_units',
  'drug_interval'); `route` is varchar(100) holding the option_id; `form` is
  the dosage form ID.
- **`prescriptions.drug_dosage_instructions longtext`** — sql:8744. Free-text
  sigs (e.g., "Take 1 tablet by mouth twice daily with food"). Often the only
  populated dosing field.
- **`prescriptions.refills int + per_refill int + filled_date date`** —
  sql:8720-8722. Refill tracking. Active=1 with refills=0 and filled_date set
  is "completed".
- **`prescriptions.active int(11) DEFAULT 1`** — sql:8725. Soft delete. **Not
  TINYINT(1)** — can hold values >1 if a custom module misuses it.
- **`prescriptions.end_date date`** — sql:8734. Combined with `active=1`
  drives the FHIR status: `PrescriptionService.php:181-184`:
  `WHEN end_date IS NOT NULL AND active = '1' THEN 'completed'; WHEN active = '1'
  THEN 'active'; ELSE 'stopped'`. **A "stopped" prescription can still have
  `active=1` if `end_date` is set; agent must compute status, not read the
  flag.**
- **`prescriptions.indication text` + `diagnosis text`** — sql:8735,8745.
  Two independent free-text fields explaining "why prescribed".
- **`prescriptions.prn varchar(30)`** — sql:8736. PRN ("as needed") frequency.
  Free-text.
- **`prescriptions.txDate DATE NOT NULL`** — sql:8739. Required, but no DEFAULT
  — INSERT must supply or fall back to `'0000-00-00'` per MySQL strict mode.
- **`prescriptions.erx_source TINYINT(4)` + `erx_uploaded TINYINT(4)`** —
  sql:8730,8731. NewCrop e-prescribing integration flags. Values 0/1.
- **`drugs.drug_id int auto_increment` + `drugs.name varchar(255)`** —
  sql:1599-1626. The internal drug catalog. `ndc_number`, `drug_code`,
  `related_code` are all optional.

## procedure_result + procedure_order

`procedure_order`: `sql/database.sql:10369-10414`.
`procedure_order_code`: `sql/database.sql:10423-10441`.
`procedure_report`: `sql/database.sql:10467-10484`.
`procedure_result`: `sql/database.sql:10493-10513`.

The lab/order data path is: `procedure_order` → `procedure_order_code` (one-to-many,
the actual tests on the order) → `procedure_report` (the lab's response
container) → `procedure_result` (one row per data point in the report).

### Findings

- **`procedure_result.result_code varchar(31)`** — sql:10498. **Comment:
  "LOINC code, might match a procedure_type.procedure_code"** — *might match*.
  Not guaranteed. Agent cannot assume LOINC is canonical.
- **`procedure_result.result varchar(255)`** — sql:10503. The actual value, as a
  string. Could be "5.4", "POSITIVE", "<5", "see comment", "Not applicable".
- **`procedure_result.result_data_type char(1)`** — sql:10497. Single-char
  type discriminator: **`N=Numeric, S=String, F=Formatted, E=External,
  L=Long text as first line of comments`**. Agent must check this before
  arithmetic on `result`.
- **`procedure_result.units varchar(31) NOT NULL DEFAULT ''`** — sql:10502.
  Free-text. Same lab can use "mg/dL", "mg/dl", "mg / dL". No normalization.
- **`procedure_result.range varchar(255) NOT NULL DEFAULT ''`** — sql:10504.
  Reference range as a free-text string ("70-100", "<200", "negative").
  Agent cannot mechanically determine if a result is in range.
- **`procedure_result.abnormal varchar(31)`** — sql:10505. **Comment: "no,yes,
  high,low"** — but it's a varchar, not enum. Could hold HL7 abnormal flags
  ("H", "L", "HH", "LL", "AA").
- **`procedure_result.result_status varchar(31)`** — sql:10508. **Comment:
  "preliminary, cannot be done, final, corrected, incomplete...etc."**
  A "preliminary" result is in the same table as a "final"; the agent must
  filter or label clearly.
- **`procedure_order_code.diagnoses text`** — sql:10429. **Comment: "diagnoses
  and maybe other coding (e.g. ICD9:111.11)"** — i.e., legacy multi-coded
  semicolon-separated string, same pattern as `lists.diagnosis`.
- **`procedure_order.activity tinyint(1) DEFAULT 1`** — sql:10380. **Comment:
  "0 if deleted"** — soft delete on order.
- **`procedure_report.review_status varchar(31) DEFAULT 'received'`** —
  sql:10479. Tracks reviewer workflow ("received", "reviewed"). Agent should
  surface unreviewed results, but they exist in the same table.
- **Date timezone awareness, partial** — `procedure_report.date_collected_tz
  varchar(5)` and `date_report_tz varchar(5)` (sql:10473,10475). UTC offsets
  like `+0500`. **Most other date columns in OpenEMR are naive datetime with
  no TZ info** — site-local time, no UTC conversion.
- **`procedure_result.document_id`** — sql:10507. Some results are stored as
  attached documents (PDFs etc.) instead of structured rows. `document_id=0`
  means inline result.

## immunizations

`sql/database.sql:3235-3270`.

### Findings

- **`immunizations.cvx_code varchar(64)`** — sql:3241. CVX vaccine code.
  Optional. The `cvx_codes.sql` separately seeds reference data
  (`sql/cvx_codes.sql`).
- **`immunizations.administered_date datetime`** — sql:3239. Naive datetime.
  Nullable.
- **`immunizations.administered_by varchar(255)` + `administered_by_id bigint`**
  — sql:3244,3245. Per the comment "Alternative to administered_by_id" — the
  free-text version is used when the administering provider isn't in the system.
  **Two columns; agent must coalesce.**
- **`immunizations.added_erroneously tinyint(1) NOT NULL DEFAULT 0`** —
  sql:3258. Distinct soft-delete flag from `activity`. Agent must filter on
  this when listing immunizations.
- **`immunizations.refusal_reason VARCHAR(31)`** — sql:3262. **An immunization
  row can represent a refusal, not an administration.** Agent must check
  `completion_status` and `refusal_reason` together.
- **`immunizations.completion_status VARCHAR(50)`** — sql:3260. "Completed",
  "Refused", "Not Administered", etc.
- **`immunizations.amount_administered float` + `amount_administered_unit
  varchar(50)`** — sql:3253,3254. Dose in mL/mg etc. Often null.
- **`immunizations.lot_number varchar(50)`** — sql:3243. Free text.
- **`immunizations.encounter_id BIGINT(20)`** — sql:3266. **Comment: "fk to
  form_encounter.encounter to link immunization to encounter record"** — but
  `0` means unlinked. Many immunizations come from registries with no encounter.
- **`immunizations.update_date timestamp NOT NULL`** — sql:3250. **Without
  DEFAULT or ON UPDATE in the schema**, MySQL fills in `'0000-00-00 00:00:00'`
  on insert if not specified, depending on `sql_mode`.

## issues vs problems vs lists — which is canonical?

There is no single canonical store. There are three patterns of "the patient
has condition X":

1. **`lists` row with `type='medical_problem'`** — primary store. Joined to
   encounters via `issue_encounter`. Used by `ConditionService` for FHIR.
2. **`issue_encounter` row** — `sql/database.sql:3437-3451`. Links a `lists.id`
   to a `form_encounter.encounter`. Tracks "this problem was active during
   this visit". `(pid, list_id, encounter)` is unique, plus `resolved` flag.
3. **`form_clinical_notes`, `form_care_plan`, `form_observation`, etc.,
   each with their own `code`/`codetext`** — encounter-level reference to
   a problem code. May not have a corresponding `lists` row at all.

### ICD10 vs SNOMED — both?

From `code_types` seeds (`sql/database.sql:10618-10635`):

- `ICD10` — `ct_diag=1, ct_problem=1, ct_active=1` — diagnosis & problem code,
  active.
- `ICD9` — `ct_diag=1, ct_problem=1, ct_active=0` — historical, disabled.
- `SNOMED` — `ct_diag=1, ct_problem=1, ct_active=0` — disabled by default.
- `SNOMED-CT` — `ct_term=1` — "Clinical Term", *not* `ct_problem`.

In practice, `lists.diagnosis` may have `ICD10:E11.9;SNOMED-CT:73211009` for
the same condition (test fixture `care-plan.php:41` uses `'SNOMED-CT:168731009'`).
**Neither is canonically preferred at the schema level; the choice depends on
which UI created the record.** FHIR resources tend to prefer SNOMED-CT;
billing uses ICD10. The agent should treat both as valid and not pretend to
have de-duplicated when only one system is present.

### Findings

- **`issue_types` is the source-of-truth registry of issue categories** —
  `sql/database.sql:3460-3472`, with seeded values at sql:3478-3490 (default
  category) and sql:3485-3490 (ippf_specific category).
- **`issue_encounter.resolved tinyint(1)`** — sql:3443. Independent of
  `lists.activity`. A problem can be `resolved=1` for one visit but
  `activity=1` (still on problem list).
- **`forms.issue_id`** — sql:2473. Some forms link to a `lists.id` (case);
  others have `issue_id=0` (unlinked).
- **No FK enforcement** — none of `issue_encounter.list_id`, `forms.issue_id`,
  or `lists_medication.list_id` have actual FK constraints. Orphan rows are
  possible.

## Clinical narrative (pnotes, encounter notes, free-text fields)

The agent's biggest free-text dumping grounds and primary hallucination risk
surface. Listed by likely volume:

- **`pnotes.body longtext`** — `sql/database.sql:8673`. Patient notes
  (nurse/physician messages, follow-up instructions). Joined by `pid`. Has
  `deleted tinyint(4)` (sql:8681) and `activity tinyint(4)` (sql:8677) — soft
  delete is duplicated. `is_msg_encrypted tinyint(2) DEFAULT 0` (sql:8684):
  body may be encrypted blob if 1.
- **`form_soap.subjective/objective/assessment/plan text`** — sql:2404-2407.
  Per encounter SOAP narrative.
- **`form_dictation.dictation longtext` + `additional_notes longtext`** —
  sql:2010,2011. Speech-to-text encounter dictation.
- **`form_clinical_notes.description text` + `codetext text`** — sql:1985,1984.
  Description is free-text; codetext is sometimes the lookup display string.
- **`form_clinical_notes.note_related_to text`** — sql:1989. Free-text linking
  the note to other concepts.
- **`form_encounter.reason longtext`** — sql:2026. Chief complaint / reason for
  visit, free-text.
- **`form_observation.ob_value varchar(255)` + `ob_reason_text text`** —
  sql:12874,12885. Free-text observations.
- **`lists.comments longtext`** — sql:7689. Comments per problem/allergy/med.
- **`history_data.*` longtext columns** — described above. Family/social/exam
  narrative.
- **`form_care_plan.codetext text` + `description text` + `reason_description
  text`** — sql:12816,12817,12823.
- **`form_clinical_instructions.instruction text`** — sql:12914. Instructions
  given to the patient.
- **`onotes.body longtext`** — sql:7955. Office notes (admin-side, non-clinical
  often).

### Findings

- **`pnotes.assigned_to varchar(255)`** — sql:8680. The intended recipient
  username. Often free-text email-style addressing.
- **`pnotes.message_status VARCHAR(20) NOT NULL DEFAULT 'New'`** — sql:8682.
  `'New'`, `'Read'`, `'Replied'`. Filterable.
- **No row-level granular permissions on narrative columns** — agent reading
  `pnotes` must respect ACL elsewhere; the body is just text.
- **Encrypted bodies** — `pnotes.is_msg_encrypted=1` rows (sql:8684) cannot
  be read without the per-site key. Agent must filter these out *or*
  request decryption.

## Demo data inventory (which patients are rich, which are sparse)

`sql/example_patient_data.sql` inserts exactly 14 rows into `patient_data`
**and nothing else.** No encounters, no problems, no medications, no allergies,
no immunizations, no vitals, no labs, no notes, no history. All seeded patients
are demographic-only.

`sql/example_patient_users.sql` inserts 2 provider users (`davis`, `hamming`)
with weak SHA-1 passwords for compatibility with patient-data references.

### The 14 demo patients

| pid | pubpid | name | DOB | sex | language | notable |
|---|---|---|---|---|---|---|
| 5 | 10 | Farrah Rolle (Ms.) | 1973-10-11 | Female | english | Latina ethnoracial; **most complete demographics** |
| 1 | 24555 | Ted Shaw (Mr.) | 1947-03-11 | Male | english | Married, occupation populated |
| 4 | 789456 | Eduardo Perez (Mrs.) | 1957-01-09 | Male | english | **title=Mrs. but sex=Male** — known data inconsistency |
| 8 | 8 | Nora Cohen (Mrs.) | 1967-06-04 | Female | spanish | Latina; ethnoracial set; **language=spanish (not in lookup)** |
| 41 | 1001 | Brent Perez (Mr.) | 1960-01-01 | Male | english | Latino |
| 17 | 17 | Jim Moses (Mr.) | 1945-02-14 | Male | (empty) | Sparse demographics |
| 18 | 18 | Richard Jones (Mr.) | 1940-12-16 | (empty) | (empty) | **No sex, no language** |
| 22 | 22 | Ilias Jenane (Mr.) | 1933-03-22 | **Female** | english | **title=Mr. but sex=Female** |
| 30 | 30 | Jason Binder (Mr.) | 1961-12-11 | Male | english | |
| 25 | 25 | John Dockerty (Mr.) | 1977-05-02 | (empty) | english | **No sex** |
| 26 | 26 | James Janssen (Mr.) | 1966-04-28 | Male | english | |
| 40 | 40 | Wallace Buckley (Mr.) | 1952-04-03 | (empty) | english | **state='California' (not 'CA')**; no sex |
| 34 | 34 | Robert Dickey (Mr.) | 1955-04-12 | (empty) | english | No sex |
| 35 | 35 | Jillian Mahoney (Mrs.) | 1968-08-11 | Female | english | |

### Findings

- **NO patient in the seed has any clinical data.** Every clinical query the
  agent runs against a demo install will return zero rows. Agent demos
  require either:
  (a) running the test fixture loader (`FixtureManager` in
  `tests/Tests/Fixtures/`, but those fixtures use `pubpid='test-fixture-*'`
  and a *new* set of patients that overlap the seed names), or
  (b) generating synthetic clinical data, or
  (c) using a real EMR snapshot.
- **Test fixtures (when loaded) add allergy data for 2 patients only** —
  `tests/Tests/Fixtures/allergy-intolerance.php`: pid mapping to
  `pubpid='test-fixture-789456'` (Eduardo Perez) gets `'Ampicillin'`
  (RXCUI:7980, hives, unconfirmed); `pubpid='test-fixture-8'` (Nora Cohen)
  gets `'Penicillin G'` (RXCUI:733, hives, confirmed).
- **Test fixtures add care plan for 1 patient** — `tests/Tests/Fixtures/care-plan.php:41`:
  Eduardo Perez (test-fixture-789456) gets a care plan with
  `code='SNOMED-CT:168731009'` ("Standard chest x-ray").
- **Test fixtures add encounter for 1 patient** — `tests/Tests/Fixtures/encounters.php`:
  Eduardo Perez gets an `'AMB'` (ambulatory) encounter with reason
  `'test-fixture-Complains of nausea, loose stools and weakness.'`.
- **No medication or vitals fixtures exist** in the test suite. Even after
  loading test fixtures, no demo patient has structured meds, vitals, labs,
  or immunizations.

### Best demo candidates (after loading test fixtures)

- **Eduardo Perez (pid=4, fixture pubpid='test-fixture-789456')** — the
  "richest" available: has demographics + 1 allergy + 1 care plan + 1
  encounter. Use for end-to-end demos that exercise multiple tables.
- **Nora Cohen (pid=8, fixture pubpid='test-fixture-8')** — has 1 allergy
  (Penicillin G, confirmed). Useful for an "allergy alert" demo.

### Failure-mode candidates (sparse / dirty patients)

- **Richard Jones (pid=18)** — empty `sex`, empty `language`, empty
  `ethnicity`. Tests how the agent handles missing demographics. **Plus the
  test fixture has him with `language='English'` but the seed has empty.**
- **Ilias Jenane (pid=22)** — `title='Mr.' AND sex='Female'`. Tests how the
  agent reconciles inconsistent demographic signals.
- **Wallace Buckley (pid=40)** — `state='California'` (not 'CA' option_id),
  empty sex, **`deceased_date='2021-07-08'`** in fixtures. Tests how the
  agent treats deceased patients in current-status queries.
- **Jim Moses (pid=17)** — no street address, no postal_code in seed; in
  fixtures has `deceased_date='2018-03-15'`. Same deceased risk.
- **Eduardo Perez (pid=4)** — `title='Mrs.' AND sex='Male'`. Same kind of
  inconsistency as Ilias.

## Cross-cutting issues

### Soft-delete inconsistency

Eight different patterns for "is this row still real":

| Table | Column | Convention |
|---|---|---|
| `lists` | `activity tinyint(4) default NULL` | sql:7688 — 1=active, 0/NULL=inactive. Nullable! |
| `forms` | `deleted tinyint(4) DEFAULT 0 NOT NULL` | sql:2470 — 1=deleted. |
| `prescriptions` | `active int(11) NOT NULL DEFAULT 1` | sql:8725 — int, not bool. |
| `pnotes` | `deleted tinyint(4) default 0` + `activity tinyint(4)` | sql:8677,8681 — both fields. |
| `immunizations` | `added_erroneously tinyint(1) DEFAULT 0` | sql:3258 — 1=mistake. |
| `procedure_order` | `activity tinyint(1) DEFAULT 1` | sql:10380 — `0 if deleted`. |
| `users` | `active tinyint(1) NOT NULL default 1` | sql:9804 — 1=active. |
| `procedure_specimen` | `deleted TINYINT(1) DEFAULT 0` | sql:10547. |
| `form_*` | `activity tinyint(4) default 0` (mostly default 0!) | e.g. form_vitals sql:2426. |

**No `deleted_at TIMESTAMP` anywhere.** The agent cannot say "this was
deleted on Tuesday"; it can only see current state.

**Critical:** `form_vitals.activity` defaults to `0` (sql:2426) but `forms.deleted`
defaults to `0`. So a `form_vitals` row with `activity=0` may *or may not* be
the deleted version — it depends on whether the convention was ever applied.
Filtering on `activity=1` excludes legitimately-recorded vitals where
`activity` was never set.

### Duplicate-of-self

- **`lists` has no uniqueness constraint on (pid, type, title) or (pid, type, diagnosis)**
  — sql:7708-7711 (PK on `id` only, KEYs on `pid` and `type`). The same patient
  can have multiple `medical_problem` rows for "Diabetes" with different
  capitalizations, different diagnosis codes, different `begdate`s. Two
  "active" rows can coexist.
- **`prescriptions` has no uniqueness constraint on drug or rxnorm code per
  patient** — sql:8748-8750. Same drug, multiple active rows.
- **`form_vitals` per encounter** — multiple vitals rows allowed per encounter
  (e.g., "before meds" / "after meds"). No uniqueness on
  (encounter, date, vital_type).
- **No "is_active" flag** in many of these tables (apart from the soft-delete
  fields), so the agent cannot mechanically pick "the current value".

### Date column inconsistency

| Pattern | Examples |
|---|---|
| `date datetime` (naive, local TZ) | `lists.date`, `form_encounter.date`, `pnotes.date`, `form_vitals.date` |
| `begdate datetime` + `enddate datetime` | `lists.begdate`/`enddate` |
| `start_date date` + `end_date date` | `prescriptions.start_date`, `prescriptions.end_date` (DATE only) |
| `date_added DATETIME` + `date_modified DATETIME` | `prescriptions.date_added/date_modified` |
| `last_updated DATETIME ON UPDATE CURRENT_TIMESTAMP` | `patient_data.last_updated`, `form_vitals.last_updated`, etc. |
| `modifydate timestamp ON UPDATE` | `lists.modifydate` |
| `update_date timestamp NOT NULL` (no DEFAULT) | `immunizations.update_date` — broken on strict SQL mode |
| `varchar(25)` "date" | `erx_rx_log.date` (sql:1795) |
| `*_tz varchar(5)` | only `procedure_report.date_collected_tz`, `date_report_tz` |
| `last_*_exam varchar(255)` | `history_data.last_breast_exam` etc. |

**Almost no UTC awareness.** Dates are naive `DATETIME`/`TIMESTAMP`. The agent
must treat all dates as local-clinic time and avoid timezone arithmetic.

### Free-text mirror of coded fields

Pervasive denormalization where both the code and a stringified label are
stored on the same row:

- `lists.title` (free) next to `lists.diagnosis` (coded string)
- `form_clinical_notes.code` + `codetext` + `description` (3 columns)
- `prescriptions.drug` (free) + `drug_id` (FK, often 0) + `rxnorm_drugcode`
  (code, often empty)
- `lists_medication.usage_category` (option_id) + `usage_category_title`
  (display string)
- `prescriptions.usage_category` + `usage_category_title`
- `procedure_result.result_code` (LOINC) + `result_text` (description)
- `immunizations.cvx_code` (CVX) + `immunization_id` (drugs FK) +
  `manufacturer` (free text)

**The free-text and coded versions can disagree.** Agent should prefer the
coded version where present, but always include the free-text in any
verbatim quote because that's what the clinician actually wrote.

### Encoding / charset

- **No table-level CHARSET= clauses in `database.sql`.** The schema relies on
  the database/server default. Many production OpenEMR installs run on
  `utf8` (MySQL's broken pre-utf8mb4 alias) which silently corrupts emoji,
  4-byte CJK characters, and some accented Latin chars.
- The `INSTALL_README` and `setup.php` (not audited here) specify the charset
  at install time; deviations across deployments are common.
- Agent should not assume that what looks like garbled text is data corruption
  vs. encoding mismatch on read.

### Stale data signal — `last_updated` vs row-level timestamp

- `patient_data.last_updated` (sql:8463) updates on any change. But child
  tables (`lists`, `prescriptions`, `pnotes`, `form_*`) have their own
  `last_updated` / `modifydate` / `update_date` columns. **Mutating a child
  row does NOT update `patient_data.last_updated`.**
- The agent cannot use `patient_data.last_updated` to decide "has this
  patient's record changed in the last 7 days" — must aggregate across all
  child tables.
- `lists_touch` (sql:7744) is a per-(pid, type) "last touched" tracker, used
  internally for cache invalidation. It does *not* fire on every list update.

### UUID gaps

`UuidRegistry::createMissingUuidsForTables` (used in
`PatientIssuesService.php:36`) is a backfill that runs lazily. New rows get
`uuid=NULL` until the registry job runs. The agent should not assume
`uuid IS NOT NULL` for arbitrary rows.

### `users` reference inconsistency

Many tables join to users by **`username` string** (`lists.user`,
`form_*.user`, `pnotes.user`), not `users.id`. Renames break joins; deleted
users orphan records. Some newer tables use `users.id` (e.g.,
`patient_data.created_by`, `created_by` columns added in upgrades).
**Mixed FK conventions in the same query**, e.g.,
`PrescriptionService.php:246`: `LEFT JOIN users ON users.username = lists.user`.

## What this means for the agent

Five non-negotiable ground rules for the Clinical Co-Pilot:

1. **Always cite the row, the column, and the verbatim text.** Because the
   schema stores the same concept in multiple shapes (free-text title +
   coded diagnosis; `prescriptions` row + `lists` row + `lists_medication`
   row), the agent must say "per `lists.title='Penicillin G'` (id=42, type=
   allergy, verification=confirmed)" — not "the patient is allergic to
   penicillin." Quote, don't summarize.

2. **Never trust a single soft-delete signal.** When listing "active" items
   the agent must combine *all* of: `activity=1`, `deleted=0`, `active=1`
   (where applicable), `added_erroneously=0`, `verification != 'entered-in-error'`,
   `enddate IS NULL OR enddate > NOW()`, `outcome=0` for problems. A row that
   looks active in one column may be dead in another.

3. **For medications, query both `prescriptions` and `lists WHERE
   type='medication'` and dedupe by `lists_medication.prescription_id`.**
   Replicate the `PrescriptionService::getBaseSql()` UNION pattern — never
   read just one source. Self-reported meds (`is_primary_record=0`) must
   be labeled as such.

4. **Treat coded fields as optional and never invent codes.** `lists.diagnosis`
   is free-text-format ICD10/SNOMED multi-coding parsed by string-split. If
   the column is empty, do not infer a code from `lists.title`; report the
   title as "uncoded patient/clinician text".

5. **Surface inconsistency, do not paper over it.** When `sex='Male'` and
   `sex_identified='Female'` differ, when `title='Mrs.'` and `sex='Male'`,
   when `language='english'` and the option list expects `'English'`, when
   the same problem appears twice in `lists`, the agent must show both
   values rather than picking one. The clinician needs to know the record
   is dirty so they can correct it.

## Out of scope / not investigated

- The 30+ `*_upgrade.sql` files in `sql/` — they show schema evolution and
  could reveal columns added/removed across versions. Not surveyed because
  the task targets the *current* schema agent will read.
- `sql/ippf_layout.sql` (380KB) — IPPF (International Planned Parenthood
  Federation) custom layout-based form schema. Specialized to that
  deployment.
- `sql/cvx_codes.sql` — CVX vaccine reference codes. Lookup table only.
- `sql/openemr-ea-mixed-complete.sql` — translation/locale data.
- `sql/ins_lang_def_nl.sql` (162KB) — language definitions, not patient data.
- ACL/permission tables (`gacl_*`, `acl_*`) — agent ground-truth depends on
  *what data exists*, not *what the user can see*. ACL filtering is a
  separate concern.
- `documents`, `documents_legal_*` — file metadata; the actual files live on
  the filesystem, encrypted in some configurations. Not raw data the agent
  pulls.
- Insurance tables (`insurance_data`, `insurance_companies`,
  `eligibility_verification`) — billing concern, only marginally clinical.
- `form_eye_*` (17 tables) — ophthalmology-specific. Most clinics won't have
  this data.
- Test data flow at runtime — `FixtureManager.php` mechanics are documented
  here only insofar as they tell us what demo patients can be enriched.
