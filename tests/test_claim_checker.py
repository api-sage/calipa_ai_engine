"""V2 against deliberately broken P2 answers. Each test breaks one thing about an
answer that otherwise passes and checks that V2 catches it and drops only what
depends on it. The attacks include every bypass the adversarial review of the first
(quote-based) checker found."""

from __future__ import annotations

from typing import Any

import pytest

from calipa.analyst.schema import ExceptionAnalysis
from calipa.analyst.tokens import scan
from calipa.analyst.verify import CheckReport, check_analysis, surviving
from tests.analyst_helpers import (
    T0001,
    edited,
    exception,
    find,
    good_answer,
    hypothesis,
    inference,
    observation,
    real_group,
    unexplained_answer,
)

BUNDLE = real_group().bundle
GOOD = good_answer(BUNDLE)
RESIDUAL = exception(BUNDLE, "TRANSIENT_RESIDUAL")
SIBLINGS = find(BUNDLE, "derived_fact", fact="sibling_transfers")
TARIFF = find(BUNDLE, "tariff_version")
FUNDING = find(BUNDLE, "gl_entry", transfer_id=T0001, credited="TRANSIENT")
LEGS = find(BUNDLE, "derived_fact", fact="leg_counts", transfer_id=T0001)


def check(answer: dict[str, Any]) -> CheckReport:
    return check_analysis(ExceptionAnalysis.model_validate(answer), BUNDLE)


def claim_errors(answer: dict[str, Any], claim_id: str) -> list[str]:
    return next(c.errors for c in check(answer).claims if c.ref == claim_id)


def with_claim(index: int, text: str) -> dict[str, Any]:
    """GOOD with one observation's text replaced (evidence ids follow the text)."""
    claim_id = GOOD["claims"][index]["claim_id"]
    return edited(GOOD, f"claims.{index}", observation(claim_id, text))


def surviving_categories(answer: dict[str, Any]) -> list[str]:
    analysis = ExceptionAnalysis.model_validate(answer)
    verified = surviving(analysis, check_analysis(analysis, BUNDLE), BUNDLE)
    return [] if verified is None else [h.category for h in verified.hypotheses]


def test_a_sound_answer_passes_whole_and_code_fills_in_the_values() -> None:
    report = check(GOOD)
    assert report.clean, report.errors()
    analysis = ExceptionAnalysis.model_validate(GOOD)
    verified = surviving(analysis, report, BUNDLE)
    assert verified is not None
    assert [h.rank for h in verified.hypotheses] == [1, 2, 3]
    assert verified.rendered["C4"] == f"GBP 2.22 was left in TRANSIENT for transfer {T0001}."
    assert "18 seconds" in verified.rendered["hypothesis 2"]


def test_the_smallest_answer_passes() -> None:
    assert check(unexplained_answer(BUNDLE)).clean


# --- values never appear as text -------------------------------------------------------

