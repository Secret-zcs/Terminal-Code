"""Rewind to a previous checkpoint — restore files and/or conversation state.

Usage:
    /rewind                  – list all checkpoints
    /rewind N                – rewind to checkpoint N (both code + conversation)
    /rewind N --code         – restore code only
    /rewind N --conv         – restore conversation only
    /rewind N --preview      – preview changes without executing
    /rewind --undo           – undo the most recent rewind
"""

from __future__ import annotations

import time

from mewcode.commands.registry import Command, CommandType


async def _handle_rewind(ctx) -> None:
    cm = getattr(ctx.agent, "checkpoint_manager", None)

    # ── 无 CheckpointManager：回退到 FileHistory 路径 ──
    if cm is None:
        await _handle_rewind_legacy(ctx)
        return

    args = ctx.args.strip()

    # ── /rewind --undo ──
    if args == "--undo":
        result = cm.undo_last_rewind(ctx.conversation)
        if result.success:
            ctx.ui.add_system_message(
                f"⟲ Undo complete. Restored {len(result.changed_files)} file(s) "
                f"to pre-rewind state."
            )
        else:
            ctx.ui.add_system_message(f"⟲ Cannot undo: {result.error}")
        return

    # ── /rewind (无参数) — 列出所有检查点 ──
    if not args:
        checkpoints = cm.list_checkpoints()
        if not checkpoints:
            ctx.ui.add_system_message("No checkpoints to rewind to. Use /checkpoint \"label\" to create one.")
            return

        lines = ["⟲ Rewind — select a checkpoint:\n"]
        for cp in checkpoints:
            trigger_icon = _trigger_icon(cp.trigger)
            trigger_label = _trigger_label(cp.trigger)
            ago = _format_ago(cp.created_at)
            lines.append(
                f"  [{cp.seq}] {trigger_icon} {cp.label}  "
                f"({trigger_label}, {cp.file_count} file(s), {ago})"
            )
        lines.append("")
        lines.append("Usage:")
        lines.append(f"  /rewind <N>             — restore code + conversation")
        lines.append(f"  /rewind <N> --code      — restore code only")
        lines.append(f"  /rewind <N> --conv      — restore conversation only")
        lines.append(f"  /rewind <N> --preview   — preview what will change")
        lines.append(f"  /rewind --undo          — undo last rewind")
        lines.append(f"\nExample: /rewind {checkpoints[-1].seq} --preview")
        ctx.ui.add_system_message("\n".join(lines))
        return

    # ── 解析参数 ──
    parts = args.split()
    try:
        seq = int(parts[0])
    except (ValueError, IndexError):
        ctx.ui.add_system_message("Invalid checkpoint number. Usage: /rewind <N> [--code|--conv|--preview]")
        return

    flags = parts[1:] if len(parts) > 1 else []
    preview_mode = "--preview" in flags
    code_only = "--code" in flags
    conv_only = "--conv" in flags

    # ── Preview ──
    if preview_mode:
        preview = cm.preview_rewind(seq, ctx.conversation)
        if preview is None:
            ctx.ui.add_system_message(f"Checkpoint {seq} not found.")
            return
        if not preview.has_changes():
            ctx.ui.add_system_message(
                f"⟲ No changes between current state and checkpoint [{seq}] "
                f"\"{preview.checkpoint.label}\"."
            )
            return

        lines = [
            f"⟲ Preview: Rewind to [{seq}] \"{preview.checkpoint.label}\"\n"
        ]
        if preview.files_to_change:
            lines.append("Files that would be restored:")
            for fc in preview.files_to_change:
                lines.append(f"  {fc.summary()}")
        else:
            lines.append("No files would be changed.")
        lines.append("")
        if preview.messages_to_remove > 0:
            lines.append(
                f"Conversation: {preview.messages_to_remove} messages "
                f"would be removed."
            )
            if preview.message_snapshot:
                lines.append(f"  Last message kept: {preview.message_snapshot}")
        else:
            lines.append("Conversation would be unchanged.")
        lines.append("")
        lines.append(f"To execute: /rewind {seq}")
        lines.append("To cancel: no action needed")
        ctx.ui.add_system_message("\n".join(lines))
        return

    # ── 验证检查点存在 ──
    cp = cm.get_checkpoint(seq)
    if cp is None:
        ctx.ui.add_system_message(
            f"Checkpoint {seq} not found. Use /rewind to list available checkpoints."
        )
        return

    # ── 执行回退 ──
    if code_only:
        option = "code"
    elif conv_only:
        option = "conv"
    else:
        option = "both"

    result = cm.execute_rewind(seq, option=option, conversation=ctx.conversation)

    if not result.success:
        ctx.ui.add_system_message(f"⟲ Rewind failed: {result.error}")
        return

    # ── 构造结果消息 ──
    parts_msg = []
    if result.changed_files:
        parts_msg.append(f"restored {len(result.changed_files)} file(s)")
    if result.messages_removed > 0:
        parts_msg.append(f"removed {result.messages_removed} conversation message(s)")

    detail = " and ".join(parts_msg) if parts_msg else "no changes needed"
    ctx.ui.add_system_message(
        f"⟲ Rewound to checkpoint [{seq}] \"{cp.label}\": {detail}."
    )


