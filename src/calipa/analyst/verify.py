"""V2, the claim checker. Code decides which parts of P2's answer can be kept.

P2 writes values only as placeholders ({E23.expected}). An observation cites exactly
the evidence items its placeholders name. An inference builds only on observations
and may use only the placeholders they use; a hypothesis summary only those of its
claims. Text outside placeholders may hold no digits, number words, currency words
or symbols, and only role labels and currency codes found in the items it cites. A
hypothesis must use a category allowed for every exception it explains, may be
"high" only if its supporting claims cite every evidence type the category needs and
nothing contradicts it, and fails if any claim it relies on fails. A failing claim
takes down only the hypotheses that use it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from calipa.analyst.categories import CATEGORIES, NOT_ANALYSED
from calipa.analyst.evidence import EvidenceBundle
from calipa.analyst.schema import (
    MAX_HYPOTHESES,
    Claim,
    EvidenceType,
    ExceptionAnalysis,
    Hypothesis,
    NextStep,
)
from calipa.analyst.support import unrelated_support
from calipa.analyst.tokens import Placeholder, render, scan

_CLAIM_ID = re.compile(r"^C[1-9][0-9]*$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ItemCheck(_Model):
    ref: str  # claim id, "hypothesis N", or "data_limitations[i]"
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


class CheckReport(_Model):
    global_errors: list[str]  # the answer as a whole cannot be used
    claims: list[ItemCheck]
    hypotheses: list[ItemCheck]  # in the order P2 gave them
    limitations: list[ItemCheck]
    coverage_errors: list[str]  # exceptions no passing hypothesis explains

    @property
    def clean(self) -> bool:
        return not self.errors()

    def errors(self) -> list[str]:
        out = list(self.global_errors)
        for group in (self.claims, self.hypotheses, self.limitations):
            out += [f"{c.ref}: {e}" for c in group for e in c.errors]
        return out + self.coverage_errors

    def passing_hypotheses(self) -> list[int]:
        """Indexes into the answer's hypothesis list."""
        if self.global_errors:
            return []
        return [i for i, h in enumerate(self.hypotheses) if h.ok]


class VerifiedAnalysis(_Model):
    """What survives V2: passing hypotheses re-ranked, only the claims they use, and
    each text with its placeholders filled in by code."""

    claims: list[Claim]
    hypotheses: list[Hypothesis]
    next_steps: list[NextStep]
    data_limitations: list[str]
    rendered: dict[str, str]  # "C1" or "hypothesis 1" -> the text with values


def check_analysis(analysis: ExceptionAnalysis, bundle: EvidenceBundle) -> CheckReport:
    claims = {c.claim_id: c for c in analysis.claims}
    global_errors = _global_errors(analysis)

    claim_errors: dict[str, list[str]] = {}
    shows: dict[str, set[Placeholder]] = {}  # the values each claim rests on
    for claim in analysis.claims:
        if claim.kind == "observation":
            errors, shows[claim.claim_id] = _observation(claim, bundle)
        else:
            errors, shows[claim.claim_id] = _inference(claim, claims, bundle)
        claim_errors.setdefault(claim.claim_id, []).extend(errors)
    for claim in analysis.claims:  # an inference falls with what it is based on
        if claim.kind == "inference":
            for basis in claim.based_on:
                if claim_errors.get(basis):
                    claim_errors[claim.claim_id].append(f"relies on {basis}, which failed")

    hypothesis_errors = [
        _hypothesis(h, claims, claim_errors, shows, bundle) for h in analysis.hypotheses
    ]
    # Limitations state what the evidence cannot show: no values, no references.
    limitation_checks = [
        ItemCheck(
            ref=f"data_limitations[{i}]", errors=_text_errors(text, bundle, allowed=set(), refs=())
        )
        for i, text in enumerate(analysis.data_limitations)
    ]
    explained = {
        e
        for h, errors in zip(analysis.hypotheses, hypothesis_errors, strict=True)
        if not errors
        for e in h.explains_exceptions
    }
    coverage = [
        f"exception {e.evidence_id} ({e.type.value}) is not explained by any passing "
        'hypothesis; use "unexplained" if nothing fits'
        for e in bundle.exceptions
        if e.type not in NOT_ANALYSED and e.evidence_id not in explained
    ]
    return CheckReport(
        global_errors=global_errors,
        claims=[
            ItemCheck(ref=c.claim_id, errors=claim_errors[c.claim_id]) for c in analysis.claims
        ],
        hypotheses=[
            ItemCheck(ref=f"hypothesis {h.rank}", errors=e)
            for h, e in zip(analysis.hypotheses, hypothesis_errors, strict=True)
        ],
        limitations=limitation_checks,
        coverage_errors=[] if global_errors else coverage,
    )


