"""The evidence builder: grouping, role labels, computed facts, category gating, and
that the same data in any order gives the same bundle."""

from __future__ import annotations

import json
import random
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from calipa.analyst.categories import CATEGORIES, allowed_categories
from calipa.analyst.step import user_content
from calipa.engine.contract import ExceptionType
from calipa.errors import IntegrityViolation
from calipa.orchestrator.steps import ApprovedScope, in_scope_exceptions
from calipa.reference import load_account_catalog, load_control_catalog, load_tariff
from calipa.synthetic import EXPECTED_ROOT_CAUSE, Anomaly, generate
from tests.analyst_helpers import T0001, T0002, find, groups, real_group, reconcile
from tests.fake_fcy import real_gl_entries

CATALOG = load_account_catalog()


def test_linked_transfers_form_one_group() -> None:
    group = real_group()
    bundle = group.bundle
    assert [str(t) for t in bundle.transfer_ids] == sorted([T0001, T0002])
    assert sorted(e.type.value for e in bundle.exceptions) == sorted(
        [
            "FEE_LEG_MISSING",
            "VAT_LEG_MISSING",
            "TRANSIENT_RESIDUAL",
            "DUPLICATE_INSTRUCTION_CANDIDATE",
            "FEE_VAT_IN_BOTH_CURRENCIES",
            "ENTRY_DIRECTION_INCONSISTENT",
        ]
    )
    assert bundle.severity.value == "high"
    # Ids run E1..En with the exceptions first.
    assert [i.evidence_id for i in bundle.items] == [
        f"E{n}" for n in range(1, len(bundle.items) + 1)
    ]
    assert [e.evidence_id for e in bundle.exceptions] == [f"E{n}" for n in range(1, 7)]


def test_claude_never_sees_account_numbers() -> None:
    group = real_group()
    assert group.labels == {"CUSTOMER_1": "5648968500", "BENEFICIARY_1": "6307882400"}
    content = user_content(group.bundle)
    for number in [*CATALOG.roles, *group.labels.values()]:
        assert number not in content
    assert not re.search(r"(?<!\d)\d{10}(?!\d)", content)
    debited = {i.fields["debited"] for i in group.bundle.items if i.type == "gl_entry"}
    assert debited == {"CUSTOMER_1", "TRANSIENT"}


def test_exception_items_point_at_their_entries() -> None:
    bundle = real_group().bundle
    direction = bundle.item(
        next(
            e.evidence_id
            for e in bundle.exceptions
            if e.type is ExceptionType.ENTRY_DIRECTION_INCONSISTENT
        )
    )
    assert direction is not None
    referenced = [bundle.item(r) for r in direction.fields["records"].split(", ")]
    assert {r.fields["entry_id"][:8] for r in referenced if r} == {"8af5b26b", "d817d930"}
    assert direction.fields["detail_note"] == "meaning of entryType is unconfirmed by the bank"


def test_computed_facts() -> None:
    bundle = real_group().bundle

    def fields(**match: str) -> dict[str, str]:
        item = bundle.item(find(bundle, "derived_fact", **match))
        assert item is not None
        return item.fields

    legs = fields(fact="leg_counts", transfer_id=T0001)
    assert (legs["funding_legs"], legs["payout_legs"]) == ("1", "1")
    assert (legs["fee_legs"], legs["vat_legs"], legs["fee_and_vat_leg_currencies"]) == (
        "0",
        "0",
        "none",
    )
    assert fields(fact="leg_counts", transfer_id=T0002)["fee_and_vat_leg_currencies"] == "GBP, USD"
    siblings = fields(fact="sibling_transfers")
    assert (
        siblings["seconds_apart"],
        siblings["same_customer"],
        siblings["same_payout_amount"],
    ) == ("18", "yes", "yes")
    fee = next(e.evidence_id for e in bundle.exceptions if e.type is ExceptionType.FEE_LEG_MISSING)
    comparison = fields(fact="amount_comparison", exception=fee)
    assert comparison["expected"] == "1.47" and comparison["difference_in_minor_units"] == "-147"
    movement = fields(fact="transient_movement", transfer_id=T0001, currency="GBP")
    assert (movement["credits_to_transient"], movement["net"]) == ("12.22", "12.22")
    timing = fields(fact="timing", transfer_id=T0002)
    assert (timing["seconds_first_to_last"], timing["local_dates"]) == ("6", "2026-10-25")


