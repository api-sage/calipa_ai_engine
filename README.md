# calipa_ai_engine
Calipa is an AI-powered General Ledger (GL) reconciliation, audit, and assurance platform that integrates with core banking systems to validate transactions, detect discrepancies, verify reversals, investigate root causes, and generate evidence-backed audit reports through natural-language requests.

## Intelligence Layer (Python)

Design: see the "Calipa Intelligence Layer" design doc (v0.2).

```bash
uv sync                      # install
cp .env.example .env         # then fill in credentials (never commit .env)
uv run pytest                # tests; needs a local Postgres (creates and drops a temp DB)
uv run ruff check . && uv run ruff format --check . && uv run mypy   # src, tests, evals
```

CI (`.github/workflows/ci.yml`) runs the same checks on every pull request, with a
Postgres service; there a database the tests cannot reach is a failure, not a skip.

Layout:

- `src/calipa/money.py` – Decimal-only money and currency; float JSON parsing helper
- `src/calipa/canonical.py` – canonical JSON and SHA-256 used for every hash
- `src/calipa/audit_trail/` – append-only Postgres schema, hash-chained run events, raw blob store, migrations (`python -m calipa.audit_trail.db migrate|check`)
- `src/calipa/audit_trail/anchor.py` – chain heads anchored to write-once storage, and the check that catches a rewritten history (`python -m calipa.audit_trail.anchor publish|verify`)
- `src/calipa/llm/` – the only path to a model: Meta Llama API (default) or Claude, chosen by `CALIPA_LLM_PROVIDER`; logs request, raw response, served model, usage, cost (Llama's price comes from `CALIPA_LLAMA_PRICE_*`)
- `src/calipa/gateway/http.py` – read-only HTTP client: GET allowlist enforced twice, retries, every call logged
- `src/calipa/gateway/fcy.py` – typed FCY API client; strict models fail loudly on API changes
- `src/calipa/gateway/snapshot.py` – reliable GL snapshots (closed periods, per-day reads, count checks, hashes)
- `src/calipa/gateway/rates.py` – daily FX rate copies
- `src/calipa/reference/` – versioned account roles, fee schedule, control catalog and entitlements (drafts, pending confirmation)
- `src/calipa/engine/` – draft Reconciliation Engine contract and a temporary stand-in engine
- `src/calipa/synthetic/` – seeded synthetic GL data with planted, labelled anomalies
- `src/calipa/orchestrator/` – run state machine, step runner with retries and resume, workers (advisory locks)
- `src/calipa/interpreter/` – P1 Query Interpreter step: V1 checks, date resolution in code, policy decision (entitlements, closed periods, limits)
- `src/calipa/analyst/` – P2 Evidence Analyst step: evidence bundles (role labels, computed facts, gated root causes), the support check (categories the evidence contradicts are ruled out), V2 claim checker, findings with caps and resume, groups analysed in parallel, unchanged findings carried over to a re-investigation
- `src/calipa/reporter/` – P3 Report Writer step: facts (keys only reach P3), V3 report checker, template fallback, Markdown report rendered and hashed by code
- `src/calipa/review/` – report versions and second-person review: segregation of duties, decisions bound to the report hash, flag acknowledgement, re-investigation as a linked run (rules also enforced by database triggers)
- `src/calipa/api/` – FastAPI: start runs (idempotent), run status, server-sent events, report (JSON and Markdown), clarification answers, review
- `src/calipa/worker.py` – background worker wiring every step; advances runs the API records and anchors the audit trail
- `src/calipa/metrics.py` – operational metrics from the audit trail, served at `GET /metrics` (aggregates only)
- `src/calipa/prompts/` – versioned prompt files; `ACTIVE` in `src/calipa/llm/prompts.py` names the version each step uses
- `evals/` – eval sets and runners (`evals/p1/`, `evals/run_p1.py`, `evals/p2/`, `evals/run_p2.py`, `evals/p3/`, `evals/run_p3.py`)
- `ops/db_roles.sql` – production roles (app can only SELECT/INSERT)
- `ops/alerts.yml` – Prometheus alert rules for `/metrics`
- `Dockerfile`, `ops/docker-compose.yml` – one image for API, worker and migrations; a local stack
- `ops/openapi.json` – the API contract the UI's TypeScript types are generated from (`ops/export_openapi.py` rewrites it; a test fails when it is stale)

Live read-only check against the real FCY API (needs credentials in `.env`):
`CALIPA_LIVE=1 uv run pytest tests/test_live_fcy.py`

Evals against the real model (need `LLAMA_API_KEY` and `CALIPA_LLAMA_MODEL`, or `CALIPA_LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY`; every call is recorded):
`uv run python evals/run_p1.py` (P1) and `uv run python evals/run_p2.py [--limit N]` (P2; about USD 0.10 per case on Claude) and `uv run python evals/run_p3.py [--limit N]` (P2 then P3 on runs with several findings; writes each report out for a person to read)

Running the service (API and worker are separate processes; runs only progress while a worker runs):

```bash
uv run python -m calipa.audit_trail.db migrate   # once per release, as calipa_migrator
CALIPA_API_IDENTITY=dev_header uv run uvicorn --factory calipa.api.app:app_from_settings --port 8000
uv run python -m calipa.worker          # needs FCY credentials and model keys in .env
uv run python -m calipa.audit_trail.anchor verify   # e.g. nightly; exits 1 on any mismatch
```

The worker refuses to start while migrations are pending (set `CALIPA_WORKER_AUTO_MIGRATE=true` to have it apply them in development). It anchors the audit trail every `CALIPA_ANCHOR_INTERVAL_SECONDS` to `CALIPA_ANCHOR_DIR`; a plain directory is fine locally, production needs storage that cannot be overwritten or deleted. `CALIPA_ANALYSIS_PARALLELISM` (default 4) sets how many exception groups P2 analyses at once.

`CALIPA_API_IDENTITY` defaults to `none`, which refuses every request until SSO is wired in. `dev_header` trusts an `X-Calipa-User` header naming a user in `reference/entitlements.v1.json` and is for development only.

UI types: `npx openapi-typescript ops/openapi.json -o <ui>/src/lib/calipa-api.d.ts`
