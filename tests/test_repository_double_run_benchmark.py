from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from mewcode.client import LLMClient, NetworkError
from mewcode.conversation import ConversationManager
from mewcode.evolution import repository_benchmark
from mewcode.evolution.repository_benchmark import (
    RepositoryFixture,
    analyze_repository_benchmark_failures,
    analyze_repository_route_impacts,
    compare_snapshots,
    load_repository_fixtures,
    promoted_route_families_from_benchmark,
    recompute_repository_task_router_policy,
    render_repository_benchmark_markdown,
    render_repository_benchmark_resume_summary,
    run_local_command,
    run_repository_case,
    run_repository_double_run_benchmark,
    snapshot_repository,
    summarize_repository_benchmark_metrics,
)
from scripts.run_repository_double_run_benchmark import _load_promoted_route_families
from mewcode.tools import create_default_registry
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete
from mewcode.tools.bash import Bash, Params as BashParams
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
async def test_bash_repeated_execution_avoids_asyncio_child_watcher(
    tmp_path: Path,
) -> None:
    bash = Bash(work_dir=tmp_path)

    for index in range(10):
        expected = f"run-{index}"
        result = await asyncio.wait_for(
            bash.execute(
                BashParams(
                    command=f"printf '{expected}' > output.txt; printf '{expected}'"
                )
            ),
            timeout=2,
        )

        assert not result.is_error
        assert result.output == f"STDOUT:\n{expected}"
        assert (tmp_path / "output.txt").read_text(encoding="utf-8") == expected


@pytest.mark.asyncio
async def test_bash_reports_subprocess_timeout_without_asyncio_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    async def reject_to_thread(*args: object, **kwargs: object) -> None:
        raise AssertionError("Bash must not use the asyncio executor")

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    monkeypatch.setattr(asyncio, "to_thread", reject_to_thread)

    result = await asyncio.wait_for(
        Bash(work_dir=tmp_path).execute(BashParams(command="blocked", timeout=7)),
        timeout=1,
    )

    assert result.is_error
    assert result.output == "Error: command timed out after 7s"


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


def test_bundled_repository_fixtures_include_distinct_failure_modes() -> None:
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures" / "repository_double_run"

    fixtures = load_repository_fixtures(fixture_root)
    by_id = {fixture.id: fixture for fixture in fixtures}
    slugify = by_id["slugify-punctuation"]
    full = run_local_command(slugify.test_command, slugify.repository, 10)
    regression = run_local_command(slugify.regression_command, slugify.repository, 10)

    assert set(by_id) == {"calculator-zero", "slugify-punctuation"}
    assert full["status"] == "failed"
    assert "2 failed, 2 passed" in full["stdout"]
    assert regression["status"] == "passed"
    assert "2 passed" in regression["stdout"]


def test_snapshot_repository_ignores_install_generated_metadata(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "package.egg-info").mkdir(parents=True)
    (tmp_path / "src" / "package.egg-info" / "PKG-INFO").write_text(
        "generated\n",
        encoding="utf-8",
    )
    (tmp_path / "build" / "lib").mkdir(parents=True)
    (tmp_path / "build" / "lib" / "module.py").write_text(
        "generated\n",
        encoding="utf-8",
    )
    (tmp_path / "sitecustomize.py").write_text("manual shim\n", encoding="utf-8")
    (tmp_path / "src" / "package.py").write_text("value = 1\n", encoding="utf-8")

    snapshot = snapshot_repository(tmp_path)

    assert snapshot == {
        "sitecustomize.py": "manual shim\n",
        "src/package.py": "value = 1\n",
    }


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


def test_compare_snapshots_counts_content_that_looks_like_diff_headers() -> None:
    result = compare_snapshots(
        {"notes.txt": "-- old\n"},
        {"notes.txt": "++ new\n"},
        allowed_paths=("notes.txt",),
    )

    assert result["patch_size"] == {"added": 1, "removed": 1, "total": 2}


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
        self.conversations: list[list[str]] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.system_prompts.append(system)
        self.conversations.append(
            [message.content for message in conversation.get_messages()]
        )
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
        raise NetworkError("provider unavailable")
        yield TextDelta("unreachable")


def _fix_calc_responses() -> list[list[StreamEvent]]:
    return [
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
        [TextDelta("Fixed."), StreamEnd("end_turn", input_tokens=30, output_tokens=4)],
    ]


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


