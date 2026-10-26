"""V1: code checks on P1's output, after the schema has been enforced.

Each error is a sentence P1 can act on when it is sent back for its one retry. Nothing
here decides permissions or dates; that is the policy step.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from calipa.interpreter.schema import InterpretedScope
from calipa.reference import ControlCatalog

_SPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _SPACE.sub(" ", text).strip().casefold()


def check_interpretation(
    scope: InterpretedScope, *, controls: ControlCatalog, auditor_text: Iterable[str]
) -> list[str]:
    """`auditor_text` is everything the people on the run wrote: the request, their
    answers and any reviewer note. Quoted values must come from it."""
    said = _norm("\n".join(auditor_text))
    errors: list[str] = []

    match scope.status:
        case "ok":
            if scope.objective is None:
                errors.append('status "ok" needs an objective')
            if scope.period is None:
                errors.append('status "ok" needs a period')
            if scope.ambiguities:
                errors.append('status "ok" must not list ambiguities; ask instead')
            if scope.clarifying_question:
                errors.append('status "ok" must not have a clarifying_question')
            if not scope.account_ids and not scope.all_entitled_accounts:
                errors.append('status "ok" needs account_ids or all_entitled_accounts')
        case "needs_clarification":
            if not (scope.clarifying_question or "").strip():
                errors.append('status "needs_clarification" needs a clarifying_question')
        case "out_of_scope" | "unsupported":
            if not (scope.status_reason or "").strip():
                errors.append(f'status "{scope.status}" needs a status_reason')

    if scope.all_entitled_accounts and scope.account_ids:
        errors.append("account_ids must be empty when all_entitled_accounts is true")
    for name, values in (
        ("account_ids", scope.account_ids),
        ("currencies", scope.currencies),
        ("transaction_references", scope.transaction_references),
        ("control_ids", scope.control_ids),
    ):
        if len(set(values)) != len(values):
            errors.append(f"{name} contains duplicates")

    unknown = [c for c in scope.control_ids if c not in controls.controls]
    if unknown:
        errors.append(f"control_ids not in <control_catalog>: {', '.join(unknown)}")
    elif scope.objective is not None:
        allowed = set(controls.objectives[scope.objective])
        outside = [c for c in scope.control_ids if c not in allowed]
        if outside:
            errors.append(
                f"control_ids {', '.join(outside)} are not part of objective "
                f"{scope.objective.value}"
            )

    period = scope.period
    if period is not None:
        if period.kind == "absolute":
            if period.start is None:
                errors.append("an absolute period needs start")
            if any(
                v is not None
                for v in (period.relative_unit, period.relative_offset, period.relative_count)
            ):
                errors.append("an absolute period must leave the relative_* fields null")
        else:
            if period.relative_unit is None or period.relative_offset is None:
                errors.append("a relative period needs relative_unit and relative_offset")
            if period.start is not None or period.end is not None:
                errors.append("a relative period must leave start and end null")
            if period.relative_count is not None and period.relative_count < 1:
                errors.append("relative_count must be at least 1")
        if not period.source_text.strip() or _norm(period.source_text) not in said:
            errors.append("period.source_text must be the auditor's words, copied exactly")

    for ref in scope.transaction_references:
        if not ref.strip() or _norm(ref) not in said:
            errors.append(f"transaction reference {ref!r} does not appear in the conversation")
    return errors
