"""Errors the rest of Calipa tells apart from ordinary failures.

A step that raises is retried, because most failures (a slow bank API, a dropped
connection, a model timeout) pass. An `IntegrityViolation` does not pass: stored or
reference data no longer matches what the run recorded, so retrying would only
repeat the mismatch, and hide it for longer. The runner fails the run at once.
"""

from __future__ import annotations

from uuid import UUID


class IntegrityViolation(RuntimeError):
    """Data the run depends on does not match what was recorded."""


class RunNotFound(LookupError):
    """No run has this id."""

    def __init__(self, run_id: UUID) -> None:
        super().__init__(f"run {run_id} not found")
        self.run_id = run_id
