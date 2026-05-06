# Clinical guideline corpus

Fixture corpus for the hybrid retriever (issues 007 + 008). The agent's
evidence-retriever worker queries this collection so it can ground
recommendations in published guidelines. Each PDF in this directory is
chunked, embedded with Cohere `embed-english-v3.0`, and inserted into
the `guideline_chunks` pgvector table by
`python -m copilot.retrieval.indexer --corpus-dir ./data/guidelines`.

The chunk_id is a content hash, so re-running the indexer is idempotent
and adding a new PDF only adds the new chunks.

## Sources

The PDFs in this directory are generated from public-domain excerpts of
widely-cited clinical guidelines so the retriever has real-shaped
content to test against. These are *abridged* and intended for
development / evaluation use only — do not cite them clinically; consult
the upstream source for full text and current updates.

| File | Source guideline | Upstream URL |
|---|---|---|
| `jnc8-hypertension-2014.pdf` | JNC 8 — Eighth Joint National Committee report on hypertension management in adults | https://jamanetwork.com/journals/jama/fullarticle/1791497 |
| `ada-diabetes-glycemic-2024.pdf` | ADA Standards of Care, glycemic targets section | https://diabetesjournals.org/care/issue/47/Supplement_1 |
| `kdigo-ckd-2024.pdf` | KDIGO 2024 Clinical Practice Guideline for the Evaluation and Management of CKD | https://kdigo.org/guidelines/ckd-evaluation-and-management/ |

## Adding a new guideline

1. Drop the PDF into `data/guidelines/`. The filename stem becomes the
   `guideline` field on every chunk (e.g., `jnc8-hypertension-2014.pdf`
   indexes as `guideline = "jnc8-hypertension-2014"`).
2. Re-run the indexer. Existing chunks are skipped on chunk_id match.
3. Adding a new file does not require a DB migration.

## Regenerating the fixture PDFs

The bundled fixture PDFs are produced by `scripts/build_guideline_fixtures.py`
from inline plain-text excerpts. Run that script after editing the
excerpts to regenerate the PDFs deterministically.
