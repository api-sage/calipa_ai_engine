"""The text rules for P2's answers.

P2 never writes a value. Every number, date, time, amount or id it relies on is a
placeholder, `{E23.expected}`, naming the evidence item and field; code puts the value
in when the text is shown. So a value cannot be invented, rounded, re-signed,
reformatted or attached to the wrong field: the placeholder says where it comes from.

What is left after removing placeholders, evidence and claim references (E3, C2) and
role labels (CUSTOMER_1) must contain no digit in any script, no number written as a
word or roman numeral, no currency word or symbol, and no unit or percent sign that
changes what a placeholder's value means.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

PLACEHOLDER = re.compile(r"\{(E[1-9][0-9]*)\.([a-z][a-z0-9_]*)\}")
REF = re.compile(r"\b([EC][1-9][0-9]*)\b")
LABEL = re.compile(r"\b((?:CUSTOMER|BENEFICIARY|OTHER)_[0-9]+)\b")
CODES = re.compile(r"\b(USD|EUR|GBP|NGN)\b", re.IGNORECASE)

_NUMBER_WORDS = re.compile(
    r"\b(zero|(?<!no )one(?! of\b)|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
    r"fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|millions?|billions?|"
    r"trillions?|half|halves|quarters?|double|twice|triple|thrice|once|single|pairs?|"
    r"dozens?|percent|pct|bps|basis points?|bn|mn|"
    r"third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|twentieth|"
    r"thirtieth|hundredth|thousandth|minus|negative)\b",
    re.IGNORECASE,
)
# Calendar words state dates. "May" is left out: it is mostly the verb.
_CALENDAR_WORDS = re.compile(
    r"\b(january|february|march|april|june|july|august|september|october|november|"
    r"december|monday|tuesday|wednesday|thursday|friday|saturday|sunday|midnight|noon|"
    r"yesterday|today|tomorrow)\b",
    re.IGNORECASE,
)
_ROMAN = re.compile(
    r"\b(?=[MDCLXVI]{2,}\b)M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\b"
)
_CURRENCY_WORDS = re.compile(
    r"\b(dollars?|pounds?|sterling|euros?|naira|cents?|pence|penny|kobo|yen|yuan|"
    r"francs?|rupees?|cedis?|shillings?|rand|bucks?|quid)\b",
    re.IGNORECASE,
)
_SYMBOLS = re.compile(r"[%‰‱¤]")
_OTHER_CODES = re.compile(
    r"\b(JPY|CHF|CNY|CAD|AUD|NZD|ZAR|KES|GHS|XOF|XAF|INR|AED|SAR|HKD|SGD|SEK|NOK|DKK|"
    r"BRL|MXN|EGP|MAD|TZS|UGX|RWF)\b",
    re.IGNORECASE,
)
_UNIT = re.compile(
    r"\s*-?\s*(milliseconds?|ms|microseconds?|nanoseconds?|seconds?|secs?|minutes?|mins?|"
    r"hours?|hrs?|days?|weeks?|fortnights?|months?|quarters?|years?|decades?|centuries)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Placeholder:
    evidence_id: str
    field: str

    def __str__(self) -> str:
        return f"{{{self.evidence_id}.{self.field}}}"


@dataclass(frozen=True, slots=True)
class Scan:
    placeholders: list[Placeholder]
    refs: list[str]  # E3, C2
    labels: list[str]  # CUSTOMER_1
    codes: list[str]  # USD, EUR, GBP, NGN written out
    errors: list[str]  # not allowed in the text whatever it cites


def scan(text: str) -> Scan:
    errors: list[str] = []
    placeholders: list[Placeholder] = []
    for match in PLACEHOLDER.finditer(text):
        placeholders.append(Placeholder(match.group(1), match.group(2)))
        errors += unit_errors(match.group(), match.group(2), text[match.end() :])
    rest = PLACEHOLDER.sub(" ", text)
    labels = LABEL.findall(rest)
    rest = LABEL.sub(" ", rest)
    refs = [r.upper() for r in REF.findall(rest)]
    rest = REF.sub(" ", rest)
    codes = [c.upper() for c in CODES.findall(rest)]
    errors += value_errors(rest)
    return Scan(placeholders, refs, labels, codes, errors)


def unit_errors(placeholder: str, field: str, after: str) -> list[str]:
    """A unit written after a placeholder must be the one its field measures."""
    unit = _UNIT.match(after)
    if unit and unit.group(1).lower()[:3] not in field:
        return [
            f"{placeholder} is followed by {unit.group(1)!r}, which that field does not measure"
        ]
    return []


def value_errors(rest: str) -> list[str]:
    """Errors for values written into text that should hold none (placeholders and
    allowed words already removed). Lookalike and invisible characters are rejected
    first, so the word checks below see what a reader would see."""
    errors = character_errors(rest)
    original, rest = rest, unicodedata.normalize("NFKC", rest)
    digits = [
        w
        for w in (*original.split(), *rest.split())
        if any(unicodedata.category(c).startswith("N") for c in w)
    ]
    if digits:
        errors.append(
            "write values as placeholders, never as digits: "
            + ", ".join(repr(w) for w in digits[:3])
        )
    if any(unicodedata.category(c) == "Sc" for c in rest):
        errors.append("a currency symbol is not allowed; use a placeholder")
    for pattern, what in (
        (_NUMBER_WORDS, "a number or scale written as a word"),
        (_CALENDAR_WORDS, "a date or time written as a word"),
        (_ROMAN, "a roman numeral"),
        (_CURRENCY_WORDS, "a currency written as a word"),
        (_SYMBOLS, "a percent or currency symbol"),
        (_OTHER_CODES, "a currency Calipa does not handle"),
    ):
        for found in pattern.finditer(rest):
            errors.append(f"{what} ({found.group()!r}) is not allowed; use a placeholder")
    return errors


def character_errors(text: str) -> list[str]:
    """Invisible characters (which can reorder or hide what is shown), private-use
    characters, and letters from scripts other than Latin (lookalikes)."""
    errors: list[str] = []
    hidden = sorted({f"U+{ord(c):04X}" for c in text if unicodedata.category(c) in ("Cf", "Co")})
    if hidden:
        errors.append(f"invisible or private characters are not allowed: {', '.join(hidden)}")
    foreign = sorted(
        {c for c in text if c.isalpha() and not unicodedata.name(c, "").startswith("LATIN")}
    )
    if foreign:
        errors.append(f"letters outside the Latin script are not allowed: {''.join(foreign)}")
    return errors


def render(text: str, values: dict[tuple[str, str], str]) -> str:
    """The text with each placeholder replaced by its value."""
    return PLACEHOLDER.sub(lambda m: values[(m.group(1), m.group(2))], text)
