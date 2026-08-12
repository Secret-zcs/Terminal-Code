from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from mewcode.evolution import repository_benchmark
from mewcode.tools import create_default_registry
from mewcode.tools.edit_file import EditFile, Params as EditFileParams
from mewcode.tools.write_file import Params as WriteFileParams
from mewcode.tools.write_file import WriteFile
from mewcode.evolution.repository_benchmark import load_repository_fixtures


class RecordingFileHistory:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def track_edit(self, file_path: str) -> None:
        self.paths.append(file_path)


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


@pytest.mark.asyncio
async def test_glob_and_grep_ignore_skipped_workspace_ancestors(tmp_path: Path) -> None:
    repo = tmp_path / "node_modules" / "repo"
    repo.mkdir(parents=True)
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


@pytest.mark.asyncio
async def test_write_and_edit_track_workspace_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "input.txt").write_text("before\n", encoding="utf-8")
    file_history = RecordingFileHistory()

    write_result = await WriteFile(file_history=file_history, work_dir=repo).execute(
        WriteFileParams(file_path="output.txt", content="created\n")
    )
    edit_result = await EditFile(file_history=file_history, work_dir=repo).execute(
        EditFileParams(
            file_path="input.txt",
            old_string="before",
            new_string="after",
        )
    )

    assert not write_result.is_error
    assert not edit_result.is_error
    assert file_history.paths == [
        str((repo / "output.txt").resolve()),
        str((repo / "input.txt").resolve()),
    ]


def _write_repository_fixture(
    root: Path,
    case_id: str,
    *,
    issue: str = "Fix the parser regression.\n",
    expected: dict[str, object] | None = None,
) -> Path:
    case = root / case_id
    (case / "repository").mkdir(parents=True)
    (case / "issue.md").write_text(issue, encoding="utf-8")
    payload = expected or {
        "test_command": "pytest tests/test_parser.py -q",
        "expected_tests": ["tests/test_parser.py"],
        "allowed_paths": ["mewcode/parser.py", "tests/test_parser.py"],
        "forbidden_paths": ["pyproject.toml"],
    }
    (case / "expected.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return case


def test_load_repository_fixtures_reads_issue_and_expected_data(tmp_path: Path) -> None:
    case = _write_repository_fixture(tmp_path, "parser-fix")

    fixtures = load_repository_fixtures(tmp_path)

    assert len(fixtures) == 1
    fixture = fixtures[0]
    assert fixture.id == "parser-fix"
    assert fixture.repository == case / "repository"
    assert fixture.issue == "Fix the parser regression."
    assert fixture.test_command == "pytest tests/test_parser.py -q"
    assert fixture.regression_command == ""
    assert fixture.expected_tests == ("tests/test_parser.py",)
    assert fixture.allowed_paths == (
        "mewcode/parser.py",
        "tests/test_parser.py",
    )
    assert fixture.forbidden_paths == ("pyproject.toml",)
    with pytest.raises(FrozenInstanceError):
        setattr(fixture, "id", "other-case")


def test_load_repository_fixtures_sorts_cases_by_id(tmp_path: Path) -> None:
    _write_repository_fixture(tmp_path, "case-z")
    _write_repository_fixture(tmp_path, "case-a")
    (tmp_path / "README.md").write_text("ignored", encoding="utf-8")

    fixtures = load_repository_fixtures(tmp_path)

    assert [fixture.id for fixture in fixtures] == ["case-a", "case-z"]


@pytest.mark.parametrize(
    "invalid_path",
    [
        "",
        "/tmp/test_parser.py",
        "tests/../secret.py",
        "C:/outside",
        "C:outside",
        "//server/share/outside.py",
        "\\\\server\\share\\outside.py",
    ],
)
@pytest.mark.parametrize(
    "field",
    ["expected_tests", "allowed_paths", "forbidden_paths"],
)
def test_load_repository_fixtures_rejects_unsafe_paths(
    tmp_path: Path,
    field: str,
    invalid_path: str,
) -> None:
    payload: dict[str, object] = {
        "test_command": "pytest -q",
        "expected_tests": ["tests/test_parser.py"],
        "allowed_paths": ["mewcode/parser.py"],
        "forbidden_paths": [],
    }
    payload[field] = [invalid_path]
    _write_repository_fixture(tmp_path, "unsafe-case", expected=payload)

    with pytest.raises(ValueError, match="unsafe-case"):
        load_repository_fixtures(tmp_path)


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("missing", None),
        ("test_command", ""),
        ("test_command", 42),
        ("expected_tests", []),
        ("expected_tests", "tests/test_parser.py"),
        ("allowed_paths", []),
        ("forbidden_paths", [1]),
        ("regression_command", ["pytest -q"]),
    ],
)
def test_load_repository_fixtures_rejects_missing_fields_and_wrong_types(
    tmp_path: Path,
    change: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "test_command": "pytest -q",
        "expected_tests": ["tests/test_parser.py"],
        "allowed_paths": ["mewcode/parser.py"],
        "forbidden_paths": [],
    }
    if change == "missing":
        del payload["test_command"]
    else:
        payload[change] = value
    _write_repository_fixture(tmp_path, "invalid-fields", expected=payload)

    with pytest.raises(ValueError, match="invalid-fields"):
        load_repository_fixtures(tmp_path)


