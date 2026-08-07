from __future__ import annotations

from pathlib import Path


def resolve_workspace_path(root_dir: str | Path | None, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute() or root_dir is None:
        return path
    return Path(root_dir).resolve() / path


def resolve_command_cwd(root_dir: str | Path | None) -> str | None:
    return str(Path(root_dir).resolve()) if root_dir is not None else None
