from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.tools import create_default_registry


@pytest.mark.asyncio
async def test_default_registry_tools_use_workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "input.txt").write_text("before\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    registry = create_default_registry(work_dir=repo)

    read_result = await registry.get("ReadFile").execute(
        registry.get("ReadFile").params_model(file_path="input.txt")
    )
    assert not read_result.is_error
    assert "1\tbefore" in read_result.output

    bash_result = await registry.get("Bash").execute(
        registry.get("Bash").params_model(command="printf after > output.txt")
    )
    assert not bash_result.is_error
    assert (repo / "output.txt").read_text(encoding="utf-8") == "after"
    assert not (tmp_path / "output.txt").exists()


@pytest.mark.asyncio
async def test_glob_and_grep_use_workspace_root_for_relative_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "input.txt").write_text("before\n", encoding="utf-8")

    registry = create_default_registry(work_dir=repo)

    glob_tool = registry.get("Glob")
    glob_result = await glob_tool.execute(
        glob_tool.params_model(pattern="*.txt", path=".")
    )
    assert glob_result.output == "input.txt"

    grep_tool = registry.get("Grep")
    grep_result = await grep_tool.execute(
        grep_tool.params_model(pattern="before", path=".", include="*.txt")
    )
    assert grep_result.output == "input.txt:1:before"
