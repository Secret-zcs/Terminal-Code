"""Repository fixture loading for double-run benchmarks."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


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
