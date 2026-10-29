"""Putting data inside XML-style tags in a prompt."""

from __future__ import annotations

import json
from typing import Any


def tagged_json(value: Any) -> str:
    """JSON with angle brackets escaped, so text people or the bank wrote cannot close
    or open a tag."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1)
    return text.replace("<", "\\u003c").replace(">", "\\u003e")
