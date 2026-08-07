# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from mewcode.tools.base import SKIP_DIRS, Tool, ToolResult
from mewcode.tools.workspace import resolve_workspace_path


class Params(BaseModel):
    pattern: str = Field(description="Glob pattern to match (e.g. '**/*.py')")
    path: str = Field(default=".", description="Base directory to search from")


class Glob(Tool):
    name = "Glob"
    description = "Find files matching a glob pattern, returning relative paths."
    params_model = Params
    category = "read"
    is_concurrency_safe = True

    def __init__(self, work_dir: str | Path | None = None) -> None:
        self.work_dir = work_dir

    async def execute(self, params: Params) -> ToolResult:
        base = resolve_workspace_path(self.work_dir, params.path)
        if not base.exists():
            return ToolResult(output=f"Error: path not found: {params.path}", is_error=True)

        try:
            matches = sorted(
                str(p.relative_to(base))
                for p in base.glob(params.pattern)
                if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)
            )
        except Exception as e:
            return ToolResult(output=f"Error: {e}", is_error=True)

        if not matches:
            return ToolResult(output="No files matched the pattern.")
        return ToolResult(output="\n".join(matches))
