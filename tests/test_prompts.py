from pathlib import Path

import pytest

from calipa.canonical import sha256_hex
from calipa.llm.prompts import load_prompt


def test_prompt_identity_is_name_version_and_hash(tmp_path: Path) -> None:
    (tmp_path / "interpreter.v2.md").write_text("You convert requests.\n", "utf-8")
    prompt = load_prompt("interpreter", 2, directory=tmp_path)
    assert (prompt.name, prompt.version) == ("interpreter", "2")
    assert prompt.sha256 == sha256_hex(b"You convert requests.\n")


def test_bad_names_and_empty_files_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_prompt("../secrets", 1, directory=tmp_path)
    (tmp_path / "report.v1.md").write_text("   \n", "utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_prompt("report", 1, directory=tmp_path)
