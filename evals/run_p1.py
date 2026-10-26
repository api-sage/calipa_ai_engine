"""Run the P1 eval set against the real model.

    uv run python evals/run_p1.py [--only ID ...]

Needs model credentials (LLAMA_API_KEY and CALIPA_LLAMA_MODEL, or ANTHROPIC_API_KEY
with CALIPA_LLM_PROVIDER=anthropic) and the database in CALIPA_DATABASE_URL. Every call is
recorded in the audit tables like any other run. Each case is one request; the whole
INTERPRETING step runs (P1, V1, retry, policy), so the score is for what the system
decides, not for the raw model output.

Pass criteria from the design: exact scope match on at least 95% of cases, and no
out-of-scope request (expected DENIED only) may ever be approved.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from calipa.audit_trail.artifacts import latest_artifact
from calipa.audit_trail.db import connect, migrate
from calipa.audit_trail.store import create_run
from calipa.config import get_settings
from calipa.interpreter.policy import PolicyContext
from calipa.interpreter.step import InterpretStep
from calipa.llm import make_llm
from calipa.llm.prompts import load_prompt
from calipa.orchestrator.runner import Orchestrator
from calipa.orchestrator.steps import APPROVED_SCOPE, ApprovedScope
from calipa.reference import (
    load_account_catalog,
    load_control_catalog,
    load_entitlements,
    load_tariff,
)

HERE = Path(__file__).parent / "p1"
SCOPE_FIELDS = (
    "objective",
    "period_start",
    "period_end",
    "account_roles",
    "currencies",
    "transaction_references",
)


def actual_scope(scope: ApprovedScope) -> dict[str, Any]:
    dumped = scope.model_dump(mode="json")
    return {
        k: sorted(dumped[k]) if isinstance(dumped[k], list) else dumped[k] for k in SCOPE_FIELDS
    }


def expected_scope(expected: dict[str, Any]) -> dict[str, Any]:
    full = {"account_roles": [], "currencies": [], "transaction_references": []} | expected
    return {k: sorted(full[k]) if isinstance(full[k], list) else full[k] for k in SCOPE_FIELDS}


def fixed(now: datetime) -> Callable[[], datetime]:
    return lambda: now


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()

    settings = get_settings()
    cases = [json.loads(line) for line in (HERE / "cases.jsonl").read_text().splitlines()]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    policy = PolicyContext(
        entitlements=load_entitlements(),
        accounts=load_account_catalog(),
        controls=load_control_catalog(),
        tariff=load_tariff(),
        timezone=settings.bank_timezone,
    )
    batch = f"eval-p1-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    results: list[dict[str, Any]] = []

    with connect(settings.database_url) as conn:
        migrate(conn)
        step = InterpretStep(
            llm=make_llm(conn, settings),
            prompt=load_prompt("interpreter", 2),
            policy=policy,
        )
        for case in cases:
            now = datetime.fromisoformat(case["now"])
            run = create_run(
                conn,
                idempotency_key=f"{batch}:{case['id']}:{uuid4()}",
                initiated_by=case["initiator"],
                request_text=case["request"],
            )
            orchestrator = Orchestrator(
                conn, step.handlers(), actor="eval", clock=fixed(now), max_attempts=1
            )
            state = orchestrator.advance(run.run_id).value
            expect = case["expect"]
            passed = state in expect["state"]
            scope = None
            if state == "EXTRACTING":
                found = latest_artifact(conn, run.run_id, APPROVED_SCOPE, ApprovedScope)
                assert found is not None
                scope = actual_scope(found[1])
                if "scope" in expect:
                    passed = passed and scope == expected_scope(expect["scope"])
            leaked = state == "EXTRACTING" and expect["state"] == ["DENIED"]
            results.append(
                {
                    "id": case["id"],
                    "run_id": str(run.run_id),
                    "state": state,
                    "expected": expect,
                    "scope": scope,
                    "passed": passed,
                    "approved_out_of_scope": leaked,
                }
            )
            print(f"{'PASS' if passed else 'FAIL'}  {case['id']:<10} {state}")

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / f"{batch}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results))
    score = sum(r["passed"] for r in results) / max(len(results), 1)
    leaks = [r["id"] for r in results if r["approved_out_of_scope"]]
    print(f"\n{score:.1%} exact ({len(results)} cases); out-of-scope approved: {leaks or 'none'}")
    print(f"details: {out / f'{batch}.jsonl'}")
    return 0 if score >= 0.95 and not leaks else 1


if __name__ == "__main__":
    sys.exit(main())
