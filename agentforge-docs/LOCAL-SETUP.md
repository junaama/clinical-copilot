# Local Development Setup

Run the Clinical Co-Pilot agent, UI, and test suites on your machine.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | `brew install python@3.12` |
| uv | 0.5+ | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js | 20+ | `brew install node@20` or nvm |
| npm | 10+ | ships with Node |
| Railway CLI | latest | `npm i -g @railway/cli` (deploy only) |

## 1. Agent Backend

### Install

```bash
cd agent
uv sync --extra dev
```

Add `--extra postgres` if you want Postgres-backed conversation state,
`--extra eval` for the evaluation framework, or `--extra seed` for
OpenEMR seeding tools.

### Configure

```bash
cp .env.example .env
```

Edit `.env` — the minimum for local dev with fixtures:

```
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...

USE_FIXTURE_FHIR=1
```

That's it. Fixture mode serves a synthetic 5-patient panel in-process
with no OpenEMR or database connection.

#### Optional: real OpenEMR

Set `USE_FIXTURE_FHIR=0` and supply a FHIR bearer token:

```
USE_FIXTURE_FHIR=0
OPENEMR_FHIR_BASE=https://openemr-production-c5b4.up.railway.app/apis/default/fhir
OPENEMR_FHIR_TOKEN=<token from scripts/seed/get_token.py --system>
```

#### Optional: Postgres state

```
CHECKPOINTER_DSN=postgresql+asyncpg://user:pass@localhost:5432/copilot
COPILOT_TOKEN_ENC_KEY=<base64 of 32 random bytes>
```

Generate the key:

```bash
python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
```

Without `CHECKPOINTER_DSN`, the agent uses in-memory state (conversations
lost on restart — fine for dev).

#### Optional: Langfuse observability

```
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
```

Empty values disable Langfuse silently.

### Run

```bash
cd agent
uv run uvicorn copilot.server:app --reload --port 8000
```

Health check: `curl http://localhost:8000/health`

---

## 2. Copilot UI

### Install

```bash
cd copilot-ui
npm install
```

### Configure

```bash
cp .env.example .env
```

Leave `VITE_AGENT_URL` blank — Vite proxies `/api/*` to
`http://localhost:8000` automatically in dev mode.

### Run

```bash
npm run dev
```

Opens at `http://localhost:5173`. Requires the agent backend running on
port 8000.

---

## 3. Seed Data

### Fixture mode (no setup needed)

With `USE_FIXTURE_FHIR=1`, the agent serves 5 synthetic patients:

| ID | Name | Archetype |
|----|------|-----------|
| fixture-1 | Eduardo Perez | Overnight hypotensive event |
| fixture-2 | Maya Singh | Stable post-op |
| fixture-3 | Robert Chen | CHF decompensation |
| fixture-4 | Linda Okafor | New pneumonia admission |
| fixture-5 | James Washington | Stable observation |

`practitioner-dr-smith` is on fixture-1, fixture-3, fixture-5.
`practitioner-admin` bypasses the CareTeam gate (sees all 5).

### Real OpenEMR: bulk Synthea patients

```bash
railway ssh --service openemr \
  '. /root/devtoolsLibrary.source && prepareVariables && importRandomPatients 50 true'
```

The second argument **must be `true`** (dev mode). `false` puts patients
in a manual review queue.

### Real OpenEMR: CareTeam memberships for dr_smith

```bash
# Generate SQL and pipe into the container's mysql
cd agent
uv run python scripts/seed/seed_careteam.py --dry-run \
    --pids "$(railway ssh --service openemr \
        'mysql -N -u root openemr -e "SELECT GROUP_CONCAT(pid) FROM patient_data"')" \
  | railway ssh --service openemr 'mysql -u root openemr'
```

Or with direct DB access (requires `--extra seed`):

