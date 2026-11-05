# Calipa

Calipa is an AI-assisted General Ledger (GL) reconciliation, audit, and assurance
platform. It validates ledger data, reconciles it against settlement statements,
investigates exceptions with evidence, and produces a report for independent review.
Auditors can also request read-only data from the core banking API.

## Why spreadsheets?

Financial institutions commonly exchange settlement records as Excel workbooks or
CSV exports. These are familiar, portable formats for sharing data that has been
exported or converted from internal banking and settlement systems, including
systems whose data is extracted using tools distributed as JAR files. Calipa's
goal is to validate and reconcile the records in the format institutions already
share, rather than require a new exchange format.

**Current upload support:** the API currently accepts `.xlsx` workbooks, using
Calipa's template, with GL entries and optional statement lines. CSV is a common
interchange format for the same reason, but CSV upload is not implemented yet.
The API rejects invalid workbook rows with a row-by-row explanation.

## How it works

1. An auditor uploads a workbook or requests a run using read-only banking data.
2. The API validates the request and records it; a separate worker advances the run.
3. Calipa reconciles ledger entries to statements, gathers evidence for exceptions,
   and generates a report with deterministic checks around model-produced content.
4. A second person reviews the report and records a decision bound to that report
   version. Re-investigations are linked to the earlier run.

The audit trail is append-only and hash-chained. Chain heads can be anchored to
write-once storage so later verification can detect rewritten history.

## Technology stack

| Area | Technologies |
| --- | --- |
| Backend and API | Python 3.12, FastAPI, Pydantic 2, Uvicorn |
| Data and persistence | PostgreSQL, psycopg connection pooling, append-only audit-trail tables |
| Spreadsheet ingestion | `openpyxl` for `.xlsx`; Python `Decimal`-based money handling |
| Banking integration | `httpx2`-based read-only HTTP client and typed FCY API client |
| AI providers | Meta Llama API (default), Meta Muse, or Anthropic Claude; selected by configuration |
| Web application | Next.js 15, React 19, TypeScript 5, Tailwind CSS 4, Radix UI |
| UI data and interaction | TanStack Query, Motion, Lucide icons, Sonner notifications |
| API contract | FastAPI OpenAPI document and generated TypeScript types via `openapi-typescript` |
| Operations | Docker, Docker Compose, Prometheus-format metrics and alert rules |
| Quality and tests | pytest, Ruff, mypy, ESLint, TypeScript, Playwright |

Model calls are routed through a shared client that records provider, model,
request/response, usage, and cost details in the audit trail. The model provider
and model IDs are configured per deployment; model credentials are required to
run the worker.

## Repository map

- `src/calipa/api/` - FastAPI endpoints, including workbook preview and template.
- `src/calipa/ingest/` - workbook parsing and row validation.
- `src/calipa/gateway/` - read-only banking API client, GL snapshots, and FX rates.
- `src/calipa/engine/` - reconciliation and GL-to-statement matching.
- `src/calipa/interpreter/`, `analyst/`, and `reporter/` - run analysis and report steps.
- `src/calipa/review/` - report versions, reviewer decisions, and segregation of duties.
- `src/calipa/audit_trail/` - database migrations, run events, artifacts, and anchoring.
- `src/calipa/worker.py` - background worker that advances runs.
- `src/calipa/prompts/` and `evals/` - versioned model prompts and evaluation sets.
- `web/` - auditor and reviewer web application.
- `ops/` - local Docker Compose stack, database roles, alerts, and OpenAPI export.
- `tests/` - backend unit, integration, and end-to-end tests.

## Local development

Requirements: Python 3.12, `uv`, Node.js with `pnpm`, and PostgreSQL. Alternatively,
the backend and database can be started with the Docker Compose file.

Install dependencies and configure environment variables:

```bash
uv sync
cp .env.example .env
```

On Windows PowerShell, copy the file with `Copy-Item .env.example .env`. Fill in
the needed credentials in `.env` and set `CALIPA_API_IDENTITY=dev_header` for local
development with the web UI. Never commit `.env`. This mode trusts the
`X-Calipa-User` header and is **not for production**. The default identity mode
refuses requests until a production sign-in integration is configured.

### Run the backend

The API and worker run as separate processes. Runs only progress while the worker
is running.

```bash
# Start PostgreSQL separately, then apply migrations.
uv run python -m calipa.audit_trail.db migrate

# Terminal 1: API
uv run uvicorn --factory calipa.api.app:app_from_settings --port 8000

# Terminal 2: worker (requires banking API and model credentials)
uv run python -m calipa.worker
```

The API serves a blank upload template at `GET /runs/uploads/template`. To run the
full local Docker stack (PostgreSQL, migrations, API, and worker):

```bash
docker compose -f ops/docker-compose.yml up --build
```

The Compose stack uses development defaults. Its anchor volume is not write-once;
production must use storage that cannot be overwritten or deleted. The worker
anchors the audit trail at `CALIPA_ANCHOR_INTERVAL_SECONDS` to `CALIPA_ANCHOR_DIR`.
Verify anchors with:

```bash
uv run python -m calipa.audit_trail.anchor verify
```

### Run the web application

With the API and worker running:

```bash
cd web
pnpm install
pnpm dev
```

Open <http://localhost:3000>. The UI uses the API through its Next.js middleware.
See [web/README.md](web/README.md) for UI details, browser tests, and development
guidance.

### Checks and tests

Backend checks (tests require a reachable local PostgreSQL database):

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Web checks, from `web/`:

```bash
pnpm typecheck
pnpm lint
pnpm e2e
```

The end-to-end tests use a local PostgreSQL database. The test suite does not call
live banking or model services by default.

To run the explicitly live FCY API check, provide credentials and opt in:

```bash
CALIPA_LIVE=1 uv run pytest tests/test_live_fcy.py
```

In PowerShell, use `$env:CALIPA_LIVE = "1"` before running the test.

Model evaluations also make real provider calls and may incur costs:

```bash
uv run python evals/run_p1.py
uv run python evals/run_p2.py [--limit N]
uv run python evals/run_p3.py [--limit N]
```

## Configuration and API types

Settings are read from the environment or `.env`; `.env.example` lists the available
settings. `CALIPA_LLM_PROVIDER` selects `meta`, `muse`, or `anthropic`. The model
keys are `LLAMA_API_KEY`, `MODEL_API_KEY`, or `ANTHROPIC_API_KEY`, respectively.
The FCY client uses `CALIPA_FCY_USERNAME` and `CALIPA_FCY_PASSWORD`.

The API contract is in `ops/openapi.json`. Export it after API changes, then
regenerate the web application's TypeScript types:

```bash
uv run python ops/export_openapi.py
cd web
pnpm api:types
```