R = RESIDUAL


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (f"GBP 2.22 was left for {{{R}.transfer_id}}.", "never as digits"),
        (f"{{{R}.actual_currency}} 2,22 was left for {{{R}.transfer_id}}.", "never as digits"),
        (f"{{{R}.actual_currency}} .99 was left.", "never as digits"),
        (f"Version 9.9.9 left {{{R}.actual_amount}}.", "never as digits"),
        (f"{{{R}.actual_currency}} 1_000 was left.", "never as digits"),
        (f"\u22122.22 was left, not {{{R}.actual_amount}}.", "never as digits"),  # Unicode minus
        (f"\u0662 legs and {{{R}.actual_amount}} left.", "never as digits"),  # Arabic-Indic
        (f"\uff12 legs and {{{R}.actual_amount}} left.", "never as digits"),  # full width
        (f"XLVII pence of {{{R}.actual_amount}}.", "roman numeral"),
        (f"Eighteen legs, {{{R}.actual_amount}} left.", "as a word"),
        (f"{{{R}.actual_amount}} bn was left.", "as a word"),
        (f"{{{R}.actual_amount}} was posted twice.", "as a word"),
        (f"{{{R}.actual_amount}} dollars were left.", "currency written as a word"),
        (f"£{{{R}.actual_amount}} was left.", "symbol"),
        (f"JPY {{{R}.actual_amount}} was left.", "does not handle"),
        (f"USD {{{R}.actual_amount}} was left.", "USD is not in the evidence"),
        (f"{{{R}.actual_amount}} was left by CUSTOMER_7.", "CUSTOMER_7 is not in"),
        (f"{{{R}.actual_amount}} was left, unlike E29.", "mentions E29"),
        (f"{{{R}.amount}} was left.", "has no field 'amount'"),
        (f"{{E99.amount}} was left, see {{{R}.actual_amount}}.", "E99 does not exist"),
        ("Money was left in TRANSIENT.", "at least one placeholder"),
    ],
)
def test_text_that_states_values_itself_is_rejected(text: str, error: str) -> None:
    errors = claim_errors(with_claim(3, text), "C4")
    assert any(error in e for e in errors), errors


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (f"The repeat came {{{SIBLINGS}.seconds_apart}} minutes later.", "'minutes'"),
        (f"The repeat came {{{SIBLINGS}.seconds_apart}} hours later.", "'hours'"),
        (f"VAT is {{{TARIFF}.vat_rate}}% of the principal.", "symbol"),
    ],
)
def test_units_and_percent_cannot_change_a_value(text: str, error: str) -> None:
    errors = claim_errors(with_claim(4, text), "C5")
    assert any(error in e for e in errors), errors


def test_labels_and_codes_must_be_in_the_cited_items() -> None:
    # CUSTOMER_1 and GBP appear in the funding entry C9 cites...
    assert check(GOOD).clean
    # ...but not in the leg counts item.
    errors = claim_errors(with_claim(0, f"CUSTOMER_1 had {{{LEGS}.fee_legs}} GBP fee legs."), "C1")
    assert any("CUSTOMER_1 is not in" in e for e in errors)
    assert any("GBP is not in" in e for e in errors)


def test_evidence_ids_must_match_what_the_text_cites() -> None:
    # Listing an item without citing it used to count toward "high" confidence.
    answer = edited(GOOD, "claims.0.evidence_ids", [LEGS, FUNDING])
    assert any("exactly the items" in e for e in claim_errors(answer, "C1"))


def test_high_confidence_counts_only_cited_evidence() -> None:
    # Without C9 no supporting claim cites a gl_entry.
    answer = edited(GOOD, "hypotheses.0.supporting_claims", ["C1", "C2", "C3", "C8"])
    assert any("citing gl_entry" in e for e in check(answer).hypotheses[0].errors)


# --- inferences ------------------------------------------------------------------------


def test_an_inference_uses_only_its_observations_values() -> None:
    answer = edited(GOOD, "claims.7.text", f"{{{FUNDING}.amount}} stayed in TRANSIENT.")
    assert any("not cited by the claims" in e for e in claim_errors(answer, "C8"))
    answer = edited(GOOD, "claims.7.text", "GBP 3.00 stayed in TRANSIENT.")
    assert any("never as digits" in e for e in claim_errors(answer, "C8"))


def test_an_inference_builds_only_on_observations() -> None:
    answer = edited(GOOD, "claims", [*GOOD["claims"], inference("C10", "So it was missed.", "C8")])
    assert any("not an observation" in e for e in claim_errors(answer, "C10"))
    answer = edited(GOOD, "claims.7.evidence_ids", [RESIDUAL])
    assert any("not evidence" in e for e in claim_errors(answer, "C8"))


def test_an_inference_falls_with_its_observation() -> None:
    answer = with_claim(0, f"Transfer {{{LEGS}.transfer_id}} has {{{LEGS}.fees}} fee legs.")
    assert any("relies on C1" in e for e in claim_errors(answer, "C8"))


# --- hypotheses ------------------------------------------------------------------------


