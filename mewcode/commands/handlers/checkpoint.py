"""Manual checkpoint creation via /checkpoint slash command.

Usage:
    /checkpoint "refactored auth module"  – create a named checkpoint
    /checkpoint                            – list all existing checkpoints
"""

from __future__ import annotations

from mewcode.commands.registry import Command, CommandType


async def _handle_checkpoint(ctx) -> None:
    cm = getattr(ctx.agent, "checkpoint_manager", None)
    if cm is None:
        ctx.ui.add_system_message("Checkpoint system is not available.")
        return

    args = ctx.args.strip()

    # 无参数：列出所有检查点
    if not args:
        checkpoints = cm.list_checkpoints()
        if not checkpoints:
            ctx.ui.add_system_message("No checkpoints yet. Use /checkpoint \"label\" to create one.")
            return

        lines = ["● Checkpoints:\n"]
        for cp in checkpoints:
            trigger_icon = _trigger_icon(cp.trigger)
            lines.append(
                f"  [{cp.seq}] {trigger_icon} {cp.label}  "
                f"({cp.file_count} file(s), {_format_ago(cp.created_at)})"
            )
        lines.append(f"\n{len(checkpoints)} checkpoint(s) total.")
        ctx.ui.add_system_message("\n".join(lines))
        return

    # 有参数：创建检查点
    label = args.strip('"').strip("'").strip()
    if not label:
        label = f"Manual checkpoint"
    # 限制标签长度
    if len(label) > 80:
        label = label[:77] + "…"

    cp = cm.create_checkpoint(
        label=label,
        trigger="manual",
        conversation=ctx.conversation,
        agent=ctx.agent,
    )
    ctx.ui.add_system_message(
        f"● Checkpoint [{cp.seq}] created: \"{cp.label}\" "
        f"({cp.file_count} file(s) tracked)"
    )


def _trigger_icon(trigger: str) -> str:
    icons = {
        "manual":      "✚",
        "turn_end":    "↻",
        "pre_write":   "✎",
        "pre_bash":    "⚡",
        "pre_delegate":"◆",
        "pre_compact": "≫",
    }
    return icons.get(trigger, "•")


def _format_ago(timestamp: float) -> str:
    import time
    ago = int(time.time() - timestamp)
    if ago < 60:
        return f"{ago}s ago"
    elif ago < 3600:
        return f"{ago // 60}m ago"
    elif ago < 86400:
        return f"{ago // 3600}h ago"
    else:
        return f"{ago // 86400}d ago"


CHECKPOINT_COMMAND = Command(
    name="checkpoint",
    description="Create or list rewind checkpoints",
    type=CommandType.LOCAL,
    handler=_handle_checkpoint,
    usage="/checkpoint [\"label\"]",
    aliases=["snapshot", "cp"],
)