def test_fx_facts_show_the_rate_the_posting_implies() -> None:
    data = _synthetic({Anomaly.FX_RATE_WRONG: 1})
    [group] = groups(data.entries, rates=data.rates)
    bundle = group.bundle
    item = bundle.item(find(bundle, "derived_fact", fact="amount_comparison"))
    assert item is not None
    recorded = Decimal(item.fields["recorded_rate"])
    implied = Decimal(item.fields["rate_implied_by_actual"])
    # The planted rate is 1% too high; the implied rate shows it, to rounding.
    assert abs(implied / recorded - Decimal("1.01")) < Decimal("0.001")
    assert any(i.type == "rate_snapshot" for i in bundle.items)


def test_same_data_in_any_order_gives_the_same_bundle() -> None:
    raw = real_gl_entries()
    shuffled = list(raw)
    random.Random(3).shuffle(shuffled)
    [a], [b] = groups(raw), groups(shuffled)
    assert a.sha256 == b.sha256
    assert user_content(a.bundle) == user_content(b.bundle)


def test_categories_are_gated_by_available_evidence() -> None:
    bundle = real_group().bundle
    available = bundle.available_categories
    assert "unexplained" in available
    assert not {"missing_reversal", "switch_timeout_unposted", "manual_adjustment"} & set(available)
    assert all(CATEGORIES[c].available for c in available)
    assert any("missing_reversal cannot be chosen" in g for g in bundle.known_data_gaps)
    duplicate = next(
        e for e in bundle.exceptions if e.type is ExceptionType.DUPLICATE_INSTRUCTION_CANDIDATE
    )
    assert duplicate.allowed_categories == ["duplicate_customer_instruction", "unexplained"]


def test_a_group_whose_checks_did_not_run_is_not_analysable() -> None:
    data = _synthetic({Anomaly.CLEAN: 1})
    [group] = groups(data.entries, rates=[])
    assert {e.type for e in group.bundle.exceptions} == {ExceptionType.RATE_UNAVAILABLE}
    assert not group.bundle.analysable


@pytest.mark.parametrize("anomaly", sorted(EXPECTED_ROOT_CAUSE))
def test_the_right_answer_is_always_on_offer(anomaly: Anomaly) -> None:
    """For every planted problem, the root cause the evals expect must be an allowed
    category for at least one of the group's exceptions, after the support check has
    ruled out what the evidence contradicts."""
    for seed in range(5):
        data = _synthetic({anomaly: 1}, seed=seed)
        found = groups(data.entries, rates=data.rates)
        planted = data.labels[-1].transfer_id
        [group] = [g for g in found if planted in g.bundle.transfer_ids]
        offered = {c for e in group.bundle.exceptions for c in e.allowed_categories}
        assert EXPECTED_ROOT_CAUSE[anomaly] & offered, seed
        for e in group.bundle.exceptions:
            assert set(e.allowed_categories) | set(e.ruled_out) == set(allowed_categories(e.type))


def test_only_the_approved_controls_are_in_scope() -> None:
    _, result = reconcile()
    scope = ApprovedScope(
        objective="duplicate_detection",
        period_start=date(2026, 10, 25),
        period_end=date(2026, 10, 25),
        timezone="Africa/Lagos",
        control_ids=["C-DUPLICATE"],
        account_catalog_sha256=CATALOG.sha256,
        tariff_sha256=load_tariff().sha256,
        approved_by="test",
    )
    kept = in_scope_exceptions(result.exceptions, scope, load_control_catalog())
    assert {e.type for e in kept} == {ExceptionType.DUPLICATE_INSTRUCTION_CANDIDATE}
    unscoped = scope.model_copy(update={"control_ids": []})
    assert len(in_scope_exceptions(result.exceptions, unscoped, load_control_catalog())) == len(
        result.exceptions
    )
    stale = scope.model_copy(update={"control_catalog_sha256": "f" * 64})
    with pytest.raises(IntegrityViolation, match="control catalog changed"):
        in_scope_exceptions(result.exceptions, stale, load_control_catalog())


def _synthetic(counts: dict[Anomaly, int], seed: int = 11):  # type: ignore[no-untyped-def]
    return generate(
        seed,
        day=date(2026, 10, 25),
        counts=counts,
        catalog=CATALOG,
        tariff=load_tariff(),
        tz=ZoneInfo("Africa/Lagos"),
    )


def test_p2_eval_cases_are_well_formed() -> None:
    lines = (Path(__file__).parent.parent / "evals/p2/cases.jsonl").read_text().splitlines()
    cases = [json.loads(line) for line in lines]
    assert len({c["id"] for c in cases}) == len(cases) >= 150
    for case in cases:
        anomaly = Anomaly(case["anomaly"])
        assert anomaly is Anomaly.CLEAN or anomaly in EXPECTED_ROOT_CAUSE
        assert case["variant"] in ("plain", "injected")
    assert set(EXPECTED_ROOT_CAUSE) <= {Anomaly(c["anomaly"]) for c in cases}
