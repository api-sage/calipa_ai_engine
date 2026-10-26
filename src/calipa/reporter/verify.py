"""V3, the report checker. Code decides which of P3's sections can be used.

Every value is a {{fact:KEY}} placeholder for a fact that exists and that the section
may use: run facts anywhere, a finding's facts only in its own narrative or in a
pattern that names it. Nothing may touch a placeholder and change its value: no
letter, sign or point joined to it and no two placeholders run together, except a
currency next to its own amount, the only place a currency may stand.

Text outside placeholders holds no digits of any script, number, ordinal or calendar
words, currency words, symbols or codes, ids or role labels, severity, confidence or
likelihood words, markup, invisible or non-Latin characters, and nothing that reads
as an audit opinion or a certainty about a cause. It may not name, even in other
words, a root cause that none of the verified hypotheses in view has.

A finding's narrative must name its exceptions and, if it was analysed, present
each verified hypothesis exactly once, in rank order, through its single fact
({{fact:G1.h1}}: root cause and confidence together, so they cannot be swapped);
otherwise it must state its status.

A section that fails is replaced by code's template text; a pattern that fails is
left out. Both are recorded and flagged for the reviewer.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable

from pydantic import BaseModel, ConfigDict

from calipa.analyst.step import Finding
from calipa.analyst.tokens import CODES, LABEL, unit_errors, value_errors
from calipa.reporter.facts import CATEGORY_LABELS, Fact
from calipa.reporter.schema import ReportNarrative

_Allowed = Callable[[str], bool]

FACT = re.compile(r"\{\{fact:([A-Za-z0-9_.]+)\}\}")
_IDS = re.compile(r"\b[ECGH][0-9]+\b")
_MARKUP = re.compile(
    r"[<>\[\]{}`|#*\\_~@&]|\b(?:https?|ftp|file|mailto|javascript|data|vbscript):|www\.",
    re.IGNORECASE,
)
# Severity, confidence and likelihood come only from facts and the rank order.
_RANKED = re.compile(
    r"\b(critical\w*|high\w*|low\w*|medium|severe\w*|minor|major|likel\w*|unlikel\w*|"
    r"probabl\w*|improbabl\w*|plausib\w*|implausib\w*|doubtful|rul(e|es|ed|ing) out|"
    r"exclud\w*|best explanation|strong\w*|weak\w*)\b",
    re.IGNORECASE,
)
_BANNED = (
    (
        re.compile(
            r"\b(ineffective\w*|effectiveness|controls?\b[^.]{0,40}\beffective|"
            r"controls? (failed|worked|operated|broke down)|deficien\w*|"
            r"(in)?adequa\w*|accura(te|cy)|inaccura\w*|reliab\w*|unreliab\w*)\b",
            re.IGNORECASE,
        ),
        "control effectiveness",
    ),
    (
        re.compile(r"\b((non-?)?complian\w*|breach\w*|violat\w*|regulator\w*)\b", re.IGNORECASE),
        "compliance",
    ),
    (
        re.compile(r"\b((im)?material(ity|ly)?|misstat\w*|significan\w*)\b", re.IGNORECASE),
        "materiality",
    ),
    (
        re.compile(
            r"(\w*fraud\w*|\blaunder\w*|\bstole\w*|\btheft\b|\bthie(f|ves)\b|\bembezzl\w*|"
            r"\bmisappropriat\w*|\bconceal\w*|\bcollu\w*|\bmanipulat\w*)",
            re.IGNORECASE,
        ),
        "fraud",
    ),
    (
        re.compile(
            r"\b(intent\w*|intend\w*|deliberat\w*|on purpose|knowing(ly)?|wilful\w*|"
            r"willful\w*|negligen\w*|misconduct|malicious\w*)\b",
            re.IGNORECASE,
        ),
        "intent or misconduct",
    ),
    (
        re.compile(
            r"\b(audit opinion|our opinion|in my opinion|we conclude|conclusion|fairly stated|"
            r"true and fair|(un)?qualified opinion|adverse opinion|assurance|satisf(ied|actory)|"
            r"came to our attention|no exceptions? of note)\b",
            re.IGNORECASE,
        ),
        "an audit opinion",
    ),
    (
        re.compile(
            r"\b(root cause|the cause (is|was)|is the cause|was the cause|"
            r"(is|was|were|are) caused by|resulted from|must have|definite\w*|certain\w*|"
            r"undoubted\w*|without (a )?doubt|beyond (a |any )?doubt|conclusive\w*|clearly|"
            r"obvious\w*|evident\w*|unambiguous\w*|proves|proven|confirms|demonstrat\w*|"
            r"establish(es|ed))\b",
            re.IGNORECASE,
        ),
        "certainty about a cause",
    ),
)
# Words that name a root cause; allowed only where a verified hypothesis has it.
_CAUSE_WORDS: dict[str, re.Pattern[str]] = {
    "rounding_difference": re.compile(r"\bround(ing|ed|s)?\b", re.IGNORECASE),
    "switch_timeout_unposted": re.compile(r"\b(time-?outs?|timed out)\b", re.IGNORECASE),
    "timing_cutoff_difference": re.compile(r"\b(cut-?offs?|timing)\b", re.IGNORECASE),
    "wrong_account_mapping": re.compile(
        r"\b(wrong account|incorrect account|mis-?post\w*|mis-?mapp\w*|mapping)\b",
        re.IGNORECASE,
    ),
    "manual_adjustment": re.compile(r"\b(manual\w*|adjustment\w*)\b", re.IGNORECASE),
    "posted_not_settled": re.compile(r"\b(not settled|unsettled)\b", re.IGNORECASE),
    "missing_reversal": re.compile(r"\b(revers(al|als|ed|ing)?|not reversed)\b", re.IGNORECASE),
}
_REVERSALS = {"missing_reversal", "partial_reversal", "orphan_reversal"}
_AMOUNT_FIELDS = {
    "amount",
    "expected",
    "actual",
    "difference",
    "net",
    "credits_to_transient",
    "debits_from_transient",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SectionCheck(_Model):
    ref: str  # "executive_summary", "G1", "pattern 1"
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


class ReportCheck(_Model):
    summary: SectionCheck
    findings: list[SectionCheck]  # in the order P3 gave them
    patterns: list[SectionCheck]
    missing: list[str]  # finding keys P3 wrote nothing for

    def errors(self) -> list[str]:
        out = [f"{c.ref}: {e}" for c in self.sections() for e in c.errors]
        return out + [f"{key}: no narrative was written for this finding" for key in self.missing]

    def sections(self) -> list[SectionCheck]:
        return [self.summary, *self.findings, *self.patterns]

    @property
    def clean(self) -> bool:
        return not self.errors()

    def passing(self) -> int:
        """Sections usable as written: the score for choosing between attempts."""
        return sum(c.ok for c in self.sections())


def check_report(
    narrative: ReportNarrative, facts: dict[str, Fact], findings: dict[str, Finding]
) -> ReportCheck:
    every = set(findings)
    summary = SectionCheck(
        ref="executive_summary",
        errors=_text_errors(
            narrative.executive_summary,
            facts,
            allowed=_within(("run.", "G")),
            categories=_categories(findings.values()),
            required=False,
        ),
    )

    seen: dict[str, int] = {}
    for f in narrative.findings:
        seen[f.finding_key] = seen.get(f.finding_key, 0) + 1
    finding_checks: list[SectionCheck] = []
    for f in narrative.findings:
        errors: list[str] = []
        finding = findings.get(f.finding_key)
        if finding is None:
            finding_checks.append(
                SectionCheck(ref=f.finding_key, errors=[f"{f.finding_key} is not a finding key"])
            )
            continue
        if seen[f.finding_key] > 1:
            errors.append("write exactly one narrative per finding key")
        own = f"{f.finding_key}."
        errors += _text_errors(
            f.narrative,
            facts,
            allowed=_within(("run.", own)),
            categories=_categories([finding]),
        )
        used = list(dict.fromkeys(FACT.findall(f.narrative)))
        if set(f.fact_keys_used) != set(used):
            errors.append(f"fact_keys_used must list exactly the facts it cites: {used}")
        errors += _coverage(f.finding_key, finding, f.narrative)
        finding_checks.append(SectionCheck(ref=f.finding_key, errors=errors))

    pattern_checks: list[SectionCheck] = []
    for i, p in enumerate(narrative.cross_finding_patterns, start=1):
        errors = []
        keys = list(dict.fromkeys(p.finding_keys))
        if len(keys) < 2:
            errors.append("a pattern links two or more different findings")
        unknown = [k for k in keys if k not in every]
        if unknown:
            errors.append(f"{unknown} are not finding keys")
        prefixes = ("run.", *(f"{k}." for k in keys))
        errors += _text_errors(
            p.text,
            facts,
            allowed=_within(prefixes),
            categories=_categories(findings[k] for k in keys if k in findings),
        )
        pattern_checks.append(SectionCheck(ref=f"pattern {i}", errors=errors))

    written = {f.finding_key for f in narrative.findings}
    return ReportCheck(
        summary=summary,
        findings=finding_checks,
        patterns=pattern_checks,
        missing=[k for k in findings if k not in written],
    )


def _coverage(key: str, finding: Finding, text: str) -> list[str]:
    errors: list[str] = []
    keys = FACT.findall(text)
    if f"{key}.exceptions" not in keys:
        errors.append(f"must say what the engine found with {{{{fact:{key}.exceptions}}}}")
    if finding.analysis is None:
        if f"{key}.status" not in keys:
            errors.append(f"must state that it was not analysed with {{{{fact:{key}.status}}}}")
        return errors
    positions: list[int] = []
    for h in finding.analysis.hypotheses:
        fact = f"{key}.h{h.rank}"
        count = keys.count(fact)
        if count != 1:
            errors.append(
                f"must present hypothesis {h.rank} exactly once with {{{{fact:{fact}}}}}, "
                f"not {count} times"
            )
        else:
            positions.append(keys.index(fact))
    if positions != sorted(positions):
        errors.append("must present the hypotheses in rank order")
    return errors


def _within(prefixes: tuple[str, ...]) -> _Allowed:
    return lambda key: key.startswith(prefixes)


def _categories(findings: Iterable[Finding]) -> set[str]:
    return {h.category for f in findings if f.analysis is not None for h in f.analysis.hypotheses}


def _text_errors(
    text: str,
    facts: dict[str, Fact],
    *,
    allowed: _Allowed,
    categories: set[str],
    required: bool = True,
) -> list[str]:
    errors: list[str] = []
    if not text.strip():
        return ["the text is empty"]
    matches = list(FACT.finditer(text))
    for match in matches:
        key = match.group(1)
        if key not in facts:
            errors.append(f"{match.group()}: there is no fact {key!r}")
        elif not allowed(key):
            errors.append(f"{match.group()} belongs to another finding")
        errors += unit_errors(match.group(), key.rsplit(".", 1)[-1], text[match.end() :])
    errors += _placement_errors(text, matches)
    if required and not matches:
        errors.append("must cite facts with {{fact:KEY}} placeholders")
    rest = FACT.sub(" ", text)
    errors += value_errors(rest)  # also rejects invisible and lookalike characters
    plain = unicodedata.normalize("NFKC", rest)
    for pattern, what in (
        (CODES, "a currency code"),
        (LABEL, "an account label"),
        (_IDS, "an evidence, claim or finding id"),
    ):
        for found in pattern.finditer(plain):
            errors.append(f"{what} ({found.group()!r}) is not allowed; use a fact placeholder")
    for found in _MARKUP.finditer(plain):
        errors.append(f"{found.group()!r} is not allowed: plain sentences only, no markup")
    for found in _RANKED.finditer(plain):
        errors.append(
            f"{found.group()!r}: severity, confidence and likelihood come only from the "
            "facts and the rank order"
        )
    for pattern, what in _BANNED:
        for found in pattern.finditer(plain):
            errors.append(f"{found.group()!r} reads as {what}, which is the auditor's call")
    lowered = plain.lower()
    present = set(categories) | (_REVERSALS if categories & _REVERSALS else set())
    for category, label in CATEGORY_LABELS.items():
        if category not in present and label.lower() in lowered:
            errors.append(f"names {label!r}, which no verified hypothesis here has")
    for cause, words in _CAUSE_WORDS.items():
        if cause not in present:
            for found in words.finditer(plain):
                errors.append(
                    f"{found.group()!r} names a root cause no verified hypothesis here has"
                )
    return errors


def _placement_errors(text: str, matches: list[re.Match[str]]) -> list[str]:
    """Nothing may change a value from outside its placeholder: no letter, digit, sign
    or point touching it, and no two placeholders run together, except a currency
    next to its own amount. A currency stands only next to its own amount."""
    errors: list[str] = []
    for i, match in enumerate(matches):
        before = text[match.start() - 1] if match.start() > 0 else " "
        after = text[match.end() : match.end() + 2]
        if before.isalnum() or before in "-+.$/'\u2212":
            errors.append(f"{match.group()} must not be joined to {before!r}")
        if (
            after[:1].isalnum()
            or after[:1] in "-+$/%'\u2212"
            or (after[:1] in ".," and after[1:2].isalnum())
        ):
            errors.append(f"{match.group()} must not be joined to {after[:1]!r}")
        key = match.group(1)
        partner = _currency_partner(key)
        neighbours = [
            m.group(1)
            for m in (
                matches[i - 1] if i else None,
                matches[i + 1] if i + 1 < len(matches) else None,
            )
            if m is not None and _adjacent(text, m, match)
        ]
        for other in neighbours:
            if not (_currency_partner(other) == key or partner == other or _pairs(key, other)):
                errors.append(f"{match.group()} and {{{{fact:{other}}}}} run together")
        if partner is not None and not any(_pairs(key, other) for other in neighbours):
            errors.append(f"{match.group()} must stand next to the amount it belongs to")
    return list(dict.fromkeys(errors))


def _adjacent(text: str, a: re.Match[str], b: re.Match[str]) -> bool:
    first, second = sorted((a, b), key=lambda m: m.start())
    return not text[first.end() : second.start()].strip()


def _currency_partner(key: str) -> str | None:
    """The item of a currency fact that must stand next to its amount, else None."""
    item, field = key.rsplit(".", 1)
    if field == "currency" or (
        field.endswith("_currency") and not field.startswith(("from", "to"))
    ):
        return item
    return None


def _pairs(a: str, b: str) -> bool:
    """A currency fact and an amount of the same item."""
    for cur, amt in ((a, b), (b, a)):
        item, field = cur.rsplit(".", 1)
        other_item, other_field = amt.rsplit(".", 1)
        if item != other_item or _currency_partner(cur) is None:
            continue
        if field == "currency" and other_field in _AMOUNT_FIELDS:
            return True
        if field.endswith("_currency") and other_field == field.replace("_currency", "_amount"):
            return True
    return False
