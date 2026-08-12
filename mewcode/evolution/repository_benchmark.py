"""Repository fixture loading for double-run benchmarks."""

from __future__ import annotations

import difflib
import json
import os
import stat
import subprocess
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

from mewcode.agent import Agent
from mewcode.client import LLMClient
from mewcode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from mewcode.tools import create_default_registry


ClientFactory = Callable[[Path, bool], LLMClient]

_IGNORED_SNAPSHOT_DIRECTORIES = frozenset(
    {".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".mewcode"}
)
_COMMAND_OUTPUT_LIMIT = 10_000


@dataclass(frozen=True)
class RepositoryFixture:
    id: str
    repository: Path
    issue: str
    test_command: str
    regression_command: str
    expected_tests: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]


def snapshot_repository(root: str | Path) -> dict[str, str]:
    """Read regular repository files into a stable relative-path snapshot."""
    repository_root = Path(root)
    snapshot: dict[str, str] = {}
    for directory, directory_names, file_names in os.walk(repository_root):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in _IGNORED_SNAPSHOT_DIRECTORIES
        )
        current_directory = Path(directory)
        for file_name in sorted(file_names):
            path = current_directory / file_name
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            relative_path = path.relative_to(repository_root).as_posix()
            snapshot[relative_path] = content
    return dict(sorted(snapshot.items()))


def compare_snapshots(
    before: dict[str, str],
    after: dict[str, str],
    allowed_paths: tuple[str, ...] | list[str],
    forbidden_paths: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Compare snapshots and report changed paths, scope violations, and line counts."""
    changed_paths = sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    )
    out_of_scope_changes = [
        path
        for path in changed_paths
        if not any(fnmatch(path, rule) for rule in allowed_paths)
    ]
    forbidden_changes = [
        path
        for path in changed_paths
        if any(fnmatch(path, rule) for rule in forbidden_paths)
    ]

    added = 0
    removed = 0
    for path in changed_paths:
        diff = difflib.unified_diff(
            before.get(path, "").splitlines(keepends=True),
            after.get(path, "").splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
        for line in diff:
            if line.startswith("--- ") or line.startswith("+++ "):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1

    return {
        "changed_paths": changed_paths,
        "out_of_scope_changes": out_of_scope_changes,
        "forbidden_changes": forbidden_changes,
        "patch_size": {
            "added": added,
            "removed": removed,
            "total": added + removed,
        },
    }


def _normalize_command_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return output[-_COMMAND_OUTPUT_LIMIT:]


def run_local_command(
    command: str,
    cwd: str | Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run a benchmark command and normalize its result."""
    started_at = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        status = "passed" if completed.returncode == 0 else "failed"
        exit_code: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        exit_code = None
        stdout = exc.stdout
        stderr = exc.stderr

    return {
        "status": status,
        "exit_code": exit_code,
        "stdout": _normalize_command_output(stdout),
        "stderr": _normalize_command_output(stderr),
        "elapsed_seconds": time.perf_counter() - started_at,
    }


async def run_repository_case(
    fixture: RepositoryFixture,
    repository_root: Path,
    *,
    evolved: bool,
    candidate_skill: str,
    client_factory: ClientFactory,
    protocol: str,
    max_iterations: int,
    test_timeout_seconds: float,
) -> dict[str, Any]:
    """Run one side of a repository benchmark in its isolated repository."""
    started_at = time.perf_counter()
    before = snapshot_repository(repository_root)
    status = "completed"
    error_type = ""
    error = ""
    final_text = ""
    tool_call_count = 0
    permission_denied = 0
    rewind_used = False
    agent: Agent | None = None

    try:
        client = client_factory(repository_root, evolved)
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(str(repository_root)),
            rule_engine=RuleEngine(),
            mode=PermissionMode.DONT_ASK,
        )
        registry = create_default_registry(work_dir=repository_root)
        agent = Agent(
            client,
            registry,
            protocol,
            work_dir=repository_root,
            max_iterations=max_iterations,
            permission_checker=checker,
        )
        if evolved:
            agent.activate_skill("candidate", candidate_skill)

        original_execute = agent._execute_tool_noninteractive

        async def execute_and_track(tool_call: Any) -> Any:
            nonlocal permission_denied
            result = await original_execute(tool_call)
            if result.is_error and result.output.startswith("Permission denied:"):
                permission_denied += 1
            return result

        agent._execute_tool_noninteractive = execute_and_track  # type: ignore[method-assign]

        def record_event(event: dict[str, Any]) -> None:
            nonlocal tool_call_count, rewind_used
            if event.get("type") != "tool_use":
                return
            tool_call_count += 1
            tool_name = str(event.get("toolName", ""))
            if "rewind" in tool_name.casefold():
                rewind_used = True

        final_text = await agent.run_to_completion(
            fixture.issue,
            event_callback=record_event,
        )
    except Exception as exc:
        status = "provider-failed"
        error_type = type(exc).__name__
        error = str(exc)

    regression_result = (
        run_local_command(
            fixture.regression_command,
            repository_root,
            test_timeout_seconds,
        )
        if fixture.regression_command
        else None
    )
    test_result = run_local_command(
        fixture.test_command,
        repository_root,
        test_timeout_seconds,
    )
    after = snapshot_repository(repository_root)
    comparison = compare_snapshots(
        before,
        after,
        fixture.allowed_paths,
        fixture.forbidden_paths,
    )

    tests_passed = test_result["status"] == "passed"
    regression_free = (
        regression_result is None or regression_result["status"] == "passed"
    )
    expected_tests_present = all(
        (repository_root / path).is_file() for path in fixture.expected_tests
    )
    task_success = (
        status == "completed"
        and tests_passed
        and expected_tests_present
        and not comparison["forbidden_changes"]
        and not comparison["out_of_scope_changes"]
    )

    return {
        "status": status,
        "task_success": task_success,
        "tests_passed": tests_passed,
        "regression_free": regression_free,
        "expected_tests_present": expected_tests_present,
        **comparison,
        "input_tokens": agent.total_input_tokens if agent is not None else 0,
        "output_tokens": agent.total_output_tokens if agent is not None else 0,
        "elapsed_seconds": time.perf_counter() - started_at,
        "tool_call_count": tool_call_count,
        "permission_denied": permission_denied,
        "rewind_used": rewind_used,
        "regression_result": regression_result,
        "test_result": test_result,
        "final_text": final_text,
        "error_type": error_type,
        "error": error,
    }


