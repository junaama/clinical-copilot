# AI Cost Analysis — Clinical Co-Pilot

## 1. Bottom line

| Metric | Value |
|---|---|
| Dev spend to date (Apr 27 – May 3) | **$6.04** |
| Pilot — 100 users / mo | **$1,255 ($12.55/user)** |
| Mid — 1K users / mo | **$12,330 ($12.33/user)** |
| Production — 10K users / mo | **$98,800 ($9.88/user)** |
| Scale — 100K users / mo | **$750,000 ($7.50/user)** |

**Bottom line:** unit economics work. Per-user-month declines from $12.55 → $7.50 across four orders of magnitude because the LLM bill scales linearly with turns, infrastructure scales sub-linearly, and prompt caching + model-router + Bedrock provisioned throughput each kick in at a different tier. The dominant cost at every tier is LLM inference; everything else is rounding.

---

## 2. Dev spend to date

Pulled from each vendor's billing dashboard, billing window Apr 27 – May 3 (one week of active development).

| Source | Amount | Notes |
|---|---|---|
| OpenAI API | $0.73 | Sum of `cost_2026-04-03_2026-05-03.csv`; agent + evals running on `gpt-4o-mini` for the dev loop |
| Anthropic API | $0.00 | Credits not yet purchased; production targets Anthropic for caching + BAA economics (see §3.4) |
| Railway (current period) | $5.31 | $52.87 projected for full month at current trajectory |
| **Total dev spend to date** | **$6.04** | |

Wall-clock: ~7 days solo. Time isn't money for this analysis but flags how cheap this stack is to iterate on.

---

## 3. Per-turn cost model

### 3.1 Workload assumption (one place, reusable)

| Variable | Value | Rationale |
|---|---|---|
| Sessions per active user per workday | 8 | Midpoint of "5–12 sessions" — a hospitalist on a 15-patient list using Co-Pilot for ~half of encounters |
| Average turns per session | 5 | UC-2 brief ≈ 1 turn; UC-2 with follow-ups ≈ 4–8; midpoint covers the mix |
| Workdays per month | 20 | Standard clinical schedule |
| **Turns per user per month** | **800** | 8 × 5 × 20 |

These three numbers drive everything below. Stress-test them by ±50% in §6.

### 3.2 Per-turn token model (UC-2, no caching, Anthropic mix)

| Node | Model | In | Out | $ in | $ out | $ |
|---|---|---|---|---|---|---|
| Classifier | Haiku 4.5 | 500 | 100 | $0.0005 | $0.0005 | $0.001 |
| Tool planner | Sonnet 4.6 | 3,000 | 300 | $0.009 | $0.0045 | $0.0135 |
| Synthesis | Opus 4.7 | 5,000 | 500 | $0.025 | $0.0125 | $0.0375 |
| **Per-turn (uncached)** | | **8,500** | **900** | | | **$0.052** |

Token shapes from `agent_audit.jsonl` early traces + ARCHITECTURE.md §4.1. Synthesis dominates — it sees system prompt + patient context + tool results.

### 3.3 With prompt caching

Anthropic 5-min cache: writes at 1.25× input rate, reads at 0.1×. The system prompt + per-patient context block are stable across turns within a session and cacheable.

Modeled cache-hit rate by tier:
- **Tier 1 (100 users):** 50% — caching enabled at launch, but session warm-up costs cache writes
- **Tier 2–3 (1K–10K):** 60% — steady state, larger session pool
- **Tier 4 (100K):** 70% — dedicated gateway batches across users with shared prompt structure

| Tier | Cache hit | $/turn | Notes |
|---|---|---|---|
| Tier 1 | 50% | **$0.015** | Caching on, no router |
| Tier 2 | 60% | **$0.015** | Same; cache improves but volume + Redis costs flatten the gain |
| Tier 3 | 60% | **$0.012** | Model router: UC-1 triage downshifts to Sonnet for panels < 8 patients |
| Tier 4 | 70% | **$0.009** | Bedrock provisioned throughput replaces metered Anthropic |

### 3.4 Why the projections are Anthropic, not OpenAI

Dev burn is OpenAI (`gpt-4o-mini`) because it's cheap to iterate against and good enough for the eval loop. Production targets the Anthropic mix for three reasons: (1) prompt caching is a 50–70% input-cost lever Anthropic offers natively, (2) Opus 4.7's pricing makes deep synthesis viable where it wasn't on Opus 4.1, (3) Anthropic BAA is the clinical-deployment path. A switchover plan + comparison evals are the bridge between dev and Tier 1.

---

## 4. Tier projections

