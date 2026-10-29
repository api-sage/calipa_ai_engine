"""The support check: categories the evidence contradicts are never offered, and a
hypothesis cannot lean on claims about transfers it does not explain."""

from __future__ import annotations

from typing import Any

import pytest

from calipa.analyst.schema import ExceptionAnalysis, RootCause
from calipa.analyst.support import RULES, ruled_out, unrelated_support
from calipa.analyst.verify import check_analysis
from tests.analyst_helpers import (
    T0001,
    T0002,
    edited,
    exception,
    find,
    good_answer,
    hypothesis,
    observation,
    real_group,
)

BUNDLE = real_group().bundle
T = T0001
EXC = {"transfer_id": T, "type": "TRANSIENT_RESIDUAL"}


def _legs(fee: int, vat: int, currencies: str) -> dict[str, str]:
    return {
        "fact": "leg_counts",
        "transfer_id": T,
        "fee_legs": str(fee),
        "vat_legs": str(vat),
        "fee_and_vat_leg_currencies": currencies,
    }


def _entry(n: int, amount: str, credited: str = "FEE_INCOME") -> dict[str, str]:
    return {
        "entry_id": f"e{n}",
        "transfer_id": T,
        "debited": "TRANSIENT",
        "credited": credited,
        "currency": "USD",
        "amount": amount,
    }


def _out(*items: dict[str, str]) -> dict[RootCause, str]:
    everything = [("E1", EXC), *((f"E{i + 2}", f) for i, f in enumerate(items))]
    return ruled_out("E1", EXC, everything, RULES)


@pytest.mark.parametrize(
    ("items", "category", "contradicted"),
    [
        ([_legs(1, 1, "USD")], "fee_or_vat_leg_missing", True),
        ([_legs(0, 1, "USD")], "fee_or_vat_leg_missing", False),
        ([_legs(1, 1, "USD")], "fee_or_vat_leg_duplicated_across_ccy", True),
        ([_legs(2, 2, "EUR, USD")], "fee_or_vat_leg_duplicated_across_ccy", False),
        ([_entry(1, "5"), _entry(2, "5", "VAT")], "duplicate_posting_same_instruction", True),
        ([_entry(1, "5"), _entry(2, "5")], "duplicate_posting_same_instruction", False),
        (
            [{"fact": "transient_movement", "transfer_id": T, "net": "0"}],
            "transient_residual_unallocated",
            True,
        ),
        (
            [{"fact": "transient_movement", "transfer_id": T, "net": "0.5"}],
            "transient_residual_unallocated",
            False,
        ),
        (
            [
                {
                    "fact": "sibling_transfers",
                    "transfer_id": T,
                    "same_customer": "no",
                    "same_funding_amount": "yes",
                }
            ],
            "duplicate_customer_instruction",
            True,
        ),
        (
            [
                {
                    "fact": "sibling_transfers",
                    "transfer_id": T,
                    "same_customer": "yes",
                    "same_funding_amount": "yes",
                }
            ],
            "duplicate_customer_instruction",
            False,
        ),
        (
            [
                {
                    "fact": "amount_comparison",
                    "exception": "E1",
                    "recorded_rate": "1.5",
                    "rate_implied_by_actual": "1.50000000",
                }
            ],
            "fx_rate_applied_mismatch",
            True,
        ),
        (
            [
                {
                    "fact": "amount_comparison",
                    "exception": "E1",
                    "recorded_rate": "1.5",
                    "rate_implied_by_actual": "1.6",
                }
            ],
            "fx_rate_applied_mismatch",
            False,
        ),
    ],
)
def test_a_rule_rules_out_only_on_contradiction(
    items: list[dict[str, str]], category: str, contradicted: bool
) -> None:
    assert (category in _out(*items)) is contradicted


def test_missing_facts_rule_nothing_out() -> None:
    assert _out() == {}
    other = {**_legs(1, 1, "USD"), "transfer_id": T0002}
    assert _out(other) == {}  # facts about another transfer say nothing about this one


def test_the_real_group_offers_only_what_its_evidence_allows() -> None:
    residual = next(e for e in BUNDLE.exceptions if e.type.value == "TRANSIENT_RESIDUAL")
    assert "duplicate_posting_same_instruction" in residual.ruled_out
    assert "duplicate_posting_same_instruction" not in residual.allowed_categories


def test_v2_rejects_a_ruled_out_category() -> None:
    residual = exception(BUNDLE, "TRANSIENT_RESIDUAL")
    answer = good_answer(BUNDLE)
    answer["hypotheses"].append(
        hypothesis(4, "duplicate_posting_same_instruction", [residual], supporting=["C4"])
    )
    report = check_analysis(ExceptionAnalysis.model_validate(answer), BUNDLE)
    assert any("not allowed" in e for e in report.hypotheses[3].errors)
    assert report.hypotheses[0].ok


def _check(answer: dict[str, Any]) -> list[str]:
    return check_analysis(ExceptionAnalysis.model_validate(answer), BUNDLE).errors()


def test_support_must_be_about_the_transfers_explained() -> None:
    good = good_answer(BUNDLE)
    assert _check(good) == []
    # Hypothesis 3 explains ...0002's exceptions; C1 is about ...0001 only.
    wrong = edited(good, "hypotheses.2.supporting_claims", ["C1"])
    assert any("C1 cites only transfers" in e for e in _check(wrong))


def test_reference_data_supports_any_transfer() -> None:
    tariff = find(BUNDLE, "tariff_version")
    good = good_answer(BUNDLE)
    good["claims"].append(observation("C10", f"The VAT rate is {{{tariff}.vat_rate}}."))
    answer = edited(good, "hypotheses.2.supporting_claims", ["C6", "C7", "C10"])
    assert _check(answer) == []


def test_unrelated_support_reads_related_transfers() -> None:
    sibling = {"transfer_id": T0002, "related_transfer_id": T0001}
    assert not unrelated_support([sibling], [{"transfer_id": T0001}])
    assert unrelated_support([{"transfer_id": T0002}], [{"transfer_id": T0001}])
    assert not unrelated_support([{"vat_rate": "0.075"}], [{"transfer_id": T0001}])