def load_repository_fixtures(root: str | Path) -> list[RepositoryFixture]:
    """Load and validate repository benchmark cases under ``root``."""
    fixture_root = Path(root)
    if not fixture_root.is_dir():
        raise ValueError(f"invalid repository fixture root: {fixture_root}")

    try:
        case_directories = sorted(
            (path for path in fixture_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        )
    except OSError as exc:
        raise ValueError(f"invalid repository fixture root: {fixture_root}: {exc}") from exc

    fixtures: list[RepositoryFixture] = []
    for case_directory in case_directories:
        case_id = case_directory.name
        try:
            fixtures.append(_load_repository_fixture(case_directory))
        except (
            json.JSONDecodeError,
            OSError,
            RecursionError,
            TypeError,
            ValueError,
            UnicodeError,
        ) as exc:
            raise ValueError(f"invalid repository fixture {case_id}: {exc}") from exc
    return fixtures


def _load_repository_fixture(case_directory: Path) -> RepositoryFixture:
    repository = case_directory / "repository"
    issue_path = case_directory / "issue.md"
    expected_path = case_directory / "expected.json"

    if case_directory.is_symlink():
        raise ValueError("case directory cannot be a symlink")
    if repository.is_symlink():
        raise ValueError("repository/ cannot be a symlink")
    if not repository.is_dir():
        raise ValueError("repository/ must be a directory")
    if issue_path.is_symlink():
        raise ValueError("issue.md cannot be a symlink")
    if not issue_path.is_file():
        raise ValueError("issue.md must be a file")
    if expected_path.is_symlink():
        raise ValueError("expected.json cannot be a symlink")
    if not expected_path.is_file():
        raise ValueError("expected.json must be a file")
    _reject_repository_symlinks(repository)

    issue = issue_path.read_text(encoding="utf-8").strip()
    if not issue:
        raise ValueError("issue.md cannot be empty")

    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if not isinstance(expected, dict):
        raise TypeError("expected.json must contain an object")

    test_command = _required_nonempty_string(expected, "test_command")
    regression_command = expected.get("regression_command", "")
    if not isinstance(regression_command, str):
        raise TypeError("regression_command must be a string")

    return RepositoryFixture(
        id=case_directory.name,
        repository=repository,
        issue=issue,
        test_command=test_command,
        regression_command=regression_command.strip(),
        expected_tests=_path_rules(expected, "expected_tests", require_nonempty=True),
        allowed_paths=_path_rules(expected, "allowed_paths", require_nonempty=True),
        forbidden_paths=_path_rules(expected, "forbidden_paths", require_nonempty=False),
    )


def _reject_repository_symlinks(repository: Path) -> None:
    pending = [repository]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    relative_path = Path(entry.path).relative_to(repository)
                    raise ValueError(
                        f"repository entry cannot be a symlink: {relative_path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))


def _required_nonempty_string(data: dict[str, Any], field: str) -> str:
    if field not in data:
        raise ValueError(f"missing required field: {field}")
    value = data[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} cannot be empty")
    return value


def _path_rules(
    data: dict[str, Any],
    field: str,
    *,
    require_nonempty: bool,
) -> tuple[str, ...]:
    if field not in data:
        raise ValueError(f"missing required field: {field}")
    values = data[field]
    if not isinstance(values, list):
        raise TypeError(f"{field} must be a list")
    if require_nonempty and not values:
        raise ValueError(f"{field} cannot be empty")

    rules: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{field} entries must be strings")
        rule = value.strip()
        path = PurePosixPath(rule)
        windows_path = PureWindowsPath(rule)
        if not rule:
            raise ValueError(f"{field} entries cannot be empty")
        if (
            "\\" in rule
            or path.is_absolute()
            or ".." in path.parts
            or windows_path.drive
        ):
            raise ValueError(f"{field} entry must be a relative POSIX path: {value!r}")
        rules.append(rule)
    return tuple(rules)