def _write_calc_fixture(root: Path) -> Path:
    case = _write_repository_fixture(
        root,
        "simple-fix",
        expected={
            "test_command": f"{sys.executable} -m pytest test_calc.py -q",
            "regression_command": f"{sys.executable} -m pytest test_regression.py -q",
            "expected_tests": ["test_calc.py"],
            "allowed_paths": ["calc.py"],
            "forbidden_paths": ["pyproject.toml"],
        },
    )
    repository = case / "repository"
    (repository / "calc.py").write_text(
        "def add(left, right):\n    return left - right\n",
        encoding="utf-8",
    )
    (repository / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    (repository / "test_regression.py").write_text(
        "from calc import add\n\ndef test_zero():\n    assert add(4, 0) == 4\n",
        encoding="utf-8",
    )
    return case


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
                    {
                        "command": (
                            f'{sys.executable} -c "from calc import add; '
                            'assert add(2, 3) == 5"'
                        )
                    },
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
    assert result["model_call_count"] == 4
    assert result["permission_denied"] == 0
    assert result["rewind_used"] is False
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 14
    assert result["final_text"] == "Fixed and verified."
    assert any(
        "Use a focused repair loop." in message
        for message in client.conversations[0]
    )


@pytest.mark.asyncio
async def test_run_repository_case_includes_benchmark_constraints_in_task(
    tmp_path: Path,
) -> None:
    case = _write_calc_fixture(tmp_path / "fixtures")
    fixture = load_repository_fixtures(tmp_path / "fixtures")[0]
    client = ScriptedRepositoryClient([
        [TextDelta("Done."), StreamEnd("end_turn", input_tokens=3, output_tokens=2)]
    ])

    await run_repository_case(
        fixture,
        case / "repository",
        evolved=False,
        candidate_skill="",
        client_factory=lambda _root, _evolved: client,
        protocol="anthropic",
        max_iterations=1,
        test_timeout_seconds=10,
    )

    task = client.conversations[0][-1]
    assert "Target test command" in task
    assert fixture.test_command in task
    assert "Regression test command" in task
    assert fixture.regression_command in task
    assert "Only these path patterns are allowed to change" in task
    assert "`calc.py`" in task
    assert "These path patterns must not change" in task
    assert "`pyproject.toml`" in task


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
    assert result["error_type"] == "NetworkError"
    assert result["error"] == "provider unavailable"
    assert result["task_success"] is False


@pytest.mark.asyncio
async def test_run_repository_case_classifies_runner_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)

    def fail_agent_construction(*args: object, **kwargs: object) -> object:
        raise RuntimeError("runner setup failed")

    monkeypatch.setattr(repository_benchmark, "Agent", fail_agent_construction)

    result = await run_repository_case(
        _repository_fixture(repository),
        repository,
        evolved=False,
        candidate_skill="",
        client_factory=lambda _root, _evolved: ScriptedRepositoryClient([]),
        protocol="anthropic",
        max_iterations=2,
        test_timeout_seconds=10,
    )

    assert result["status"] == "runner-failed"
    assert result["error_type"] == "RuntimeError"
    assert result["error"] == "runner setup failed"
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


