"""Date resolution, V1 and the policy decision: plain code, no database or model."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from calipa.interpreter.dates import PeriodUnresolved, resolve_period
from calipa.interpreter.policy import Approve, Clarify, Deny, Unsupported, decide
from calipa.interpreter.schema import PeriodSpec
from calipa.interpreter.validate import check_interpretation
from calipa.llm.prompts import load_prompt
from calipa.orchestrator.lifecycle import RunState
from calipa.reference import load_control_catalog, load_entitlements
from tests.interpreter_helpers import NOW, period, policy_context, scope

TODAY = date(2026, 10, 26)  # a Monday
CONTROLS = load_control_catalog()


def spec(**overrides: object) -> PeriodSpec:
    return PeriodSpec.model_validate(period(**overrides))


# --- dates ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "start", "end"),
    [
        ({"relative_unit": "day", "relative_offset": -1}, date(2026, 10, 25), date(2026, 10, 25)),
        ({"relative_unit": "week", "relative_offset": -1}, date(2026, 10, 19), date(2026, 10, 25)),
        ({}, date(2026, 9, 1), date(2026, 9, 30)),
        ({"relative_count": 3}, date(2026, 7, 1), date(2026, 9, 30)),
        (
            {"relative_unit": "quarter", "calendar": "calendar"},
            date(2026, 7, 1),
            date(2026, 9, 30),
        ),
        (
            {"relative_unit": "year", "calendar": "calendar"},
            date(2025, 1, 1),
            date(2025, 12, 31),
        ),
        (
            {"kind": "absolute", "start": date(2026, 2, 1), "end": date(2026, 2, 28)},
            date(2026, 2, 1),
            date(2026, 2, 28),
        ),
        ({"kind": "absolute", "start": date(2026, 10, 1)}, date(2026, 10, 1), date(2026, 10, 1)),
    ],
)
def test_periods_resolve_to_dates(overrides: dict[str, object], start: date, end: date) -> None:
    resolved = resolve_period(spec(**overrides), today=TODAY)
    assert (resolved.start, resolved.end) == (start, end)
    assert resolved.rule


def test_month_arithmetic_crosses_year_ends() -> None:
    resolved = resolve_period(spec(), today=date(2026, 1, 5))
    assert (resolved.start, resolved.end) == (date(2025, 12, 1), date(2025, 12, 31))
    leap = resolve_period(spec(relative_offset=-8), today=date(2024, 10, 1))
    assert (leap.start, leap.end) == (date(2024, 2, 1), date(2024, 2, 29))


def test_fiscal_quarters_follow_the_configured_year() -> None:
    fiscal = spec(relative_unit="quarter", relative_offset=0, calendar="fiscal")
    # A fiscal year starting in February: its quarters start Feb, May, Aug, Nov.
    resolved = resolve_period(fiscal, today=date(2026, 1, 5), fiscal_year_start_month=2)
    assert (resolved.start, resolved.end) == (date(2025, 11, 1), date(2026, 1, 31))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"relative_unit": "quarter"}, "calendar quarter or the fiscal quarter"),
        ({"relative_unit": "year", "calendar": "fiscal"}, "fiscal calendar is not set up"),
        ({"relative_offset": 1}, "past periods"),
        ({"kind": "absolute"}, "start from"),
        ({"kind": "absolute", "start": date(2026, 3, 2), "end": date(2026, 3, 1)}, "before"),
    ],
)
def test_unresolvable_periods_become_questions(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(PeriodUnresolved, match=message):
        resolve_period(spec(**overrides), today=TODAY)


# --- V1 ------------------------------------------------------------------------------

SAID = ["Check fee and VAT legs for LAST   month, reference FT-2026-0001"]


def v1(**overrides: object) -> list[str]:
    return check_interpretation(scope(**overrides), controls=CONTROLS, auditor_text=SAID)


def test_a_sound_interpretation_passes() -> None:
    assert v1(transaction_references=["FT-2026-0001"], control_ids=["C-FEE-LEG"]) == []


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"period": None}, "needs a period"),
        ({"objective": None}, "needs an objective"),
        ({"clarifying_question": "Which?"}, "must not have a clarifying_question"),
        ({"all_entitled_accounts": False}, "needs account_ids"),
        ({"account_ids": ["VAT"]}, "must be empty when all_entitled_accounts"),
        ({"control_ids": ["C-MADE-UP"]}, "not in <control_catalog>"),
        ({"control_ids": ["C-FX"]}, "not part of objective"),
        ({"currencies": ["USD", "USD"]}, "duplicates"),
        ({"period": period(source_text="the last quarter")}, "auditor's words"),
        ({"period": period(start=date(2026, 9, 1))}, "leave start and end null"),
        ({"period": period(kind="absolute")}, "needs start"),
        ({"transaction_references": ["FT-2026-0002"]}, "does not appear"),
        ({"status": "needs_clarification"}, "needs a clarifying_question"),
        ({"status": "out_of_scope"}, "needs a status_reason"),
    ],
)
def test_v1_catches(overrides: dict[str, object], error: str) -> None:
    errors = v1(**overrides)
    assert any(error in e for e in errors), errors


# --- policy --------------------------------------------------------------------------


def test_approves_with_resolved_dates_and_versions() -> None:
    ctx = policy_context()
    decision = decide(scope(), initiator="auditor@bank", now=NOW, ctx=ctx)
    assert isinstance(decision, Approve)
    approved = decision.scope
    assert (approved.period_start, approved.period_end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert approved.account_roles == ["FEE_INCOME", "TRANSIENT", "VAT"]
    assert approved.control_ids == ["C-FEE-LEG", "C-VAT-LEG", "C-STRUCTURE"]
    assert approved.timezone == "Africa/Lagos"
    assert approved.entitlements_sha256 == ctx.entitlements.sha256
    assert approved.control_catalog_sha256 == ctx.controls.sha256


def test_yesterday_is_closed_but_today_is_not() -> None:
    yesterday = scope(period=period(relative_unit="day", source_text="yesterday"))
    assert isinstance(
        decide(yesterday, initiator="auditor@bank", now=NOW, ctx=policy_context()), Approve
    )
    this_month = scope(period=period(relative_offset=0, source_text="this month"))
    decision = decide(this_month, initiator="auditor@bank", now=NOW, ctx=policy_context())
    assert isinstance(decision, Clarify)
    assert "stop at 2026-10-25" in decision.question


@pytest.mark.parametrize(
    ("initiator", "overrides", "kind", "text"),
    [
        ("stranger@bank", {}, Deny, "not entitled to start"),
        ("reviewer@bank", {}, Deny, "not entitled to start"),
        ("fees@bank", {"objective": "fx_conversion_check"}, Deny, "objective"),
        (
            "fees@bank",
            {"all_entitled_accounts": False, "account_ids": ["TRANSIENT"]},
            Deny,
            "accounts TRANSIENT",
        ),
        ("fees@bank", {"period": period(relative_count=3)}, Clarify, "at most 31 days"),
        ("auditor@bank", {"period": period(relative_count=6)}, Clarify, "at most 92 days"),
        ("auditor@bank", {"status": "out_of_scope", "status_reason": "asks to post"}, Deny, "post"),
        (
            "auditor@bank",
            {"status": "unsupported", "status_reason": "no logs"},
            Unsupported,
            "logs",
        ),
        (
            "auditor@bank",
            {"unsupported_constraints": ["branch: Ikeja"]},
            Clarify,
            "cannot filter by: branch: Ikeja",
        ),
        (
            "auditor@bank",
            {"period": period(relative_unit="quarter", source_text="Q3")},
            Clarify,
            "calendar quarter or the fiscal",
        ),
    ],
)
def test_policy_outcomes(
    initiator: str, overrides: dict[str, object], kind: type, text: str
) -> None:
    decision = decide(scope(**overrides), initiator=initiator, now=NOW, ctx=policy_context())
    assert isinstance(decision, kind)
    assert text in (decision.question if isinstance(decision, Clarify) else decision.reason)  # type: ignore[union-attr]


def test_a_narrower_entitlement_limits_all_accounts() -> None:
    decision = decide(scope(), initiator="fees@bank", now=NOW, ctx=policy_context())
    assert isinstance(decision, Approve)
    assert decision.scope.account_roles == ["FEE_INCOME", "VAT"]


# --- the shipped prompt and eval set -------------------------------------------------


def test_the_interpreter_prompt_ships_with_the_package() -> None:
    prompt = load_prompt("interpreter", 2)
    assert "<conversation>" in prompt.text and len(prompt.sha256) == 64


def test_p1_eval_cases_are_well_formed() -> None:
    lines = (Path(__file__).parent.parent / "evals/p1/cases.jsonl").read_text().splitlines()
    cases = [json.loads(line) for line in lines]
    assert len({c["id"] for c in cases}) == len(cases) >= 40
    users = load_entitlements().users
    allowed = {"objective", "period_start", "period_end", "account_roles", "currencies"}
    allowed |= {"transaction_references"}
    for case in cases:
        assert case["initiator"] in users
        assert all(RunState(s) for s in case["expect"]["state"])
        assert set(case["expect"].get("scope", {})) <= allowed