def explained_exceptions(analysis: ExceptionAnalysis, report: CheckReport) -> set[str]:
    """Engine exceptions (evidence ids) that some passing hypothesis explains."""
    return {
        e for i in report.passing_hypotheses() for e in analysis.hypotheses[i].explains_exceptions
    }


def surviving(
    analysis: ExceptionAnalysis, report: CheckReport, bundle: EvidenceBundle
) -> VerifiedAnalysis | None:
    keep = report.passing_hypotheses()
    if not keep:
        return None
    hypotheses = [
        analysis.hypotheses[i].model_copy(update={"rank": rank})
        for rank, i in enumerate(keep, start=1)
    ]
    by_id = {c.claim_id: c for c in analysis.claims}
    used: set[str] = set()
    pending = [c for h in hypotheses for c in (*h.supporting_claims, *h.contradicting_claims)]
    while pending:
        claim_id = pending.pop()
        if claim_id not in used:
            used.add(claim_id)
            pending.extend(by_id[claim_id].based_on)
    claims = [c for c in analysis.claims if c.claim_id in used]
    values = {(i.evidence_id, k): v for i in bundle.items for k, v in i.fields.items()}
    rendered = {c.claim_id: render(c.text, values) for c in claims}
    rendered |= {f"hypothesis {h.rank}": render(h.summary, values) for h in hypotheses}
    passing_limits = {c.ref for c in report.limitations if c.ok}
    return VerifiedAnalysis(
        claims=claims,
        hypotheses=hypotheses,
        next_steps=list(dict.fromkeys(analysis.next_steps)),
        data_limitations=[
            text
            for i, text in enumerate(analysis.data_limitations)
            if f"data_limitations[{i}]" in passing_limits
        ],
        rendered=rendered,
    )


# --- the answer as a whole -------------------------------------------------------------


def _global_errors(analysis: ExceptionAnalysis) -> list[str]:
    errors: list[str] = []
    if not 1 <= len(analysis.hypotheses) <= MAX_HYPOTHESES:
        errors.append(f"give 1 to {MAX_HYPOTHESES} hypotheses, not {len(analysis.hypotheses)}")
    ids = [c.claim_id for c in analysis.claims]
    if len(set(ids)) != len(ids):
        errors.append("claim ids must be unique")
    bad = [i for i in ids if not _CLAIM_ID.match(i)]
    if bad:
        errors.append(f"claim ids must look like C1, C2, ...: {bad}")
    ranks = [h.rank for h in analysis.hypotheses]
    if ranks != list(range(1, len(ranks) + 1)):
        errors.append(f"hypothesis ranks must be 1, 2, ... in order, got {ranks}")
    categories = [h.category for h in analysis.hypotheses]
    if len(set(categories)) != len(categories):
        errors.append("each category may appear in only one hypothesis")
    return errors


# --- claims ----------------------------------------------------------------------------


def _observation(claim: Claim, bundle: EvidenceBundle) -> tuple[list[str], set[Placeholder]]:
    errors: list[str] = []
    if claim.based_on:
        errors.append("an observation cites evidence, not other claims (based_on must be empty)")
    placeholders = scan(claim.text).placeholders
    if not placeholders:
        errors.append("an observation must cite evidence with at least one placeholder")
    named = {p.evidence_id for p in placeholders}
    if set(claim.evidence_ids) != named:
        errors.append(
            f"evidence_ids must list exactly the items its placeholders cite: {sorted(named)}"
        )
    errors += _text_errors(claim.text, bundle, allowed=None, refs=named)
    return errors, set(placeholders)


def _inference(
    claim: Claim, claims: dict[str, Claim], bundle: EvidenceBundle
) -> tuple[list[str], set[Placeholder]]:
    errors: list[str] = []
    rests_on: set[Placeholder] = set()
    if claim.evidence_ids:
        errors.append("an inference cites observations in based_on, not evidence")
    if not claim.based_on:
        errors.append("an inference needs at least one observation in based_on")
    for basis in claim.based_on:
        base = claims.get(basis)
        if base is None:
            errors.append(f"based_on {basis}, which does not exist")
        elif base.kind != "observation":
            errors.append(f"based_on {basis}, which is not an observation")
        else:
            rests_on |= set(scan(base.text).placeholders)
    refs = {p.evidence_id for p in rests_on} | set(claim.based_on)
    errors += _text_errors(claim.text, bundle, allowed=rests_on, refs=refs)
    return errors, rests_on