@pytest.mark.asyncio
async def test_double_run_compares_baseline_and_evolved_without_mutating_fixture(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    case = _write_calc_fixture(fixture_root)
    calls: list[tuple[str, bool]] = []

    def client_factory(root: Path, evolved: bool) -> LLMClient:
        calls.append((root.name, evolved))
        return ScriptedRepositoryClient(_fix_calc_responses())

    result = await run_repository_double_run_benchmark(
        client_factory,
        fixture_root,
        "# Candidate\nUse a focused repair loop.",
        workspace_root=tmp_path / "workspace",
        max_iterations=5,
        test_timeout_seconds=10,
    )

    assert calls == [("baseline", False), ("evolved", True)]
    assert result["summary"]["case_count"] == 1
    assert result["summary"]["baseline_success"] == 1
    assert result["summary"]["evolved_success"] == 1
    assert result["metrics"]["baseline_success_rate"] == 100.0
    assert result["metrics"]["evolved_success_rate"] == 100.0
    assert result["metrics"]["success_rate_lift_percentage_points"] == 0.0
    assert result["configuration"]["automatic_promotion"] is False
    assert result["cases"][0]["baseline"]["task_success"] is True
    assert result["cases"][0]["evolved"]["task_success"] is True
    assert result["cases"][0]["delta"]["tests_passed"] == 0
    assert "return left - right" in (
        case / "repository" / "calc.py"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_double_run_can_give_evolved_runs_extra_iterations(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    _write_calc_fixture(fixture_root)

    def client_factory(_root: Path, _evolved: bool) -> LLMClient:
        return ScriptedRepositoryClient(_fix_calc_responses())

    result = await run_repository_double_run_benchmark(
        client_factory,
        fixture_root,
        "# Candidate\nUse the extra budget to finish verification.",
        workspace_root=tmp_path / "workspace",
        max_iterations=1,
        evolved_max_iterations=5,
        test_timeout_seconds=10,
    )

    assert result["configuration"]["max_iterations"] == 1
    assert result["configuration"]["evolved_max_iterations"] == 5
    assert result["summary"]["baseline_success"] == 0
    assert result["summary"]["evolved_success"] == 1
    assert result["metrics"]["task_success_lift_count"] == 1
    assert result["metrics"]["canary_gate_passed"] is True


@pytest.mark.asyncio
async def test_double_run_can_reuse_existing_baseline_results(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    _write_calc_fixture(fixture_root)
    calls: list[tuple[str, bool]] = []

    def client_factory(root: Path, evolved: bool) -> LLMClient:
        calls.append((root.name, evolved))
        return ScriptedRepositoryClient(_fix_calc_responses())

    result = await run_repository_double_run_benchmark(
        client_factory,
        fixture_root,
        "# Candidate\nReuse the stable baseline and test this candidate only.",
        workspace_root=tmp_path / "workspace",
        max_iterations=5,
        test_timeout_seconds=10,
        reuse_baseline_result={
            "cases": [{
                "id": "simple-fix",
                "baseline": {
                    "status": "completed",
                    "task_success": False,
                    "tests_passed": False,
                    "regression_free": True,
                    "expected_tests_present": True,
                    "changed_paths": [],
                    "out_of_scope_changes": [],
                    "forbidden_changes": [],
                    "patch_size": {"added": 0, "removed": 0, "total": 0},
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "elapsed_seconds": 2.0,
                    "tool_call_count": 1,
                    "permission_denied": 0,
                    "rewind_used": False,
                },
            }],
        },
    )

    assert calls == [("evolved", True)]
    assert result["summary"]["baseline_runs_executed"] == 0
    assert result["summary"]["evolved_runs_executed"] == 1
    assert result["summary"]["agent_runs_executed"] == 1
    assert result["summary"]["reused_baseline_cases"] == 1
    assert result["configuration"]["baseline_reuse_enabled"] is True
    assert result["cases"][0]["baseline"]["input_tokens"] == 11
    assert result["summary"]["baseline_success"] == 0
    assert result["summary"]["evolved_success"] == 1
    assert result["metrics"]["executed_run_count"] == 1
    assert result["metrics"]["provider_run_reduction_rate"] == 50.0
    assert result["metrics"]["canary_gate_passed"] is True


@pytest.mark.asyncio
async def test_double_run_can_filter_specific_case_ids(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    _write_calc_fixture(fixture_root)
    second = _write_calc_fixture(fixture_root / "nested")
    shutil.move(str(second), fixture_root / "second-fix")
    shutil.rmtree(fixture_root / "nested")
    calls: list[tuple[str, bool]] = []

    def client_factory(root: Path, evolved: bool) -> LLMClient:
        calls.append((root.name, evolved))
        return ScriptedRepositoryClient(_fix_calc_responses())

    result = await run_repository_double_run_benchmark(
        client_factory,
        fixture_root,
        "# Candidate\nRun only the selected case.",
        workspace_root=tmp_path / "workspace",
        max_iterations=5,
        test_timeout_seconds=10,
        case_ids=("second-fix",),
        reuse_baseline_result={
            "cases": [{
                "id": "second-fix",
                "baseline": {
                    "status": "completed",
                    "task_success": False,
                    "tests_passed": False,
                    "regression_free": True,
                    "expected_tests_present": True,
                    "changed_paths": [],
                    "out_of_scope_changes": [],
                    "forbidden_changes": [],
                    "patch_size": {"added": 0, "removed": 0, "total": 0},
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "elapsed_seconds": 0.0,
                },
            }],
        },
    )

    assert [case["id"] for case in result["cases"]] == ["second-fix"]
    assert calls == [("evolved", True)]
    assert result["summary"]["fixture_case_count"] == 2
    assert result["metrics"]["selected_case_reduction_rate"] == 50.0
    assert result["metrics"]["full_run_count"] == 4
    assert result["metrics"]["executed_run_count"] == 1
    assert result["metrics"]["full_provider_run_reduction_rate"] == 75.0


@pytest.mark.asyncio
async def test_double_run_rejects_unknown_case_id(tmp_path: Path) -> None:
    fixture_root = tmp_path / "fixtures"
    _write_calc_fixture(fixture_root)

    with pytest.raises(ValueError, match=r"case id\(s\) not found: missing-case"):
        await run_repository_double_run_benchmark(
            lambda _root, _evolved: ScriptedRepositoryClient(_fix_calc_responses()),
            fixture_root,
            "# Candidate\nNo-op.",
            workspace_root=tmp_path / "workspace",
            case_ids=("missing-case",),
        )


@pytest.mark.asyncio
async def test_double_run_strategy_router_adds_evolved_hint_only(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    case = _write_calc_fixture(fixture_root)
    (case / "expected.json").write_text(
        json.dumps({
            "test_command": f"{sys.executable} -m pytest test_calc.py -q",
            "regression_command": f"{sys.executable} -m pytest test_regression.py -q",
            "expected_tests": ["test_calc.py"],
            "allowed_paths": ["sympy/printing/ccode.py"],
            "forbidden_paths": ["pyproject.toml"],
        }),
        encoding="utf-8",
    )
    clients: list[ScriptedRepositoryClient] = []

    def client_factory(_root: Path, _evolved: bool) -> LLMClient:
        client = ScriptedRepositoryClient([
            [TextDelta("Done."), StreamEnd("end_turn", input_tokens=1, output_tokens=1)]
        ])
        clients.append(client)
        return client

    result = await run_repository_double_run_benchmark(
        client_factory,
        fixture_root,
        "# Candidate\nUse routed guidance.",
        workspace_root=tmp_path / "workspace",
        max_iterations=1,
        test_timeout_seconds=10,
        strategy_router_enabled=True,
    )

    assert result["configuration"]["strategy_router_enabled"] is True
    assert len(clients) == 2
    baseline_task = clients[0].conversations[0][-1]
    evolved_task = clients[1].conversations[0][-1]
    assert "Strategy router hint" not in baseline_task
    assert "Strategy router hint" in evolved_task
    assert "SymPy printer task" in evolved_task


@pytest.mark.asyncio
async def test_double_run_task_router_injects_family_skill_only_for_evolved(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    case = _write_calc_fixture(fixture_root)
    (case / "expected.json").write_text(
        json.dumps({
            "test_command": f"{sys.executable} -m pytest test_calc.py -q",
            "regression_command": f"{sys.executable} -m pytest test_regression.py -q",
            "expected_tests": ["sympy/printing/tests/test_ccode.py"],
            "allowed_paths": ["sympy/printing/ccode.py"],
            "forbidden_paths": ["pyproject.toml"],
        }),
        encoding="utf-8",
    )
    clients: list[ScriptedRepositoryClient] = []

    def client_factory(_root: Path, _evolved: bool) -> LLMClient:
        client = ScriptedRepositoryClient([
            [TextDelta("Done."), StreamEnd("end_turn", input_tokens=1, output_tokens=1)]
        ])
        clients.append(client)
        return client

    result = await run_repository_double_run_benchmark(
        client_factory,
        fixture_root,
        "# Candidate\nGLOBAL SHOULD NOT BE USED BY ROUTER.",
        workspace_root=tmp_path / "workspace",
        max_iterations=1,
        test_timeout_seconds=10,
        task_router_enabled=True,
    )

    assert result["configuration"]["task_router_enabled"] is True
    assert result["configuration"]["task_router_short_circuit_skips"] is True
    assert result["configuration"]["candidate_skill_injected"] is True
    assert result["summary"]["task_router_injections"] == 1
    assert result["summary"]["task_router_skips"] == 0
    assert result["summary"]["task_router_short_circuits"] == 0
    assert result["cases"][0]["task_route"] == {
        "family": "sympy_printing",
        "action": "inject",
        "skill_name": "sympy_printing_repair",
        "hint": (
            "Task router selected `sympy_printing_repair` for family `sympy_printing`. "
            "Follow this family-specific Skill over generic repository repair habits."
        ),
        "reason": "fixture issue, test command, expected tests, and allowed paths matched route signals",
    }
    assert len(clients) == 2
    baseline_messages = "\n".join(clients[0].conversations[0])
    evolved_messages = "\n".join(clients[1].conversations[0])
    assert "sympy_printing_repair" not in baseline_messages
    assert "GLOBAL SHOULD NOT BE USED BY ROUTER" in evolved_messages
    assert "sympy_printing_repair" in evolved_messages
    assert "SymPy printer repair tasks" in evolved_messages
    assert "Keep the generic safety, scope, and verification rules" in evolved_messages


@pytest.mark.asyncio
async def test_double_run_task_router_short_circuits_unknown_family_to_baseline(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    _write_calc_fixture(fixture_root)
    clients: list[ScriptedRepositoryClient] = []

    def client_factory(_root: Path, _evolved: bool) -> LLMClient:
        client = ScriptedRepositoryClient([
            [TextDelta("Done."), StreamEnd("end_turn", input_tokens=1, output_tokens=1)]
        ])
        clients.append(client)
        return client

    result = await run_repository_double_run_benchmark(
        client_factory,
        fixture_root,
        "# Candidate\nGLOBAL SHOULD BE SKIPPED.",
        workspace_root=tmp_path / "workspace",
        max_iterations=1,
        test_timeout_seconds=10,
        task_router_enabled=True,
    )

    assert result["summary"]["task_router_injections"] == 0
    assert result["summary"]["task_router_skips"] == 1
    assert result["summary"]["task_router_short_circuits"] == 1
    assert result["summary"]["evolved_runs_executed"] == 0
    assert result["summary"]["agent_runs_executed"] == 1
    assert result["configuration"]["candidate_skill_injected"] is False
    assert result["configuration"]["task_router_short_circuits"] == 1
    assert result["cases"][0]["task_route"]["family"] == "unknown"
    assert result["cases"][0]["task_route"]["action"] == "skip"
    assert len(clients) == 1
    assert result["cases"][0]["evolved"]["status"] == "skipped"
    assert result["cases"][0]["evolved"]["agent_run_skipped"] is True
    assert result["cases"][0]["evolved"]["selected_policy"] == "baseline"
    assert result["cases"][0]["evolved"]["task_success"] == result["cases"][0]["baseline"]["task_success"]
    assert result["metrics"]["task_router_short_circuit_count"] == 1
    assert result["metrics"]["provider_run_reduction_rate"] == 50.0


@pytest.mark.asyncio
async def test_double_run_task_router_can_rerun_skipped_routes(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    _write_calc_fixture(fixture_root)
    clients: list[ScriptedRepositoryClient] = []

    def client_factory(_root: Path, _evolved: bool) -> LLMClient:
        client = ScriptedRepositoryClient([
            [TextDelta("Done."), StreamEnd("end_turn", input_tokens=1, output_tokens=1)]
        ])
        clients.append(client)
        return client

    result = await run_repository_double_run_benchmark(
        client_factory,
        fixture_root,
        "# Candidate\nGLOBAL SHOULD BE SKIPPED.",
        workspace_root=tmp_path / "workspace",
        max_iterations=1,
        test_timeout_seconds=10,
        task_router_enabled=True,
        task_router_short_circuit_skips=False,
    )

    assert result["summary"]["task_router_skips"] == 1
    assert result["summary"]["task_router_short_circuits"] == 0
    assert result["summary"]["evolved_runs_executed"] == 1
    assert len(clients) == 2
    evolved_messages = "\n".join(clients[1].conversations[0])
    assert "GLOBAL SHOULD BE SKIPPED" not in evolved_messages
    assert "no high-confidence family skill" in evolved_messages


@pytest.mark.asyncio
async def test_double_run_task_router_skips_unpromoted_family_skill(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    case = _write_calc_fixture(fixture_root)
    (case / "expected.json").write_text(
        json.dumps({
            "test_command": f"{sys.executable} -m pytest test_calc.py -q",
            "regression_command": f"{sys.executable} -m pytest test_regression.py -q",
            "expected_tests": ["sympy/printing/tests/test_ccode.py"],
            "allowed_paths": ["sympy/printing/ccode.py"],
            "forbidden_paths": ["pyproject.toml"],
        }),
        encoding="utf-8",
    )
    clients: list[ScriptedRepositoryClient] = []

    def client_factory(_root: Path, _evolved: bool) -> LLMClient:
        client = ScriptedRepositoryClient([
            [TextDelta("Done."), StreamEnd("end_turn", input_tokens=1, output_tokens=1)]
        ])
        clients.append(client)
        return client

    result = await run_repository_double_run_benchmark(
        client_factory,
        fixture_root,
        "# Candidate\nGLOBAL SHOULD BE GATED.",
        workspace_root=tmp_path / "workspace",
        max_iterations=1,
        test_timeout_seconds=10,
        task_router_enabled=True,
        task_router_promoted_families=("sympy_functions",),
    )

    assert result["configuration"]["task_router_promoted_families"] == ["sympy_functions"]
    assert result["configuration"]["candidate_skill_injected"] is False
    assert result["summary"]["task_router_injections"] == 0
    assert result["summary"]["task_router_skips"] == 1
    assert result["summary"]["task_router_short_circuits"] == 1
    assert len(clients) == 1
    assert result["cases"][0]["task_route"] == {
        "family": "sympy_printing",
        "action": "skip",
        "skill_name": "sympy_printing_repair",
        "hint": (
            "Task router matched `sympy_printing_repair` for family `sympy_printing`, "
            "but this family Skill is not in the promoted route set. Do not inject the "
            "candidate Skill; rely on the baseline repository repair prompt."
        ),
        "reason": "routed family skill not promoted by task-router gate",
    }
    assert result["cases"][0]["evolved"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_double_run_marks_out_of_scope_change(tmp_path: Path) -> None:
    fixture_root = tmp_path / "fixtures"
    _write_calc_fixture(fixture_root)

    def client_factory(_root: Path, _evolved: bool) -> LLMClient:
        return ScriptedRepositoryClient([
            [
                ToolCallComplete(
                    "write",
                    "WriteFile",
                    {"file_path": "notes.md", "content": "extra\n"},
                ),
                StreamEnd("end_turn", input_tokens=3, output_tokens=2),
            ],
            [TextDelta("Done."), StreamEnd("end_turn", input_tokens=4, output_tokens=1)],
        ])

    result = await run_repository_double_run_benchmark(
        client_factory,
        fixture_root,
        "# Candidate\nNo-op.",
        workspace_root=tmp_path / "workspace",
        max_iterations=3,
        test_timeout_seconds=10,
    )

    assert result["cases"][0]["baseline"]["out_of_scope_changes"] == ["notes.md"]
    assert result["cases"][0]["baseline"]["task_success"] is False
    assert result["summary"]["out_of_scope_case_count"] == 1


def test_summarize_repository_benchmark_metrics_calculates_rates_and_deltas() -> None:
    metrics = summarize_repository_benchmark_metrics({
        "summary": {
            "case_count": 2,
            "baseline_success": 0,
            "evolved_success": 1,
            "evolved_regression_free": 2,
            "provider_failed": 1,
            "runner_failed": 0,
            "test_timeouts": 1,
            "out_of_scope_case_count": 1,
            "baseline_out_of_scope_case_count": 0,
            "evolved_out_of_scope_case_count": 1,
            "baseline_input_tokens": 100,
            "evolved_input_tokens": 130,
            "baseline_output_tokens": 50,
            "evolved_output_tokens": 40,
            "baseline_model_call_count": 10,
            "evolved_model_call_count": 9,
            "baseline_tool_call_count": 20,
            "evolved_tool_call_count": 18,
            "baseline_average_elapsed_seconds": 10.0,
            "evolved_average_elapsed_seconds": 8.5,
        },
        "cases": [
            {"delta": {"tests_passed": 1, "patch_size_total": 4}},
            {"delta": {"tests_passed": 0, "patch_size_total": -1}},
        ],
    })

    assert metrics["run_count"] == 4
    assert metrics["baseline_success_rate"] == 0.0
    assert metrics["evolved_success_rate"] == 50.0
    assert metrics["success_rate_lift_percentage_points"] == 50.0
    assert metrics["task_success_lift_count"] == 1
    assert metrics["tests_passed_lift_count"] == 1
    assert metrics["evolved_regression_free_rate"] == 100.0
    assert metrics["out_of_scope_case_rate"] == 50.0
    assert metrics["baseline_out_of_scope_case_rate"] == 0.0
    assert metrics["evolved_out_of_scope_case_rate"] == 50.0
    assert metrics["provider_failure_run_rate"] == 25.0
    assert metrics["test_timeout_run_rate"] == 25.0
    assert metrics["canary_gate_passed"] is False
    assert "evolved out-of-scope changes present" in metrics["canary_gate_reason"]
    assert metrics["runtime_efficiency_gate_passed"] is False
    assert "canary gate did not pass" in metrics["runtime_efficiency_gate_reason"]
    assert metrics["input_token_delta_total"] == 30
    assert metrics["input_token_delta_rate"] == 30.0
    assert metrics["output_token_delta_total"] == -10
    assert metrics["output_token_delta_rate"] == -20.0
    assert metrics["total_token_delta_total"] == 20
    assert metrics["total_token_delta_rate"] == 13.33
    assert metrics["model_call_delta_total"] == -1
    assert metrics["model_call_delta_rate"] == -10.0
    assert metrics["tool_call_delta_total"] == -2
    assert metrics["tool_call_delta_rate"] == -10.0
    assert metrics["average_elapsed_delta_seconds"] == -1.5
    assert metrics["average_elapsed_delta_rate"] == -15.0
    assert metrics["patch_size_delta_total"] == 3


def test_summarize_repository_benchmark_metrics_marks_clean_canary_pass() -> None:
    metrics = summarize_repository_benchmark_metrics({
        "summary": {
            "case_count": 2,
            "baseline_success": 0,
            "evolved_success": 1,
            "evolved_regression_free": 2,
            "provider_failed": 0,
            "runner_failed": 0,
            "test_timeouts": 0,
            "out_of_scope_case_count": 0,
            "baseline_out_of_scope_case_count": 0,
            "evolved_out_of_scope_case_count": 0,
            "baseline_input_tokens": 100,
            "evolved_input_tokens": 90,
            "baseline_output_tokens": 50,
            "evolved_output_tokens": 40,
            "baseline_model_call_count": 10,
            "evolved_model_call_count": 8,
            "baseline_tool_call_count": 20,
            "evolved_tool_call_count": 18,
            "baseline_average_elapsed_seconds": 10.0,
            "evolved_average_elapsed_seconds": 9.0,
        },
        "cases": [],
    })

    assert metrics["canary_gate_passed"] is True
    assert metrics["canary_gate_reason"] == "positive lift with clean evolved runs"
    assert metrics["runtime_efficiency_gate_passed"] is True


def test_summarize_repository_benchmark_metrics_fails_runtime_gate_on_token_growth() -> None:
    metrics = summarize_repository_benchmark_metrics({
        "summary": {
            "case_count": 2,
            "baseline_success": 0,
            "evolved_success": 1,
            "evolved_regression_free": 2,
            "provider_failed": 0,
            "runner_failed": 0,
            "test_timeouts": 0,
            "out_of_scope_case_count": 0,
            "baseline_out_of_scope_case_count": 0,
            "evolved_out_of_scope_case_count": 0,
            "baseline_input_tokens": 100,
            "evolved_input_tokens": 120,
            "baseline_output_tokens": 50,
            "evolved_output_tokens": 45,
            "baseline_model_call_count": 10,
            "evolved_model_call_count": 9,
            "baseline_tool_call_count": 20,
            "evolved_tool_call_count": 19,
            "baseline_average_elapsed_seconds": 10.0,
            "evolved_average_elapsed_seconds": 9.0,
        },
        "cases": [],
    })

    assert metrics["canary_gate_passed"] is True
    assert metrics["runtime_efficiency_gate_passed"] is False
    assert "total tokens increased" in metrics["runtime_efficiency_gate_reason"]


def test_summarize_repository_benchmark_metrics_derives_side_scope_for_old_json() -> None:
    metrics = summarize_repository_benchmark_metrics({
        "summary": {
            "case_count": 1,
            "baseline_success": 0,
            "evolved_success": 1,
            "evolved_regression_free": 1,
            "provider_failed": 0,
            "runner_failed": 0,
            "test_timeouts": 0,
            "out_of_scope_case_count": 1,
        },
        "cases": [{
            "baseline": {"out_of_scope_changes": []},
            "evolved": {"out_of_scope_changes": ["tests/test_parser.py"]},
            "delta": {},
        }],
    })

    assert metrics["baseline_out_of_scope_case_rate"] == 0.0
    assert metrics["evolved_out_of_scope_case_rate"] == 100.0
    assert metrics["canary_gate_passed"] is False


def test_analyze_repository_benchmark_failures_classifies_actionable_buckets() -> None:
    taxonomy = analyze_repository_benchmark_failures({
        "cases": [
            {
                "id": "swebench_sympy__sympy-11400",
                "source_repository": "fixtures/sympy",
                "baseline": {
                    "task_success": True,
                    "changed_paths": ["sympy/printing/ccode.py"],
                },
                "evolved": {
                    "task_success": False,
                    "tests_passed": False,
                    "regression_free": True,
                    "expected_tests_present": True,
                    "changed_paths": [],
                    "out_of_scope_changes": [],
                    "forbidden_changes": [],
                    "test_result": {
                        "stdout": "sympy/printing/tests/test_ccode.py::test_ccode_sinc failed",
                        "stderr": "",
                    },
                },
            },
            {
                "id": "swebench_sympy__sympy-13031",
                "baseline": {"task_success": False, "tests_passed": False},
                "evolved": {
                    "task_success": False,
                    "tests_passed": False,
                    "regression_free": True,
                    "expected_tests_present": True,
                    "changed_paths": ["sympy/matrices/sparse.py"],
                    "out_of_scope_changes": [],
                    "forbidden_changes": [],
                },
            },
        ],
    })

    assert taxonomy["outcome_counts"] == {"both_failed": 1, "regression": 1}
    assert taxonomy["task_family_counts"] == {
        "sympy_matrices": 1,
        "sympy_printing": 1,
    }
    assert taxonomy["evolved_failure_type_counts"] == {
        "tests_failed_no_patch": 1,
        "tests_failed_with_patch": 1,
    }
    assert taxonomy["targeted_case_ids"]["regression"] == [
        "swebench_sympy__sympy-11400"
    ]
    assert taxonomy["targeted_case_ids"]["evolved_patch_failed_tests"] == [
        "swebench_sympy__sympy-13031"
    ]


def _route_impact_fixture_result() -> dict[str, object]:
    return {
        "cases": [
            {
                "id": "printing-lift",
                "task_route": {
                    "family": "sympy_printing",
                    "action": "inject",
                    "skill_name": "sympy_printing_repair",
                },
                "baseline": {"task_success": False},
                "evolved": {
                    "task_success": True,
                    "regression_free": True,
                    "out_of_scope_changes": [],
                    "status": "completed",
                },
                "delta": {
                    "task_success": 1,
                    "model_call_count": -1,
                    "tool_call_count": 1,
                    "input_tokens": 10,
                    "output_tokens": -30,
                    "elapsed_seconds": -5.0,
                    "patch_size_total": 2,
                },
            },
            {
                "id": "printing-both-success",
                "task_route": {
                    "family": "sympy_printing",
                    "action": "inject",
                    "skill_name": "sympy_printing_repair",
                },
                "baseline": {"task_success": True},
                "evolved": {
                    "task_success": True,
                    "regression_free": True,
                    "out_of_scope_changes": [],
                    "status": "completed",
                },
                "delta": {
                    "task_success": 0,
                    "model_call_count": 0,
                    "tool_call_count": 0,
                    "input_tokens": -1,
                    "output_tokens": -1,
                    "elapsed_seconds": -1.0,
                    "patch_size_total": 0,
                },
            },
            {
                "id": "matrix-regression",
                "task_route": {
                    "family": "sympy_matrices",
                    "action": "inject",
                    "skill_name": "sympy_matrix_repair",
                },
                "baseline": {"task_success": True},
                "evolved": {
                    "task_success": False,
                    "regression_free": True,
                    "out_of_scope_changes": [],
                    "status": "completed",
                },
                "delta": {
                    "task_success": -1,
                    "model_call_count": 1,
                    "tool_call_count": 0,
                    "input_tokens": 5,
                    "output_tokens": 5,
                    "elapsed_seconds": 2.0,
                    "patch_size_total": 1,
                },
            },
            {
                "id": "unknown-skip",
                "task_route": {
                    "family": "unknown",
                    "action": "skip",
                    "skill_name": "",
                },
                "baseline": {"task_success": True},
                "evolved": {
                    "task_success": True,
                    "regression_free": True,
                    "out_of_scope_changes": [],
                    "status": "skipped",
                    "agent_run_skipped": True,
                },
                "delta": {
                    "task_success": 0,
                    "model_call_count": 0,
                    "tool_call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "elapsed_seconds": 0.0,
                    "patch_size_total": 0,
                },
            },
        ],
    }


def test_analyze_repository_route_impacts_recommends_promoted_families() -> None:
    impacts = analyze_repository_route_impacts(_route_impact_fixture_result())

    assert impacts["promoted_families"] == ["sympy_printing"]
    by_family = {row["family"]: row for row in impacts["families"]}
    assert by_family["sympy_printing"]["case_count"] == 2
    assert by_family["sympy_printing"]["injected_case_count"] == 2
    assert by_family["sympy_printing"]["baseline_success_rate"] == 50.0
    assert by_family["sympy_printing"]["evolved_success_rate"] == 100.0
    assert by_family["sympy_printing"]["task_success_lift_count"] == 1
    assert by_family["sympy_printing"]["promotion_recommended"] is True
    assert by_family["sympy_printing"]["total_token_delta_total"] == -22
    assert by_family["sympy_matrices"]["promotion_recommended"] is False
    assert "route caused task regression" in by_family["sympy_matrices"]["promotion_reason"]
    assert by_family["unknown"]["short_circuit_count"] == 1


def test_promoted_route_families_can_require_runtime_efficiency() -> None:
    result = _route_impact_fixture_result()

    assert promoted_route_families_from_benchmark(result) == ["sympy_printing"]
    assert promoted_route_families_from_benchmark(
        result,
        require_runtime_efficiency=True,
    ) == []


def test_recompute_repository_task_router_policy_short_circuits_unpromoted_routes() -> None:
    recomputed = recompute_repository_task_router_policy(
        _route_impact_fixture_result(),
        promoted_families=("sympy_printing",),
    )

    assert recomputed["method"] == "repository_task_router_policy_recompute"
    assert recomputed["configuration"]["policy_recompute"] is True
    assert recomputed["configuration"]["task_router_promoted_families"] == [
        "sympy_printing"
    ]
    assert recomputed["summary"]["baseline_success"] == 3
    assert recomputed["summary"]["evolved_success"] == 4
    assert recomputed["summary"]["task_router_injections"] == 2
    assert recomputed["summary"]["task_router_skips"] == 2
    assert recomputed["summary"]["task_router_short_circuits"] == 2
    assert recomputed["metrics"]["success_rate_lift_percentage_points"] == 25.0
    matrix_case = next(
        case for case in recomputed["cases"] if case["id"] == "matrix-regression"
    )
    assert matrix_case["task_route"]["action"] == "skip"
    assert matrix_case["evolved"]["status"] == "skipped"
    assert matrix_case["evolved"]["task_success"] is True
    assert matrix_case["delta"]["task_success"] == 0


def test_cli_helper_loads_promoted_route_families_from_json(tmp_path: Path) -> None:
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(
        json.dumps(_route_impact_fixture_result()),
        encoding="utf-8",
    )

    promoted = _load_promoted_route_families(
        ["sympy_printing"],
        [str(benchmark)],
        min_cases=1,
        require_runtime_efficiency=False,
    )

    assert promoted == ["sympy_printing"]


def test_render_repository_benchmark_markdown_exposes_side_by_side_metrics() -> None:
    report = render_repository_benchmark_markdown({
        "summary": {
            "case_count": 1,
            "baseline_success": 0,
            "evolved_success": 1,
            "provider_failed": 0,
            "evolved_regression_free": 1,
        },
        "cases": [{
            "id": "calculator-zero",
            "baseline": {"task_success": False, "tests_passed": False},
            "evolved": {"task_success": True, "tests_passed": True},
            "delta": {"task_success": 1, "patch_size_total": 2},
        }],
        "configuration": {"automatic_promotion": False},
    })

    assert "Baseline" in report and "Evolved" in report
    assert "Quantitative Metrics" in report
    assert "Failure Taxonomy" in report
    assert "Outcome Counts" in report
    assert "Success lift: `+100.00 pp` / `+1 cases`" in report
    assert "Patch-size delta: `+2 lines`" in report
    assert "calculator-zero" in report
    assert "automatic_promotion: false" in report


def test_render_repository_benchmark_markdown_exposes_route_impacts() -> None:
    result = _route_impact_fixture_result()
    result["route_impacts"] = analyze_repository_route_impacts(result)  # type: ignore[index]

    report = render_repository_benchmark_markdown(result)  # type: ignore[arg-type]

    assert "Route Family Impact" in report
    assert "| sympy_printing | 2 | 2 | 0 | 50.00% | 100.00% | +50.00 pp" in report
    assert "Promoted route families: `sympy_printing`" in report


def test_render_repository_benchmark_resume_summary_is_copy_ready() -> None:
    summary = render_repository_benchmark_resume_summary({
        "summary": {
            "case_count": 2,
            "baseline_success": 0,
            "evolved_success": 1,
            "evolved_regression_free": 2,
            "provider_failed": 1,
            "baseline_input_tokens": 100,
            "evolved_input_tokens": 120,
            "baseline_output_tokens": 50,
            "evolved_output_tokens": 45,
            "baseline_average_elapsed_seconds": 10.0,
            "evolved_average_elapsed_seconds": 9.0,
        },
        "cases": [{"delta": {"patch_size_total": 2}}],
    })

    assert "Repository double-run benchmark impact summary" in summary
    assert "0.00% to 50.00% (+50.00 pp, +1 case(s))" in summary
    assert "Evolved regression-free rate: 100.00%" in summary
    assert "provider 25.00%" in summary
    assert "+20 input tokens" in summary
    assert "fake-client runs validate the harness only" in summary


def test_repository_benchmark_cli_renders_existing_json_without_provider(
    tmp_path: Path,
) -> None:
    json_input = tmp_path / "benchmark.json"
    json_output = tmp_path / "with-metrics.json"
    md_output = tmp_path / "benchmark.md"
    summary_output = tmp_path / "summary.txt"
    json_input.write_text(
        json.dumps({
            "method": "repository_double_run",
            "fixture_root": "fixtures/repository_double_run",
            "summary": {
                "case_count": 1,
                "baseline_success": 0,
                "evolved_success": 1,
                "evolved_regression_free": 1,
            },
            "cases": [{
                "id": "calculator-zero",
                "baseline": {"task_success": False, "tests_passed": False},
                "evolved": {"task_success": True, "tests_passed": True},
                "delta": {"task_success": 1, "patch_size_total": 2},
            }],
            "configuration": {"automatic_promotion": False},
        }),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_repository_double_run_benchmark.py",
            "--from-json",
            str(json_input),
            "--json-output",
            str(json_output),
            "--md-output",
            str(md_output),
            "--summary-output",
            str(summary_output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    rendered = md_output.read_text(encoding="utf-8")
    enriched = json.loads(json_output.read_text(encoding="utf-8"))
    summary = summary_output.read_text(encoding="utf-8")
    assert "Quantitative Metrics" in rendered
    assert "Success lift: `+100.00 pp` / `+1 cases`" in rendered
    assert enriched["metrics"]["evolved_success_rate"] == 100.0
    assert "Task success improved from 0.00% to 100.00%" in summary


def test_repository_benchmark_cli_recomputes_task_router_policy(
    tmp_path: Path,
) -> None:
    json_input = tmp_path / "benchmark.json"
    json_output = tmp_path / "recomputed.json"
    md_output = tmp_path / "recomputed.md"
    json_input.write_text(
        json.dumps(_route_impact_fixture_result()),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_repository_double_run_benchmark.py",
            "--from-json",
            str(json_input),
            "--recompute-task-router-policy",
            "--json-output",
            str(json_output),
            "--md-output",
            str(md_output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    recomputed = json.loads(json_output.read_text(encoding="utf-8"))
    rendered = md_output.read_text(encoding="utf-8")
    assert recomputed["method"] == "repository_task_router_policy_recompute"
    assert recomputed["configuration"]["task_router_promoted_families"] == [
        "sympy_printing"
    ]
    assert recomputed["summary"]["evolved_success"] == 4
    assert recomputed["summary"]["task_router_short_circuits"] == 2
    assert "Route Family Impact" in rendered
    assert "policy_recompute: true" in rendered


def test_repository_benchmark_cli_from_json_writes_failure_taxonomy_buckets(
    tmp_path: Path,
) -> None:
    json_input = tmp_path / "benchmark.json"
    json_output = tmp_path / "selected.json"
    json_input.write_text(
        json.dumps({
            "method": "repository_double_run",
            "fixture_root": "fixtures/repository_double_run",
            "summary": {
                "case_count": 2,
                "baseline_success": 1,
                "evolved_success": 1,
                "evolved_regression_free": 2,
            },
            "cases": [
                {
                    "id": "calculator-zero",
                    "baseline": {"task_success": True},
                    "evolved": {
                        "task_success": False,
                        "tests_passed": False,
                        "regression_free": True,
                        "expected_tests_present": True,
                        "changed_paths": [],
                        "out_of_scope_changes": [],
                        "forbidden_changes": [],
                    },
                    "delta": {"task_success": -1},
                },
                {
                    "id": "slugify-punctuation",
                    "baseline": {"task_success": False},
                    "evolved": {"task_success": True, "tests_passed": True},
                    "delta": {"task_success": 1},
                },
            ],
            "configuration": {"automatic_promotion": False},
        }),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_repository_double_run_benchmark.py",
            "--from-json",
            str(json_input),
            "--json-output",
            str(json_output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    enriched = json.loads(json_output.read_text(encoding="utf-8"))
    assert enriched["failure_taxonomy"]["targeted_case_ids"]["evolved_no_patch"] == [
        "calculator-zero"
    ]


def test_repository_benchmark_cli_requires_reuse_json_for_case_bucket() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_repository_double_run_benchmark.py",
            "--case-bucket",
            "evolved_no_patch",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode != 0
    assert "--case-bucket requires --reuse-baseline-json" in completed.stderr