```bash
MYSQL_HOST=<host> MYSQL_PORT=3306 MYSQL_USER=root \
MYSQL_PASSWORD=<pass> MYSQL_DATABASE=openemr \
    uv run python scripts/seed/seed_careteam.py
```

### Real OpenEMR: FHIR bearer token

```bash
cd agent
OE_FHIR_BASE_URL=https://openemr-production-c5b4.up.railway.app \
    uv run python scripts/seed/get_token.py --system
```

Token is saved to `scripts/seed/secrets/last_token.json`.

---

## 4. Run Tests

### Agent (Python)

```bash
cd agent

# All unit tests (fixture mode, no DB required)
uv run pytest -q

# Single file
uv run pytest tests/test_care_team_gate.py -v

# Exclude Postgres-requiring tests
uv run pytest -q --ignore=tests/test_postgres_session_store.py

# Eval cases by tier
uv run pytest -q -m smoke
uv run pytest -q -m golden
uv run pytest -q -m adversarial

# Collect eval cases without running (verify YAML parses)
uv run pytest evals/ -m golden --collect-only -q
```

### Agent lint & type check

```bash
cd agent
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src/copilot
```

### UI (TypeScript)

```bash
cd copilot-ui

npm run test            # Vitest (single run)
npm run test:watch      # Watch mode
npm run test:coverage   # Coverage report
npm run typecheck       # tsc --noEmit
npm run lint            # ESLint
```

---

## 5. Deploy to Railway

```bash
bash scripts/deploy-agent.sh      # Agent backend
bash scripts/deploy-ui.sh         # Copilot UI
bash scripts/deploy-openemr.sh    # OpenEMR + copilot-launcher module
bash scripts/deploy-minio.sh      # MinIO (Langfuse storage)
bash scripts/deploy-all.sh        # All of the above
```

Watch logs: `railway logs --service copilot-agent`

---

## Environment Variable Reference

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `LLM_PROVIDER` | yes | `openai` | `openai` or `anthropic` |
| `LLM_MODEL` | yes | `gpt-4o-mini` | Model identifier |
| `OPENAI_API_KEY` | if openai | — | |
| `ANTHROPIC_API_KEY` | if anthropic | — | Also enables Haiku title summarizer |
| `USE_FIXTURE_FHIR` | no | `0` | `1` for synthetic data (no OpenEMR) |
| `OPENEMR_FHIR_BASE` | if real mode | prod URL | FHIR R4 endpoint |
| `OPENEMR_FHIR_TOKEN` | if real mode | — | Static bearer token |
| `CHECKPOINTER_DSN` | no | — | Postgres DSN; omit for in-memory |
| `COPILOT_TOKEN_ENC_KEY` | if DSN set | — | AES-256 base64 key |
| `SESSION_SECRET` | for standalone auth | — | Cookie signing secret |
| `COPILOT_ADMIN_USER_IDS` | no | — | CSV of admin Practitioner UUIDs |
| `ALLOWED_ORIGINS` | no | `localhost:5173` | CORS allow-list |
| `AGENT_AUDIT_LOG_PATH` | no | `./logs/agent_audit.jsonl` | |
| `LOG_LEVEL` | no | `INFO` | |
| `LANGFUSE_HOST` | no | — | Empty disables Langfuse |
| `LANGFUSE_PUBLIC_KEY` | no | — | |
| `LANGFUSE_SECRET_KEY` | no | — | |
| `VITE_AGENT_URL` | no | — | UI only; blank in dev (Vite proxies) |

---

## Quick Start (copy-paste)

```bash
# Terminal 1: agent
cd agent
uv sync --extra dev
cp .env.example .env
# Edit .env: set OPENAI_API_KEY, USE_FIXTURE_FHIR=1
uv run uvicorn copilot.server:app --reload --port 8000

# Terminal 2: UI
cd copilot-ui
npm install
npm run dev

# Terminal 3: tests
cd agent && uv run pytest -q
cd ../copilot-ui && npm run test
```
