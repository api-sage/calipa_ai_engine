"""What AI-written text may not say, in V2 (the analyst's claims, summaries and
limitations) and in V3 (the report's prose). Both end up in front of the reviewer.

Calipa explains exceptions; it does not judge. Text may not give an audit opinion or
a conclusion that is the auditor's to reach (control effectiveness, compliance,
materiality, fraud, intent), claim certainty about a cause, make or anticipate the
reviewer's decision ("approved", "no further review", "false positive"), cite a
confirmation the evidence does not contain ("operations confirmed", "a known
issue"), say the records are right, or carry instructions. The last four are what
instruction text planted in bank data tries to get a model to write.
"""

from __future__ import annotations

import re
import unicodedata

BANNED: tuple[tuple[re.Pattern[str], str], ...] = (
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
    (
        re.compile(
            # "approved" alone is the approved scope and controls the run used
            r"\b(approv(e|es|al|als|ing)|(is|are|was|were|be|been|has|have|had) "
            r"(now |already |fully )?approved|sign(s|ed|ing)?[- ]off|"
            r"no (further |more )?(review|action|follow-?up|attention)|"
            r"(needs?|requires?|requiring) no|nothing (else )?(to|needs?|requires?)|"
            r"disregard\w*|ignor\w*|dismiss\w*|resolved|"
            r"false positives?|can be (closed|cleared))\b",
            re.IGNORECASE,
        ),
        "a review decision",
    ),
    (
        re.compile(
            r"\b((confirmed|verified|validated|explained|acknowledged) by (operations|"
            r"the bank|management|the auditor|the reviewer|the customer|the business|"
            r"staff|the team)|(has|have) (already )?been (confirmed|verified|validated|"
            r"explained|acknowledged)|(operations|the bank|management|the auditor|"
            r"the reviewer|the customer|the business|staff|the team) (has |have )?"
            r"(already )?(confirmed|verified|validated|explained|said|stated|agreed)|"
            r"known (issue|problem|difference)s?|expected (behaviou?r|difference)s?|"
            r"as expected|(is|are|was|were) normal|business as usual)\b",
            re.IGNORECASE,
        ),
        "a confirmation the evidence does not contain",
    ),
    (
        re.compile(
            r"\b(reconcil(es|ed)|(ledger|books|accounts?) balances?|(ledger|books|"
            r"accounts?|records?|everything|transfers?|postings?|entries) (is|are|was|"
            r"were) (all )?(correct|valid|fine|in order|legitimate|balanced)|"
            r"no (issues?|problems?|errors?|discrepanc\w*)|engine (is|was) (wrong|"
            r"incorrect|mistaken))\b",
            re.IGNORECASE,
        ),
        "a judgement that the records are right",
    ),
    (
        re.compile(
            r"(\b(system|assistant|developer|user)\s*:|\b(prompt\w*|"
            r"overrid\w*)\b)",
            re.IGNORECASE,
        ),
        "an instruction",
    ),
)


def wording_errors(text: str) -> list[str]:
    """Errors for `text` with its placeholders already taken out."""
    plain = unicodedata.normalize("NFKC", text)
    return [
        f"{found.group()!r} reads as {what}, which is not for Calipa to say"
        for pattern, what in BANNED
        for found in pattern.finditer(plain)
    ]