# --- hypotheses ------------------------------------------------------------------------


def _hypothesis(
    h: Hypothesis,
    claims: dict[str, Claim],
    claim_errors: dict[str, list[str]],
    shows: dict[str, set[Placeholder]],
    bundle: EvidenceBundle,
) -> list[str]:
    errors: list[str] = []
    category = CATEGORIES[h.category]
    if not h.supporting_claims and h.category != "unexplained":
        errors.append("needs at least one supporting claim")
    both = set(h.supporting_claims) & set(h.contradicting_claims)
    if both:
        errors.append(f"claims {sorted(both)} cannot both support and contradict")
    for claim_id in (*h.supporting_claims, *h.contradicting_claims):
        if claim_id not in claims:
            errors.append(f"claim {claim_id} does not exist")
        elif claim_errors.get(claim_id):
            errors.append(f"relies on claim {claim_id}, which failed")

    exceptions = {e.evidence_id: e for e in bundle.exceptions}
    if not h.explains_exceptions:
        errors.append("must say which exceptions it explains")
    for evidence_id in h.explains_exceptions:
        exception = exceptions.get(evidence_id)
        if exception is None:
            errors.append(f"{evidence_id} is not an engine_exception item")
        elif h.category not in exception.allowed_categories:
            errors.append(
                f"category {h.category} is not allowed for {evidence_id} "
                f"({exception.type.value}); allowed: {exception.allowed_categories}"
            )

    explained = [i.fields for e in h.explains_exceptions if (i := bundle.item(e)) is not None]
    supported: set[Placeholder] = set()
    for claim_id in h.supporting_claims:
        supported |= shows.get(claim_id, set())
        cited = {p.evidence_id for p in shows.get(claim_id, set())}
        items = [i.fields for e in sorted(cited) if (i := bundle.item(e)) is not None]
        if unrelated_support(items, explained):
            errors.append(f"claim {claim_id} cites only transfers this hypothesis does not explain")
    types: set[EvidenceType] = set()
    for placeholder in supported:
        item = bundle.item(placeholder.evidence_id)
        if item is not None:
            types.add(item.type)
    if h.category == "unexplained" and h.confidence != "low":
        errors.append('"unexplained" can only have low confidence')
    if h.confidence == "high":
        if h.contradicting_claims:
            errors.append("high confidence is not allowed when a claim contradicts it")
        missing = [t for t in category.required if t not in types]
        if missing:
            errors.append(f"high confidence needs supporting claims citing {', '.join(missing)}")
    mentioned = supported.union(*(shows.get(c, set()) for c in h.contradicting_claims))
    refs = (
        {p.evidence_id for p in mentioned}
        | set(h.explains_exceptions)
        | set(h.supporting_claims)
        | set(h.contradicting_claims)
    )
    errors += _text_errors(h.summary, bundle, allowed=mentioned, refs=refs)
    return errors


# --- text ------------------------------------------------------------------------------


def _text_errors(
    text: str,
    bundle: EvidenceBundle,
    *,
    allowed: set[Placeholder] | None,
    refs: Iterable[str],
) -> list[str]:
    """`allowed` None means any placeholder that resolves (an observation choosing what
    it cites); otherwise only those placeholders."""
    found = scan(text)
    errors = list(found.errors)
    for placeholder in found.placeholders:
        item = bundle.item(placeholder.evidence_id)
        if item is None:
            errors.append(f"{placeholder}: evidence {placeholder.evidence_id} does not exist")
        elif placeholder.field not in item.fields:
            errors.append(
                f"{placeholder}: {placeholder.evidence_id} has no field {placeholder.field!r}"
            )
        elif allowed is not None and placeholder not in allowed:
            errors.append(f"{placeholder} is not cited by the claims this text rests on")
    allowed_refs = {r.upper() for r in refs}
    for ref in found.refs:
        if ref not in allowed_refs:
            errors.append(f"mentions {ref}, which this text does not cite")
    # Role labels and currency codes are words, but they are facts too: they must
    # appear in the items this text cites.
    cited = allowed if allowed is not None else set(found.placeholders)
    values = {
        value
        for p in cited
        if (item := bundle.item(p.evidence_id)) is not None
        for value in item.fields.values()
        for part in (value, *value.split(", "))
    }
    for word in (*found.labels, *found.codes):
        if word not in values:
            errors.append(f"{word} is not in the evidence this text cites")
    return errors
