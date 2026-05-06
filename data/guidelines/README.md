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
4. No agent restart needed — the retriever reads the table on every
   request, so newly indexed chunks are reachable from the next turn.

### Indexer one-liner

The indexer needs `COHERE_API_KEY` for embeddings and `CHECKPOINTER_DSN`
for the Postgres write. Pull them from Railway and point at the corpus:

```bash
COHERE_API_KEY=$(railway variables --service copilot-agent --kv | grep ^COHERE_API_KEY= | cut -d= -f2-) \
CHECKPOINTER_DSN=$(railway variables --service Postgres --kv | grep ^DATABASE_PUBLIC_URL= | cut -d= -f2-) \
uv run --extra retrieval --extra postgres python -m copilot.retrieval.indexer \
  --corpus-dir ./data/guidelines
```

The CLI logs `N total / M new / K skipped` per file. Idempotent — only
new chunk_ids are sent to Cohere, so re-runs are cheap.

### Verifying the corpus

```bash
uv run python -c "
import os, psycopg
with psycopg.connect(os.environ['CHECKPOINTER_DSN']) as c, c.cursor() as cur:
    cur.execute('SELECT guideline, count(*) FROM guideline_chunks GROUP BY guideline ORDER BY guideline')
    for r in cur.fetchall(): print(r)
"
```

### Where to source more guidelines

All of these publish free PDFs that fit the indexer's PDF-first input.

| Topic | Source | URL |
|---|---|---|
| Diabetes (ADA Standards of Care, annual) | `diabetesjournals.org/care` | https://diabetesjournals.org/care/issue |
| Hypertension (ACC/AHA 2017, more current than JNC8) | `professional.heart.org` | https://professional.heart.org/en/guidelines-and-statements |
| CKD / glomerular / BP-in-CKD / anemia | KDIGO | https://kdigo.org/guidelines/ |
| Cholesterol (ACC/AHA) | `acc.org` | https://www.acc.org/Guidelines |
| Heart failure (AHA/ACC/HFSA) | `professional.heart.org` | https://professional.heart.org/en/guidelines-and-statements |
| Atrial fibrillation (AHA/ACC/HRS) | `professional.heart.org` | https://professional.heart.org/en/guidelines-and-statements |
| Preventive screening (USPSTF) | `uspreventiveservicestaskforce.org` | https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics |

Long-form clinical guidelines (40–80 pages) typically yield 50–200
chunks each with the default 512/64 token-window settings; the bundled
fixtures are abridged and only produce ~5 chunks apiece.

## Regenerating the fixture PDFs

The bundled fixture PDFs are produced by `scripts/build_guideline_fixtures.py`
from inline plain-text excerpts. Run that script after editing the
excerpts to regenerate the PDFs deterministically.
