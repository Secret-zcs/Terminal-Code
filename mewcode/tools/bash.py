# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from mewcode.tools.base import Tool, ToolResult
from mewcode.tools.workspace import resolve_command_cwd

MAX_TIMEOUT = 600


class Params(BaseModel):
    command: str = Field(description="Shell command to execute")
    timeout: int = Field(default=120, description="Timeout in seconds (max 600)")


class Bash(Tool):
    name = "Bash"
    description = "Execute a shell command and return stdout and stderr."
    params_model = Params
    category = "command"

    def __init__(self, work_dir: str | Path | None = None) -> None:
        self.work_dir = work_dir

    async def execute(self, params: Params) -> ToolResult:
        timeout = min(params.timeout, MAX_TIMEOUT)

        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                params.command,
                shell=True,
                cwd=resolve_command_cwd(self.work_dir),
                capture_output=True,
                timeout=timeout,
                text=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(output=f"Error: command timed out after {timeout}s", is_error=True)
        except Exception as e:
            return ToolResult(output=f"Error executing command: {e}", is_error=True)

        parts: list[str] = []
        stdout = completed.stdout
        stderr = completed.stderr
        if stdout:
            stdout_text = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
            parts.append(f"STDOUT:\n{stdout_text}")
        if stderr:
            stderr_text = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
            parts.append(f"STDERR:\n{stderr_text}")
        if not parts:
            parts.append("(no output)")

        output = "\n".join(parts)
        return ToolResult(output=output, is_error=completed.returncode != 0)