def test_a_failing_claim_drops_only_the_hypotheses_that_use_it() -> None:
    broken = with_claim(0, f"Transfer {{{LEGS}.transfer_id}} has 2 fee legs.")
    assert surviving_categories(broken) == [
        "duplicate_customer_instruction",
        "fee_or_vat_leg_duplicated_across_ccy",
    ]
    report = check(broken)
    assert any("relies on claim C1" in e for e in report.hypotheses[0].errors)
    assert any("FEE_LEG_MISSING" in e for e in report.coverage_errors)


@pytest.mark.parametrize(
    ("index", "changes", "error"),
    [
        (1, {"explains_exceptions": ["E1"]}, "not allowed for E1"),
        (1, {"category": "missing_reversal"}, "not allowed"),
        (1, {"explains_exceptions": ["E14"]}, "not an engine_exception"),
        (1, {"explains_exceptions": []}, "which exceptions"),
        (1, {"confidence": "high"}, "citing gl_entry"),
        (0, {"contradicting_claims": ["C6"]}, "a claim contradicts it"),
        (1, {"supporting_claims": ["C5", "C42"]}, "C42 does not exist"),
        (1, {"supporting_claims": []}, "at least one supporting claim"),
        (1, {"contradicting_claims": ["C5"]}, "both support and contradict"),
        # The summary may use only its claims' placeholders, and no values of its own.
        (1, {"summary": f"Funded with {{{FUNDING}.amount}}."}, "not cited by the claims"),
        (1, {"summary": "A repeat sent 25 seconds later."}, "never as digits"),
        (1, {"summary": "A repeat sent twice."}, "as a word"),
    ],
)
def test_broken_hypotheses_are_caught(index: int, changes: dict[str, Any], error: str) -> None:
    answer = GOOD
    for key, value in changes.items():
        answer = edited(answer, f"hypotheses.{index}.{key}", value)
    errors = check(answer).hypotheses[index].errors
    assert any(error in e for e in errors), errors


def test_unexplained_is_always_low() -> None:
    answer = edited(unexplained_answer(BUNDLE), "hypotheses.0.confidence", "medium")
    assert any("only have low" in e for e in check(answer).hypotheses[0].errors)


def test_every_exception_must_be_explained() -> None:
    answer = edited(GOOD, "hypotheses", GOOD["hypotheses"][:2])
    assert len(check(answer).coverage_errors) == 2
    assert surviving_categories(answer) == [
        "fee_or_vat_leg_missing",
        "duplicate_customer_instruction",
    ]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda a: edited(a, "hypotheses.1.rank", 3), "ranks must be 1, 2"),
        (lambda a: edited(a, "hypotheses", []), "give 1 to 5"),
        (lambda a: edited(a, "claims.1.claim_id", "C1"), "unique"),
        (
            lambda a: edited(a, "hypotheses.2.category", "duplicate_customer_instruction"),
            "only one hypothesis",
        ),
    ],
)
def test_structural_errors_reject_the_whole_answer(change: Any, error: str) -> None:
    answer = change(GOOD)
    report = check(answer)
    assert any(error in e for e in report.global_errors), report.global_errors
    assert report.passing_hypotheses() == []
    assert surviving_categories(answer) == []


def test_data_limitations_carry_no_values_or_references() -> None:
    answer = edited(
        GOOD,
        "data_limitations",
        [
            f"{{{SIBLINGS}.seconds_apart}} seconds is short.",
            "C5 shows a repeat.",
            "About 3 legs may be missing.",
            "Logs are missing.",
        ],
    )
    report = check(answer)
    assert [c.ok for c in report.limitations] == [False, False, False, True]
    verified = surviving(ExceptionAnalysis.model_validate(answer), report, BUNDLE)
    assert verified is not None and verified.data_limitations == ["Logs are missing."]


def test_hypothesis_builder_defaults_are_complete() -> None:
    assert set(hypothesis(1, "unexplained", [])) == set(GOOD["hypotheses"][0])


def test_scan() -> None:
    found = scan("{E3.amount} moved from CUSTOMER_1 (E3, C2) in gbp")
    assert [str(p) for p in found.placeholders] == ["{E3.amount}"]
    assert (found.labels, found.refs, found.codes, found.errors) == (
        ["CUSTOMER_1"],
        ["E3", "C2"],
        ["GBP"],
        [],
    )
