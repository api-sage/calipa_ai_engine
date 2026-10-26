"""What P3 (the Report Writer) returns.

P3 writes explanatory prose only. Every value in it is a placeholder,
{{fact:G1.severity}}, naming a fact code prepared; code fills in the value. Scope,
data, totals, tables, limitations and the auditor's opinion are rendered by code.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MAX_TEXT = 2_000


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FindingText(_Model):
    finding_key: str = Field(max_length=8)  # G1, G2, ...
    narrative: str = Field(max_length=MAX_TEXT)  # values only as {{fact:KEY}}
    fact_keys_used: list[str]  # exactly the keys its placeholders name


class Pattern(_Model):
    text: str = Field(max_length=MAX_TEXT)
    finding_keys: list[str]  # two or more findings it links


class ReportNarrative(_Model):
    executive_summary: str = Field(max_length=MAX_TEXT)
    findings: list[FindingText]
    cross_finding_patterns: list[Pattern] = Field(max_length=10)