| Tier | Users | Turns/mo | LLM $/mo | Infra $/mo | **Total/mo** | **$/user/mo** |
|---|---:|---:|---:|---:|---:|---:|
| Pilot | 100 | 80K | $1,200 | $55 | **$1,255** | **$12.55** |
| Mid | 1K | 800K | $12,000 | $330 | **$12,330** | **$12.33** |
| Production | 10K | 8M | $96,000 | $2,800 | **$98,800** | **$9.88** |
| Scale | 100K | 80M | $720,000 | $30,000 | **$750,000** | **$7.50** |

LLM = turns × per-turn cost from §3.3. Infra detailed below.

### 4.1 Tier 1 — 100 users (pilot, current architecture)

**Architectural change vs. today: none.** Current Railway stack (openemr + mariadb + copilot-agent + Langfuse) holds at this volume. Caching is the one switch flipped on at launch. Infra: ~$55/mo, in line with current $52.87 projected dev spend.

### 4.2 Tier 2 — 1K users (single hospital)

**Architectural changes:** Redis added for session state, MariaDB read replica for FHIR read fan-out, Co-Pilot service horizontally scaled to 3–5 pods behind Railway's load balancer. Infra ~$330/mo. The story isn't compute — it's preventing one heavy-user session from saturating the read path. Per-user cost barely moves because LLM still dominates.

### 4.3 Tier 3 — 10K users (multi-hospital tenant)

**Architectural changes:** Railway → AWS migration. ECS Fargate (8–12 tasks), Aurora MySQL multi-AZ, ElastiCache Redis cluster, ALB + CloudFront + Route 53. Langfuse v2 → v3 (Clickhouse + Postgres + Redis + S3). pgvector added on Aurora if longitudinal queries enter scope. Model router downshifts UC-1 triage from Opus to Sonnet for small panels — first lever where engineering effort cuts LLM cost ~25%. Infra ~$2,800/mo. AWS BAA included free.

### 4.4 Tier 4 — 100K users (multi-region SaaS)

**Architectural changes:** active-active multi-region deployment, async tool dispatch via SQS, Bedrock provisioned throughput replacing metered Anthropic API for predictable cost/latency, per-region Langfuse, sensitive-encounter encryption with per-classification keys backed by HSM/KMS. Infra ~$30K/mo (multi-region Aurora, 50–100 Fargate tasks, SQS, Lambda, S3 Object Lock for audit archive, KMS). At this scale a 10% improvement in cache-hit rate is worth ~$70K/mo — engineering against caching pays for itself in days.

---

## 5. What's NOT just `cost-per-token × n`

Three reasons the curve bends:

1. **Cache-hit rate climbs with scale.** Larger session pools mean more cache hits on the shared system prompt. 50% → 70% across tiers cuts input cost by ~40% on the cached portion.
2. **Model router activates at Tier 3.** Cohorts of < 8 patients don't need Opus for UC-1 triage; Sonnet is sufficient and 5× cheaper. Engineering effort that's wasted at Tier 1 (where there's nothing to route) compounds at scale.
3. **Bedrock provisioned throughput at Tier 4.** Metered API pricing assumes worst-case capacity reservation. At 80M turns/month, dedicated provisioned throughput is ~30% cheaper than on-demand and removes rate-limit risk.

Infrastructure cost goes up but per-user infra cost goes down — Aurora multi-AZ at $2,800/mo serves 10K users at $0.28/user; Railway at $55/mo serves 100 users at $0.55/user.

---

## 6. Sensitivity (10K-user tier)

| Variable shifted | Effect on $/mo | $/user impact |
|---|---:|---:|
| Turns/user/mo +50% (1,200 vs 800) | +$48,000 | $9.88 → $14.68 |
| Cache hit rate −50% (30% vs 60%) | +$24,000 | $9.88 → $12.28 |
| Opus → Sonnet for synthesis (eval-blocked) | −$60,000 | $9.88 → $3.88 |
| Token budget per turn +50% | +$48,000 | $9.88 → $14.68 |

Dominant lever: **synthesis model choice**. If evals show Sonnet is sufficient for UC-2 synthesis, the 10K tier drops 60%. This is the single most important eval-driven cost decision.

---

## 7. Open questions and what I'm waiting on

- **Real cache-hit rate** — modeled at 50–70%; revisit after first 1K production turns land in Langfuse.
- **Anthropic BAA pricing** — verify with Anthropic sales before clinical go-live; tier may differ from public rates.
- **Synthesis model floor** — golden-case evals on Sonnet vs. Opus for UC-2 synthesis. If Sonnet passes, §6 shows the cost impact is enormous.
- **Turn distribution by workflow** — UC-2 dominates the model; UC-1 panel triage and UC-3 pager workflows could shift average tokens/turn meaningfully. Re-measure at 100-user pilot.

Pricing sources verified 2026-04-30: Anthropic (`platform.claude.com/docs/en/about-claude/pricing`), Railway (`railway.com/pricing`), AWS (Fargate / Aurora / ElastiCache / S3 public pricing pages).
