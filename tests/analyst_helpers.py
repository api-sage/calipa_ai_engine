"""The real transfers ...0001 and ...0002 as one exception group, and a P2 answer for
it that passes V2. Tests break that answer one way at a time."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from calipa.analyst.evidence import EvidenceBundle, ExceptionGroup, build_groups
from calipa.analyst.tokens import scan
from calipa.engine.contract import ReconciliationRequest, ReconciliationResult
from calipa.engine.standin import StandInEngine
from calipa.reference import load_account_catalog, load_tariff
from tests.engine_helpers import build_request
from tests.fake_fcy import real_gl_entries

T0001 = "ca650882-cc3c-462c-ad71-81ae5182e945"
T0002 = "828972ad-6b3d-4f16-b230-4ef2f4875b33"


def reconcile(
    raw: list[dict[str, Any]] | None = None, **kw: Any
) -> tuple[ReconciliationRequest, ReconciliationResult]:
    request = build_request(real_gl_entries() if raw is None else raw, **kw)
    return request, StandInEngine(load_tariff()).reconcile(request)


def groups(raw: list[dict[str, Any]] | None = None, **kw: Any) -> list[ExceptionGroup]:
    request, result = reconcile(raw, **kw)
    return build_groups(
        result.exceptions, request, tariff=load_tariff(), catalog=load_account_catalog()
    )


def real_group() -> ExceptionGroup:
    [group] = groups()
    return group


def find(bundle: EvidenceBundle, kind: str, **fields: str) -> str:
    """The id of the one item of this type whose fields include these values."""
    matches = [
        i.evidence_id
        for i in bundle.items
        if i.type == kind and all(i.fields.get(k) == v for k, v in fields.items())
    ]
    assert len(matches) == 1, (kind, fields, matches)
    return matches[0]


def exception(bundle: EvidenceBundle, type_: str) -> str:
    return next(e.evidence_id for e in bundle.exceptions if e.type.value == type_)


def observation(claim_id: str, text: str) -> dict[str, Any]:
    """An observation citing exactly the items its placeholders name."""
    return {
        "claim_id": claim_id,
        "kind": "observation",
        "text": text,
        "evidence_ids": list(dict.fromkeys(p.evidence_id for p in scan(text).placeholders)),
        "based_on": [],
    }


def inference(claim_id: str, text: str, *based_on: str) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "kind": "inference",
        "text": text,
        "evidence_ids": [],
        "based_on": list(based_on),
    }


def hypothesis(rank: int, category: str, explains: list[str], **kw: Any) -> dict[str, Any]:
    return {
        "rank": rank,
        "category": category,
        "explains_exceptions": explains,
        "summary": kw.pop("summary", "See the supporting claims."),
        "supporting_claims": kw.pop("supporting", []),
        "contradicting_claims": kw.pop("contradicting", []),
        "confidence": kw.pop("confidence", "medium"),
        "would_confirm": kw.pop("would_confirm", []),
        "would_refute": kw.pop("would_refute", []),
        **kw,
    }


def good_answer(bundle: EvidenceBundle) -> dict[str, Any]:
    """A sound analysis of the real group: fees missing on ...0001, ...0002 a repeat of
    it, and ...0002's charges posted in both currencies."""
    legs1 = find(bundle, "derived_fact", fact="leg_counts", transfer_id=T0001)
    legs2 = find(bundle, "derived_fact", fact="leg_counts", transfer_id=T0002)
    fee = find(
        bundle,
        "derived_fact",
        fact="amount_comparison",
        exception=exception(bundle, "FEE_LEG_MISSING"),
    )
    vat = find(
        bundle,
        "derived_fact",
        fact="amount_comparison",
        exception=exception(bundle, "VAT_LEG_MISSING"),
    )
    residual = exception(bundle, "TRANSIENT_RESIDUAL")
    siblings = find(bundle, "derived_fact", fact="sibling_transfers")
    tariff = find(bundle, "tariff_version")
    funding = find(bundle, "gl_entry", transfer_id=T0001, credited="TRANSIENT")
    usd_fee = find(bundle, "gl_entry", credited="FEE_INCOME", currency="USD")
    usd_vat = find(bundle, "gl_entry", credited="VAT", currency="USD")
    claims = [
        observation(
            "C1",
            f"Transfer {{{legs1}.transfer_id}} has {{{legs1}.fee_legs}} fee legs and "
            f"{{{legs1}.vat_legs}} VAT legs.",
        ),
        observation(
            "C2",
            f"The tariff is a flat charge of {{{tariff}.flat_charge_currency}} "
            f"{{{tariff}.flat_charge_amount}} plus VAT at {{{tariff}.vat_rate}} of the "
            "principal.",
        ),
        observation(
            "C3",
            f"The engine expected a {{{fee}.currency}} {{{fee}.expected}} charge leg and a "
            f"{{{vat}.currency}} {{{vat}.expected}} VAT leg.",
        ),
        observation(
            "C4",
            f"{{{residual}.actual_currency}} {{{residual}.actual_amount}} was left in "
            f"TRANSIENT for transfer {{{residual}.transfer_id}}.",
        ),
        observation(
            "C5",
            f"Transfer {{{siblings}.transfer_id}} repeats {{{siblings}.related_transfer_id}} "
            f"{{{siblings}.seconds_apart}} seconds later; same customer: "
            f"{{{siblings}.same_customer}}.",
        ),
        observation(
            "C6",
            f"Transfer {{{legs2}.transfer_id}} has fee and VAT legs in "
            f"{{{legs2}.fee_and_vat_leg_currencies}}.",
        ),
        observation(
            "C7",
            f"The USD legs {usd_fee} and {usd_vat} are marked {{{usd_fee}.entry_type}} and "
            f"{{{usd_vat}.entry_type}} although {{{usd_fee}.debited}} is debited.",
        ),
        inference(
            "C8",
            f"The charge and VAT for {{{residual}.transfer_id}} were never posted, leaving "
            f"{{{residual}.actual_currency}} {{{residual}.actual_amount}} in TRANSIENT.",
            "C1",
            "C3",
            "C4",
        ),
        observation(
            "C9",
            f"Transfer {{{funding}.transfer_id}} was funded with {{{funding}.currency}} "
            f"{{{funding}.amount}} by CUSTOMER_1.",
        ),
    ]
    return {
        "claims": claims,
        "hypotheses": [
            hypothesis(
                1,
                "fee_or_vat_leg_missing",
                [
                    exception(bundle, "FEE_LEG_MISSING"),
                    exception(bundle, "VAT_LEG_MISSING"),
                    residual,
                ],
                summary=f"The charge and VAT legs of {{{residual}.transfer_id}} were not "
                f"posted, so {{{residual}.actual_currency}} {{{residual}.actual_amount}} "
                "stayed in TRANSIENT.",
                supporting=["C1", "C2", "C3", "C8", "C9"],
                confidence="high",
                would_confirm=["switch_log"],
            ),
            hypothesis(
                2,
                "duplicate_customer_instruction",
                [exception(bundle, "DUPLICATE_INSTRUCTION_CANDIDATE")],
                summary=f"Transfer {{{siblings}.transfer_id}} looks like a repeat sent "
                f"{{{siblings}.seconds_apart}} seconds after the first.",
                supporting=["C5"],
                would_confirm=["ops_confirmation"],
            ),
            hypothesis(
                3,
                "fee_or_vat_leg_duplicated_across_ccy",
                [
                    exception(bundle, "FEE_VAT_IN_BOTH_CURRENCIES"),
                    exception(bundle, "ENTRY_DIRECTION_INCONSISTENT"),
                ],
                supporting=["C6", "C7"],
                would_confirm=["transaction_status"],
            ),
        ],
        "next_steps": [
            "check_customer_instruction_with_ops",
            "confirm_entry_semantics_with_core_banking",
        ],
        "data_limitations": ["Switch logs are not available, so a timeout cannot be confirmed."],
    }


def unexplained_answer(bundle: EvidenceBundle) -> dict[str, Any]:
    """The smallest answer V2 accepts for any group."""
    return {
        "claims": [],
        "hypotheses": [
            hypothesis(
                1,
                "unexplained",
                [e.evidence_id for e in bundle.exceptions],
                confidence="low",
            )
        ],
        "next_steps": ["escalate_to_auditor"],
        "data_limitations": [],
    }


def edited(answer: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """A copy with one value replaced; path like "claims.0.text"."""
    out = deepcopy(answer)
    *parents, last = path.split(".")
    node: Any = out
    for part in parents:
        node = node[int(part)] if part.isdigit() else node[part]
    node[int(last) if last.isdigit() else last] = value
    return out
