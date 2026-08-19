# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from mewcode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from mewcode.teams.manager import TeamManager


class TaskClaimParams(BaseModel):
    task_id: str


class TaskClaimTool(Tool):
    name = "TaskClaim"
    description = (
        "Atomically claim a pending shared task for yourself. "
        "Only one teammate can claim a task — a second claim on the same task fails. "
        "Use this instead of TaskUpdate(status=in_progress) to claim work, "
        "so two teammates never pick up the same task by accident."
    )
    params_model = TaskClaimParams
    category = "command"
    is_concurrency_safe = True


    def __init__(self, team_manager: TeamManager, team_name: str, agent_name: str = "") -> None:
        self._team_manager = team_manager
        self._team_name = team_name
        self._agent_name = agent_name


    async def execute(self, params: BaseModel) -> ToolResult:
        p: TaskClaimParams = params  # type: ignore[assignment]

        store = self._team_manager.get_task_store(self._team_name)
        if store is None:
            return ToolResult(output=f"Task store not found for team '{self._team_name}'", is_error=True)

        if not store.claim(p.task_id, self._agent_name):
            task = store.get(p.task_id)
            if task is None:
                reason = "task not found"
            else:
                reason = f"already claimed/closed (status={task.status}, assignee={task.assignee or 'none'})"
            return ToolResult(
                output=f"Cannot claim task '{p.task_id}': {reason}",
                is_error=True,
            )

        return ToolResult(output=f"Task '{p.task_id}' claimed by '{self._agent_name}' (in_progress).")
