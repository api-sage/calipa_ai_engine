"""V3 against deliberately broken P3 answers. Each test breaks one thing about an
answer that otherwise passes and checks that V3 catches it, and that the section is
replaced by template text while the rest is kept."""

from __future__ import annotations

from typing import Any

import pytest

from calipa.reporter.facts import user_content
from calipa.reporter.render import assemble, md
from calipa.reporter.schema import ReportNarrative
from calipa.reporter.verify import ReportCheck, check_report
from tests.analyst_helpers import edited
from tests.report_helpers import (
    G1_TEXT,
    G2_TEXT,
    facts_for,
    finding_text,
    good_narrative,
    report_inputs,
    siblings_key,
)

INPUTS = report_inputs()
FACTS = facts_for(INPUTS)
FINDINGS = dict(INPUTS.keyed())
GOOD = good_narrative()


def check(answer: dict[str, Any]) -> ReportCheck:
    return check_report(ReportNarrative.model_validate(answer), FACTS, FINDINGS)


def with_g1(text: str) -> dict[str, Any]:
    return edited(GOOD, "findings.0", finding_text("G1", text))


def g1_errors(text: str) -> list[str]:
    return check(with_g1(text)).findings[0].errors


def test_a_sound_answer_passes_and_code_fills_in_the_values() -> None:
    report = check(GOOD)
    assert report.clean, report.errors()
    final = assemble(INPUTS, FACTS, ReportNarrative.model_validate(GOOD), report)
    assert final.template_sections() == []
    g1 = final.findings[0].text
    assert "a charge or VAT leg that was not posted (high confidence)" in g1
    assert "followed the first by 18 seconds" in g1
    assert "{{fact:" not in g1
    assert final.findings[1].text.startswith("The engine found ")
    assert "not analysed because the run's limit" in final.findings[1].text


def test_p3_never_sees_a_value() -> None:
    content = user_content(INPUTS, FACTS)
    for fact in FACTS.values():
        if any(c.isdigit() for c in fact.value) and len(fact.value) > 3:
            assert fact.value not in content, fact
    assert "5648968500" not in content
    assert "{{fact:G1.E" in content  # P2's claims arrive as fact placeholders


# --- values and words that must be placeholders ----------------------------------------

BASE = "The engine found {{fact:G1.exceptions}}. "
TAIL = G1_TEXT.split("{{fact:G1.h1}}. ", 1)[1] % siblings_key()
H1 = "The evidence is consistent with {{fact:G1.h1}}. "


@pytest.mark.parametrize(
    ("insert", "error"),
    [
        ("It involved 3 legs.", "never as digits"),
        ("It involved ٣ legs.", "never as digits"),
        ("It involved two transfers.", "as a word"),
        ("It was posted twice.", "as a word"),
        ("The residual was in pounds.", "currency written as a word"),
        ("The residual was £ only.", "symbol"),
        ("The residual was GBP only.", "a currency code"),
        ("It was paid by CUSTOMER_1.", "an account label"),
        ("See E3 and G2.", "an evidence, claim or finding id"),
        ("This is a high severity finding.", "come only from the facts"),
        ("Confidence is low.", "come only from the facts"),
        ("The amount was {E3.expected_amount}.", "no markup"),
        ("It is **important**.", "no markup"),
        ("See <a href=x>this</a>.", "no markup"),
        ("See the [ledger](https://evil.example).", "no markup"),
        ("The fee code fee_or_vat_leg_missing applies.", "no markup"),
        ("The control is not effective.", "control effectiveness"),
        ("There is no material misstatement.", "materiality"),
        ("The bank is compliant.", "compliance"),
        ("This may indicate fraud.", "fraud"),
        ("The posting looks deliberate.", "intent or misconduct"),
        ("In our opinion the ledger is fine.", "an audit opinion"),
        ("The root cause is a switch timeout.", "certainty about a cause"),
        ("This was clearly a duplicate.", "certainty about a cause"),
        ("This proves the legs were missed.", "certainty about a cause"),
        ("It may also be a rounding difference.", "no verified hypothesis here has"),
    ],
)
def test_text_that_states_values_or_opinions_is_rejected(insert: str, error: str) -> None:
    errors = g1_errors(BASE + H1 + insert + " " + TAIL)
    assert any(error in e for e in errors), errors


