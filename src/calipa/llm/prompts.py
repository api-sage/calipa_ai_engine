"""Versioned prompt files. A prompt's identity is its name, version and SHA-256;
all three are recorded with every call."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from calipa.canonical import sha256_hex

_PROMPTS_PACKAGE = "calipa.prompts"
_FILENAME = re.compile(r"^(?P<name>[a-z][a-z0-9_]*)\.v(?P<version>[0-9]+)\.md$")


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    text: str

    @property
    def sha256(self) -> str:
        return sha256_hex(self.text.encode("utf-8"))


def load_prompt(name: str, version: int, *, directory: Path | None = None) -> Prompt:
    filename = f"{name}.v{version}.md"
    if not _FILENAME.match(filename):
        raise ValueError(f"invalid prompt name {name!r}")
    if directory is not None:
        text = (directory / filename).read_text("utf-8")
    else:
        text = resources.files(_PROMPTS_PACKAGE).joinpath(filename).read_text("utf-8")
    if not text.strip():
        raise ValueError(f"prompt {filename} is empty")
    return Prompt(name=name, version=str(version), text=text)