def test_load_repository_fixtures_rejects_empty_issue(tmp_path: Path) -> None:
    _write_repository_fixture(tmp_path, "empty-issue", issue=" \n")

    with pytest.raises(ValueError, match="empty-issue"):
        load_repository_fixtures(tmp_path)


@pytest.mark.parametrize("missing", ["repository", "issue.md", "expected.json"])
def test_load_repository_fixtures_requires_case_files(
    tmp_path: Path,
    missing: str,
) -> None:
    case = _write_repository_fixture(tmp_path, "incomplete-case")
    target = case / missing
    if target.is_dir():
        target.rmdir()
    else:
        target.unlink()

    with pytest.raises(ValueError, match="incomplete-case"):
        load_repository_fixtures(tmp_path)


def test_load_repository_fixtures_rejects_invalid_json(tmp_path: Path) -> None:
    case = _write_repository_fixture(tmp_path, "bad-json")
    (case / "expected.json").write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="bad-json"):
        load_repository_fixtures(tmp_path)


def test_load_repository_fixtures_wraps_json_recursion_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_repository_fixture(tmp_path, "recursive-json")

    def raise_recursion_error(_: str) -> object:
        raise RecursionError("nested too deeply")

    monkeypatch.setattr(repository_benchmark.json, "loads", raise_recursion_error)

    with pytest.raises(ValueError, match="recursive-json"):
        load_repository_fixtures(tmp_path)


@pytest.mark.parametrize("target", ["case", "repository", "issue.md", "expected.json"])
def test_load_repository_fixtures_rejects_fixture_symlinks(
    tmp_path: Path,
    target: str,
) -> None:
    fixture_root = tmp_path / "fixtures"
    case = _write_repository_fixture(fixture_root, "linked-case")
    if target == "case":
        real_case = tmp_path / "real-case"
        case.rename(real_case)
        case.symlink_to(real_case, target_is_directory=True)
    elif target == "repository":
        repository = case / "repository"
        repository.rmdir()
        external_repository = tmp_path / "external-repository"
        external_repository.mkdir()
        repository.symlink_to(external_repository, target_is_directory=True)
    else:
        metadata = case / target
        external_metadata = tmp_path / f"external-{target}"
        metadata.rename(external_metadata)
        metadata.symlink_to(external_metadata)

    with pytest.raises(ValueError, match="linked-case"):
        load_repository_fixtures(fixture_root)


def test_load_repository_fixtures_rejects_repository_internal_symlink(
    tmp_path: Path,
) -> None:
    case = _write_repository_fixture(tmp_path / "fixtures", "internal-link")
    external_file = tmp_path / "outside.txt"
    external_file.write_text("secret", encoding="utf-8")
    (case / "repository" / "linked.txt").symlink_to(external_file)

    with pytest.raises(ValueError, match="internal-link"):
        load_repository_fixtures(tmp_path / "fixtures")


def test_load_repository_fixtures_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_repository_fixtures(tmp_path / "missing")