RANKED = "come only from the facts"
H3 = "The evidence is also consistent with {{fact:G1.h3}}."


@pytest.mark.parametrize(
    ("before", "after", "passes"),
    [
        (H1, "", True),
        (H1 + "It has highest severity {{fact:G1.severity}}. ", "", True),
        (H1 + "Its highest severity is {{fact:G1.severity}}. ", "", True),
        (H1 + "It is the highest severity here. ", "", False),
        (H1 + "Its severity is highest. ", "", False),
        (
            H1,
            H3.replace("The evidence is also consistent with", "A third possible explanation is"),
            True,
        ),
        (H1, H3.replace("}}.", "}} as the third hypothesis."), True),
        (
            H1,
            H3.replace("The evidence is also consistent with", "A fourth possible explanation is"),
            False,
        ),
        (H1, H3.replace("}}.", "}} as the second hypothesis."), False),
    ],
)
def test_words_that_name_the_fact_beside_them_pass_only_there(
    before: str, after: str, passes: bool
) -> None:
    text = BASE + before + TAIL
    if after:
        text = text.replace(H3, after)
        assert after in text
    errors = g1_errors(text)
    assert (errors == []) is passes, errors


def test_minor_units_pass_only_after_a_difference_in_minor_units() -> None:
    named = BASE + H1 + "The gap is {{fact:G1.E1.difference_in_minor_units}} minor units. " + TAIL
    assert not any(RANKED in e for e in g1_errors(named))
    loose = BASE + H1 + "The gap is minor. " + TAIL
    assert any(RANKED in e for e in g1_errors(loose))


def test_unknown_and_foreign_facts_are_rejected() -> None:
    errors = g1_errors(BASE + H1 + "It is {{fact:G1.nothing}}. " + TAIL)
    assert any("there is no fact 'G1.nothing'" in e for e in errors)
    errors = g1_errors(BASE + H1 + "It is like {{fact:G2.exceptions}}. " + TAIL)
    assert any("belongs to another finding" in e for e in errors)


def test_a_unit_must_be_the_facts_own() -> None:
    errors = g1_errors(BASE + H1 + TAIL.replace("}} seconds", "}} minutes"))
    assert any("'minutes'" in e for e in errors), errors


def test_fact_keys_used_must_match_the_text() -> None:
    answer = edited(GOOD, "findings.0.fact_keys_used", ["G1.exceptions"])
    assert any("exactly the facts it cites" in e for e in check(answer).findings[0].errors)


# --- coverage --------------------------------------------------------------------------


def test_every_hypothesis_must_be_presented_in_rank_order() -> None:
    skipped = BASE + H1 + "Nothing else fits."
    errors = g1_errors(skipped)
    assert any("hypothesis 2 exactly once" in e for e in errors)
    assert any("hypothesis 3 exactly once" in e for e in errors)

    swapped = (G1_TEXT % siblings_key()).replace("G1.h1}", "G1.hX}")
    swapped = swapped.replace("G1.h2}", "G1.h1}").replace("G1.hX}", "G1.h2}")
    assert any("rank order" in e for e in g1_errors(swapped))


def test_a_narrative_must_say_what_the_engine_found() -> None:
    errors = g1_errors((G1_TEXT % siblings_key()).replace("{{fact:G1.exceptions}}", "problems"))
    assert any("G1.exceptions" in e for e in errors)


def test_an_unanalysed_finding_must_say_so() -> None:
    answer = edited(
        GOOD,
        "findings.1",
        finding_text("G2", "The engine found {{fact:G2.exceptions}}."),
    )
    assert any("G2.status" in e for e in check(answer).findings[1].errors)


