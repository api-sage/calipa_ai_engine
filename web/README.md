# Calipa web

The auditor's and reviewer's UI for the Calipa Intelligence Layer: start an audit,
follow it live, answer Calipa's questions, read the report, and review it.

## Run it locally

Three processes, from the repository root:

```bash
# 1. The API (development sign-in: trusts the user picked in the UI)
CALIPA_API_IDENTITY=dev_header uv run uvicorn --factory calipa.api.app:app_from_settings --port 8765

# 2. The worker, which runs audits. It calls Claude, so it spends money.
uv run python -m calipa.worker

# 3. This app
cd web && pnpm dev
```

Open http://localhost:3000. Pick who you are with the user switch at the bottom of
the sidebar (auditor, reviewer, lead, fees analyst). Without the worker you can
browse every existing audit and report; new audits wait in "Queued".

On a slow network Node gives up on connections too early. Set
`NODE_OPTIONS="--network-family-autoselection-attempt-timeout=2000"` for `pnpm`.

## How it is put together

- `src/middleware.ts` forwards `/api/*` to the API and adds `X-Calipa-User`. When
  SSO lands, this is the one place that changes.
- `src/lib/api/schema.d.ts` is generated from the API's OpenAPI document. After an
  API change: `uv run python ops/export_openapi.py && pnpm api:types`.
- `src/app/globals.css` holds the design tokens, from the brand profile (paper
  neutrals, Calipa green, the signal orange of the logo dot). Type is Newsreader for
  headings, Geist for the interface and JetBrains Mono for figures. `/foundations`
  shows every token, atom, dialog and illustration in every state.
- `src/components/shell/logo.tsx` draws the wordmark from Newsreader's own outlines
  (wght 700, opsz 72), so it needs no font and matches the logo file exactly.
- `src/components/shell/` is the frame: icon rail (`[` collapses it), top bar with
  breadcrumbs, ⌘K search over audits, controls and actions, and `?` for shortcuts.
- `src/components/calipa/illustrations.tsx` holds the spot illustrations for empty,
  edge and error states; `dialogs.tsx` the confirm and error dialogs. Anything that
  spends money or cannot be undone (starting an audit, a review decision) is confirmed.
- Money is formatted from the decimal string the API sends (`src/lib/money.ts`),
  never through a JavaScript number, and never animated.

## Checks

```bash
pnpm typecheck && pnpm lint
pnpm e2e        # browser tests; needs local Postgres (CALIPA_TEST_ADMIN_URL to point elsewhere)
```

`next build` writes to `.next`, which the dev server also uses; stop `pnpm dev` first,
or build with `NEXT_DIST_DIR=<another folder>`.

### Browser tests

`e2e/` drives the built app in Chromium against the real API. `playwright.config.ts`
recreates a `calipa_e2e` database, seeds it with `tests/e2e_seed.py` (reports made with
the unit tests' scripted model, and a run waiting on a question), and starts the API on
:8766 and the app on :3100, built into `.next-e2e` so it runs beside `pnpm dev`. No
worker runs, so nothing calls a model or the bank. The tests check what the browser
caused through the API: one approval however often the button is pressed, nothing
recorded on cancel, reasons required, flags acknowledged, the author kept from review.
CI runs them on every pull request; on failure the trace is uploaded as an artifact
(`pnpm exec playwright show-trace <trace.zip>`).