# ═══════════════════════════════════════════════════════════════════
# Legacy path — when no CheckpointManager is available
# ═══════════════════════════════════════════════════════════════════

async def _handle_rewind_legacy(ctx) -> None:
    """原有 /rewind 行为（无 CheckpointManager 时回退到 FileHistory）。"""
    fh = getattr(ctx.agent, "file_history", None)
    if fh is None or not fh.has_snapshots():
        ctx.ui.add_system_message("No checkpoints to rewind to.")
        return

    snapshots = fh.get_snapshots()

    lines = ["⟲ Rewind — select a checkpoint:\n"]
    for i, snap in enumerate(snapshots):
        ago = int(time.time() - snap.timestamp)
        label = snap.user_text[:50] + "…" if len(snap.user_text) > 50 else snap.user_text
        lines.append(f"  [{i + 1}] {label} ({ago}s ago, {len(snap.backups)} file(s))")
    lines.append("\nOptions after selecting:")
    lines.append("  1) Restore code and conversation")
    lines.append("  2) Restore conversation only")
    lines.append("  3) Restore code only")
    lines.append(
        f"\nUsage: /rewind <checkpoint> [option]  "
        f"(e.g. /rewind {len(snapshots)} 1)"
    )
    ctx.ui.add_system_message("\n".join(lines))

    args = ctx.args.strip()
    if not args:
        return

    parts = args.split()
    try:
        idx = int(parts[0]) - 1
    except (ValueError, IndexError):
        ctx.ui.add_system_message("Invalid checkpoint number.")
        return

    if idx < 0 or idx >= len(snapshots):
        ctx.ui.add_system_message(
            f"Checkpoint {idx + 1} not found. Valid: 1-{len(snapshots)}"
        )
        return

    option = 1
    if len(parts) > 1:
        try:
            option = int(parts[1])
        except ValueError:
            pass

    snap = snapshots[idx]

    if option == 1:
        changed = fh.rewind(idx)
        ctx.conversation.replace_history(
            ctx.conversation.history[: snap.message_index]
        )
        ctx.ui.add_system_message(
            f"⟲ Rewound to checkpoint {idx + 1}. "
            f"Restored {len(changed)} file(s) and conversation."
        )
    elif option == 2:
        ctx.conversation.replace_history(
            ctx.conversation.history[: snap.message_index]
        )
        ctx.ui.add_system_message(
            f"⟲ Rewound conversation to checkpoint {idx + 1}. Files unchanged."
        )
    elif option == 3:
        changed = fh.rewind(idx)
        ctx.ui.add_system_message(
            f"⟲ Restored {len(changed)} file(s) to checkpoint {idx + 1}. "
            f"Conversation unchanged."
        )
    else:
        ctx.ui.add_system_message(
            "Invalid option. Use 1 (both), 2 (conversation), or 3 (code)."
        )


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

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


def _trigger_label(trigger: str) -> str:
    labels = {
        "manual":      "manual",
        "turn_end":    "auto",
        "pre_write":   "auto",
        "pre_bash":    "auto",
        "pre_delegate":"auto",
        "pre_compact": "auto",
    }
    return labels.get(trigger, "auto")


def _format_ago(timestamp: float) -> str:
    ago = int(time.time() - timestamp)
    if ago < 60:
        return f"{ago}s ago"
    elif ago < 3600:
        return f"{ago // 60}m ago"
    elif ago < 86400:
        return f"{ago // 3600}h ago"
    else:
        return f"{ago // 86400}d ago"


REWIND_COMMAND = Command(
    name="rewind",
    description="Rewind to a previous checkpoint",
    type=CommandType.LOCAL,
    handler=_handle_rewind,
    usage="/rewind [checkpoint_number] [--code|--conv|--preview] | --undo",
)