def test_each_finding_once_and_only_real_findings() -> None:
    report = check(edited(GOOD, "findings", GOOD["findings"][:1]))
    assert report.missing == ["G2"]

    twice = edited(GOOD, "findings", [*GOOD["findings"], GOOD["findings"][0]])
    report = check(twice)
    assert [c.ok for c in report.findings] == [False, True, False]

    extra = edited(GOOD, "findings", [*GOOD["findings"], finding_text("G9", G2_TEXT)])
    assert "G9 is not a finding key" in check(extra).findings[2].errors


# --- patterns and the summary ----------------------------------------------------------


def test_patterns_link_real_findings_and_use_only_their_facts() -> None:
    good: dict[str, Any] = {
        "text": "Both {{fact:G1.exceptions}} and {{fact:G2.exceptions}} concern the same day.",
        "finding_keys": ["G1", "G2"],
    }
    assert check(edited(GOOD, "cross_finding_patterns", [good])).clean
    for broken, error in (
        ({**good, "finding_keys": ["G1"]}, "two or more"),
        ({**good, "finding_keys": ["G1", "G7"]}, "not finding keys"),
        ({**good, "text": good["text"] + " See E3."}, "id"),
    ):
        report = check(edited(GOOD, "cross_finding_patterns", [broken]))
        assert any(error in e for e in report.patterns[0].errors), report.patterns[0].errors


def test_the_summary_follows_the_same_rules() -> None:
    for text, error in (
        ("", "empty"),
        ("The ledger is fairly stated.", "an audit opinion"),
        ("Five exceptions were raised.", "as a word"),
    ):
        report = check(edited(GOOD, "executive_summary", text))
        assert any(error in e for e in report.summary.errors), report.summary.errors


# --- what replaces a failing section ---------------------------------------------------


def test_a_failing_section_is_replaced_by_template_text_and_the_rest_kept() -> None:
    answer = with_g1(BASE + H1 + "It involved 3 legs. " + TAIL)
    report = check(answer)
    final = assemble(INPUTS, FACTS, ReportNarrative.model_validate(answer), report)
    assert final.template_sections() == ["G1"]
    g1 = final.findings[0]
    assert g1.source == "template" and g1.written is None and g1.errors
    assert "a charge or VAT leg that was not posted (high confidence)" in g1.text
    assert final.findings[1].source == "ai" and final.executive_summary.source == "ai"


def test_with_no_answer_everything_is_template_text() -> None:
    final = assemble(INPUTS, FACTS, None, None, why_template="P3 gave no usable answer")
    assert final.template_sections() == ["executive_summary", "G1", "G2"]
    assert "There is no AI analysis for this finding" in final.findings[1].text


def test_markdown_escaping_keeps_data_as_text() -> None:
    hostile = "ok | <script>x</script> [click](http://a) **b** # h\nnew line &percnt; a@b.c"
    escaped = md(hostile)
    assert "\n" not in escaped
    for raw in ("|", "<", ">", "[", "]", "*", "#", "(", ")", "&", "@"):
        assert raw not in escaped.replace("\\" + raw, "")
    assert md("- item") == "\\- item" and md("2. item") == "2\\. item"
    assert md("see www.evil.example") == "see www\\.evil.example"
    assert md("go HTTPS://x or javascript:y") == "go HTTPS\\://x or javascript\\:y"
    assert md("2026-10-25T10:00:00Z") == "2026-10-25T10:00:00Z"
    assert md("a\u202eb\u200bc") == "abc"


# --- bypasses found by the adversarial review of V3 -------------------------------------

AMOUNT = "{{fact:G1.E4.actual_amount}}"
CCY = "{{fact:G1.E4.actual_currency}}"


