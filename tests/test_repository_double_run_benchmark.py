from __future__ import annotations

import asyncio
import json
import shutil
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from mewcode.client import LLMClient
from mewcode.conversation import ConversationManager
from mewcode.evolution import repository_benchmark
from mewcode.evolution.repository_benchmark import (
    RepositoryFixture,
    compare_snapshots,
    load_repository_fixtures,
    run_local_command,
    run_repository_case,
    snapshot_repository,
)
from mewcode.tools import create_default_registry
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete
from mewcode.tools.edit_file import EditFile, Params as EditFileParams
from mewcode.tools.write_file import Params as WriteFileParams
from mewcode.tools.write_file import WriteFile


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


def test_snapshot_repository_ignores_runtime_and_cache_directories(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_bytes(b"value = '\xff'\n")
    for directory in (".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".mewcode"):
        ignored = tmp_path / "nested" / directory
        ignored.mkdir(parents=True)
        (ignored / "state.txt").write_text("ignored", encoding="utf-8")

    snapshot = snapshot_repository(tmp_path)

    assert snapshot == {"src/module.py": "value = '\ufffd'\n"}


def test_compare_snapshots_reports_scope_forbidden_and_patch_size() -> None:
    before = {
        "src/main.py": "old\nkeep\n",
        "src/removed.py": "removed\n",
        "pyproject.toml": "version = 1\n",
    }
    after = {
        "src/main.py": "new\nkeep\n",
        "tests/new_test.py": "test = True\n",
        "pyproject.toml": "version = 2\n",
    }

    result = compare_snapshots(
        before,
        after,
        allowed_paths=("src/*.py", "tests/**"),
        forbidden_paths=("pyproject.toml",),
    )

    assert result["changed_paths"] == [
        "pyproject.toml",
        "src/main.py",
        "src/removed.py",
        "tests/new_test.py",
    ]
    assert result["out_of_scope_changes"] == ["pyproject.toml"]
    assert result["forbidden_changes"] == ["pyproject.toml"]
    assert result["patch_size"] == {"added": 3, "removed": 3, "total": 6}


def test_run_local_command_classifies_pass_fail_and_timeout(tmp_path: Path) -> None:
    passed = run_local_command(
        f'{sys.executable} -c "print(\'ok\')"', tmp_path, 2
    )
    failed = run_local_command(
        f'{sys.executable} -c "import sys; print(\'bad\', file=sys.stderr); sys.exit(7)"',
        tmp_path,
        2,
    )
    timed_out = run_local_command(
        f'{sys.executable} -c "import time; print(\'start\', flush=True); time.sleep(1)"',
        tmp_path,
        0.05,
    )

    assert (passed["status"], passed["exit_code"], passed["stdout"]) == (
        "passed",
        0,
        "ok\n",
    )
    assert (failed["status"], failed["exit_code"]) == ("failed", 7)
    assert failed["stderr"] == "bad\n"
    assert timed_out["status"] == "timeout"
    assert timed_out["exit_code"] is None
    assert "start" in timed_out["stdout"]
    assert isinstance(timed_out["stderr"], str)
    assert all(result["elapsed_seconds"] >= 0 for result in (passed, failed, timed_out))


class ScriptedRepositoryClient(LLMClient):
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self.responses = responses
        self.call_index = 0
        self.system_prompts: list[str] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.system_prompts.append(system)
        events = self.responses[self.call_index]
        self.call_index += 1
        for event in events:
            yield event


class FailingRepositoryClient(LLMClient):
    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        raise RuntimeError("provider unavailable")
        yield TextDelta("unreachable")


def _repository_fixture(repository: Path, **overrides: object) -> RepositoryFixture:
    values: dict[str, object] = {
        "id": "simple-fix",
        "repository": repository,
        "issue": "Fix add() so the tests pass.",
        "test_command": f"{sys.executable} -m pytest test_calc.py -q",
        "regression_command": f"{sys.executable} -m pytest test_regression.py -q",
        "expected_tests": ("test_calc.py",),
        "allowed_paths": ("calc.py",),
        "forbidden_paths": ("pyproject.toml",),
    }
    values.update(overrides)
    return RepositoryFixture(**values)  # type: ignore[arg-type]


def _make_repository(root: Path) -> None:
    root.mkdir()
    (root / "calc.py").write_text(
        "def add(left, right):\n    return left - right\n", encoding="utf-8"
    )
    (root / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (root / "test_regression.py").write_text(
        "from calc import add\n\ndef test_zero():\n    assert add(4, 0) == 4\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_run_repository_case_drives_real_agent_without_changing_fixture(
    tmp_path: Path,
) -> None:
    source_repository = tmp_path / "fixture" / "repository"
    source_repository.parent.mkdir()
    _make_repository(source_repository)
    repository_root = tmp_path / "run"
    shutil.copytree(source_repository, repository_root)
    fixture = _repository_fixture(source_repository)
    client = ScriptedRepositoryClient(
        [
            [
                ToolCallComplete("read", "ReadFile", {"file_path": "calc.py"}),
                StreamEnd("end_turn", input_tokens=10, output_tokens=2),
            ],
            [
                ToolCallComplete(
                    "edit",
                    "EditFile",
                    {
                        "file_path": "calc.py",
                        "old_string": "return left - right",
                        "new_string": "return left + right",
                    },
                ),
                StreamEnd("end_turn", input_tokens=20, output_tokens=3),
            ],
            [
                ToolCallComplete(
                    "bash",
                    "Bash",
                    {"command": f"{sys.executable} -m pytest test_calc.py -q"},
                ),
                StreamEnd("end_turn", input_tokens=30, output_tokens=4),
            ],
            [
                TextDelta("Fixed and verified."),
                StreamEnd("end_turn", input_tokens=40, output_tokens=5),
            ],
        ]
    )
    factory_calls: list[tuple[Path, bool]] = []

    def client_factory(root: Path, evolved: bool) -> LLMClient:
        factory_calls.append((root, evolved))
        return client

    result = await run_repository_case(
        fixture,
        repository_root,
        evolved=True,
        candidate_skill="# Candidate\nUse a focused repair loop.",
        client_factory=client_factory,
        protocol="anthropic",
        max_iterations=5,
        test_timeout_seconds=10,
    )

    assert factory_calls == [(repository_root, True)]
    assert "return left - right" in (source_repository / "calc.py").read_text()
    assert "return left + right" in (repository_root / "calc.py").read_text()
    assert result["status"] == "completed"
    assert result["task_success"] is True
    assert result["tests_passed"] is True
    assert result["regression_free"] is True
    assert result["expected_tests_present"] is True
    assert result["changed_paths"] == ["calc.py"]
    assert result["tool_call_count"] == 3
    assert result["permission_denied"] == 0
    assert result["rewind_used"] is False
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 14
    assert result["final_text"] == "Fixed and verified."
    assert "Use a focused repair loop." in client.system_prompts[0]


@pytest.mark.asyncio
async def test_run_repository_case_classifies_provider_failure(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)

    result = await run_repository_case(
        _repository_fixture(repository),
        repository,
        evolved=False,
        candidate_skill="ignored",
        client_factory=lambda _root, _evolved: FailingRepositoryClient(),
        protocol="anthropic",
        max_iterations=2,
        test_timeout_seconds=10,
    )

    assert result["status"] == "provider-failed"
    assert result["error_type"] == "RuntimeError"
    assert result["error"] == "provider unavailable"
    assert result["task_success"] is False


@pytest.mark.asyncio
async def test_run_repository_case_rejects_out_of_scope_success(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    client = ScriptedRepositoryClient(
        [
            [
                ToolCallComplete(
                    "write",
                    "WriteFile",
                    {"file_path": "notes.txt", "content": "extra\n"},
                ),
                StreamEnd("end_turn", input_tokens=3, output_tokens=2),
            ],
            [TextDelta("Done."), StreamEnd("end_turn", input_tokens=4, output_tokens=1)],
        ]
    )

    result = await run_repository_case(
        _repository_fixture(
            repository,
            issue="Add notes without breaking tests.",
            regression_command="",
        ),
        repository,
        evolved=False,
        candidate_skill="",
        client_factory=lambda _root, _evolved: client,
        protocol="anthropic",
        max_iterations=3,
        test_timeout_seconds=10,
    )

    assert result["tests_passed"] is False
    assert result["out_of_scope_changes"] == ["notes.txt"]
    assert result["task_success"] is False


@pytest.mark.asyncio
async def test_run_repository_case_counts_dangerous_command_denial(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    client = ScriptedRepositoryClient(
        [
            [
                ToolCallComplete("danger", "Bash", {"command": "rm -rf /"}),
                StreamEnd("end_turn", input_tokens=2, output_tokens=1),
            ],
            [TextDelta("Denied."), StreamEnd("end_turn", input_tokens=2, output_tokens=1)],
        ]
    )

    result = await run_repository_case(
        _repository_fixture(repository),
        repository,
        evolved=False,
        candidate_skill="",
        client_factory=lambda _root, _evolved: client,
        protocol="anthropic",
        max_iterations=3,
        test_timeout_seconds=10,
    )

    assert result["permission_denied"] == 1
    assert result["tool_call_count"] == 1