@pytest.mark.parametrize(
    ("insert", "error"),
    [
        # Text touching a placeholder changes its value.
        (f"The residual was {AMOUNT}{{{{fact:run.groups}}}}.", "run together"),
        (f"The residual was {AMOUNT} {{{{fact:run.groups}}}}.", "run together"),
        (f"The residual was {AMOUNT}k.", "must not be joined"),
        (f"The residual was -{AMOUNT}.", "must not be joined"),
        (f"The residual was minus {AMOUNT}.", "as a word"),
        (f"The residual was {AMOUNT}.5 in total.", "must not be joined"),
        # A currency only next to its own amount.
        (f"The residual was {{{{fact:G1.E1.expected_amount}}}} {CCY}.", "run together"),
        (f"It was in {CCY} only.", "next to the amount it belongs to"),
        # Hypotheses restated, reranked or ruled out in words.
        ("The evidence rules out the first explanation.", "likelihood"),
        ("The last explanation is the most likely.", "likelihood"),
        ("This is strongly supported.", "likelihood"),
        # Root causes no verified hypothesis has, in other words.
        ("It may also be due to rounding.", "root cause no verified"),
        ("A switch timeout may have left legs unposted.", "root cause no verified"),
        ("A missing reversal could explain it.", "root cause no verified"),
        ("It may be a cut-off difference.", "root cause no verified"),
        ("It may be a manual adjustment.", "root cause no verified"),
        # Opinions and certainty outside the first ban list.
        ("The controls failed.", "control effectiveness"),
        ("This is a significant deficiency.", "control effectiveness"),
        ("The ledger is accurate and reliable.", "control effectiveness"),
        ("This is a regulatory breach.", "compliance"),
        ("The customer was defrauded.", "fraud"),
        ("This may be money laundering.", "fraud"),
        ("The legs were posted knowingly.", "intent or misconduct"),
        ("We are satisfied with the ledger.", "an audit opinion"),
        ("The legs must have been missed.", "certainty about a cause"),
        ("The evidence demonstrates a missed leg.", "certainty about a cause"),
        # Values as words, other digits, symbols, units.
        ("Millions were moved.", "as a word"),
        ("It was posted on the fifth of October.", "as a word"),
        ("It happened once.", "as a word"),
        ("It ran at midnight.", "date or time"),
        ("Superscript \u00b2 appears here.", "never as digits"),
        ("Half \u00bd was left.", "never as digits"),
        ("Circled \u2460 was left.", "never as digits"),
        ("Numeral \u2162 was left.", "never as digits"),
        ("The fee was \u20bd only.", "currency symbol"),
        ("The fee was \uff04 only.", "currency symbol"),
        ("The fee was a few bucks.", "currency written as a word"),
        ("The rate is a few basis points off.", "as a word"),
        # Markup the first list missed.
        ("Write to audit@evil.example now.", "no markup"),
        ("See HTTPS:evil.example now.", "no markup"),
        ("See WWW.evil.example now.", "no markup"),
        ("Run javascript:alert now.", "no markup"),
        ("It is 0.075&percnt; of it.", "no markup"),
        # Invisible and lookalike characters.
        (f"The residual was \u202e{AMOUNT}\u202c in total.", "invisible"),
        ("This may be fra\u200bud.", "invisible"),
        ("This may be fr\u0430ud.", "Latin script"),
        ("This may be \uff46\uff52\uff41\uff55\uff44.", "fraud"),
    ],
)
def test_review_bypasses_are_rejected(insert: str, error: str) -> None:
    errors = g1_errors(BASE + H1 + insert + " " + TAIL)
    assert any(error in e for e in errors), errors


def test_a_currency_may_stand_next_to_its_own_amount() -> None:
    assert g1_errors(BASE + H1 + f"The residual was {CCY} {AMOUNT}. " + TAIL) == []


def test_a_hypothesis_appears_exactly_once() -> None:
    twice = BASE + H1 + "Of these, {{fact:G1.h3}} stands out. " + TAIL
    assert any("hypothesis 3 exactly once" in e for e in g1_errors(twice))


@pytest.mark.parametrize(
    "insert",
    [
        "One of the transfers had no charge leg.",
        "No one hypothesis explains every exception.",
        "The second transfer followed the first.",
        "Switch logs or settlement records would help settle this.",
    ],
)
def test_ordinary_hedged_prose_passes(insert: str) -> None:
    assert g1_errors(BASE + H1 + insert + " " + TAIL) == []
