"""CheckpointManager — unified orchestrator for the rewind/snapshot system.

Wraps FileHistory (file-level backups + restore) and CheckpointStore (metadata
persistence) to provide a single API for checkpoint creation, listing, preview,
and rewind execution.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from mewcode.checkpoint.models import (
    Checkpoint,
    FileChange,
    RewindPreview,
    RewindResult,
    TriggerType,
    new_checkpoint_id,
)
from mewcode.checkpoint.store import CheckpointStore

if TYPE_CHECKING:
    from mewcode.agent import Agent
    from mewcode.conversation import ConversationManager
    from mewcode.filehistory.history import FileHistory


class CheckpointManager:
    """Orchestrates checkpoint creation, listing, preview, and rewind.

    Builds on top of FileHistory (which handles file-level backup/restore)
    and adds: metadata persistence, agent state capture, auto-trigger
    rate limiting, preview, and undo.
    """

    def __init__(
        self,
        file_history: "FileHistory",
        session_dir: Path,
        *,
        auto_enabled: bool = True,
        auto_min_interval: float = 30.0,
    ) -> None:
        self._fh = file_history
        self._store = CheckpointStore(session_dir)
        self._auto_enabled = auto_enabled
        self._min_interval = auto_min_interval
        self._last_auto_time: float = 0.0
        # 从磁盘恢复时重建 seq_counter
        self._seq_counter: int = self._store.last_seq()
        # 回退前的快照（用于 undo），在 execute_rewind 时设置
        self._pre_rewind_snapshot: Checkpoint | None = None

    # ═══════════════════════════════════════════════════════════════
    # Checkpoint Creation
    # ═══════════════════════════════════════════════════════════════

    def create_checkpoint(
        self,
        label: str,
        trigger: TriggerType,
        conversation: "ConversationManager",
        agent: "Agent",
    ) -> Checkpoint:
        """Create a new checkpoint: file backup + metadata persistence.

        This is synchronous by design — it does file I/O (reading current
        file contents for backup) which should complete before the agent
        proceeds with the risky operation that triggered it.
        """
        msg_index = len(conversation.history)

        # Delegate file-level snapshot to FileHistory
        self._fh.make_snapshot(msg_index, label)
        snapshots = self._fh.get_snapshots()
        file_count = len(snapshots[-1].backups) if snapshots else 0

        # Capture agent state
        agent_state = self._capture_agent_state(agent)

        # Create and persist metadata
        self._seq_counter += 1
        cp = Checkpoint(
            id=new_checkpoint_id(),
            seq=self._seq_counter,
            label=label,
            trigger=trigger,
            message_index=msg_index,
            file_count=file_count,
            agent_state=agent_state,
            created_at=time.time(),
        )
        self._store.save(cp)
        return cp

    # ═══════════════════════════════════════════════════════════════
    # Auto-Checkpoint Rate Limiting
    # ═══════════════════════════════════════════════════════════════

    @property
    def auto_enabled(self) -> bool:
        return self._auto_enabled

    @auto_enabled.setter
    def auto_enabled(self, value: bool) -> None:
        self._auto_enabled = value

    def should_auto_checkpoint(self, trigger: TriggerType) -> bool:
        """Check if enough time has passed since the last auto-checkpoint.

        Manual checkpoints and turn_end checkpoints are not rate-limited.
        """
        if not self._auto_enabled:
            return False
        if trigger in ("manual", "turn_end"):
            return True
        elapsed = time.monotonic() - self._last_auto_time
        return elapsed >= self._min_interval

    def mark_auto_checkpoint(self) -> None:
        """Record that an auto-checkpoint just fired."""
        self._last_auto_time = time.monotonic()

    # ═══════════════════════════════════════════════════════════════
    # Query
    # ═══════════════════════════════════════════════════════════════

    def list_checkpoints(self) -> list[Checkpoint]:
        """Return all checkpoints ordered by seq (oldest first)."""
        return self._store.load_all()

    def get_checkpoint(self, seq: int) -> Checkpoint | None:
        """Look up a single checkpoint by its sequence number."""
        for cp in self.list_checkpoints():
            if cp.seq == seq:
                return cp
        return None

    def has_checkpoints(self) -> bool:
        return self._store.count() > 0

    @property
    def checkpoint_count(self) -> int:
        return self._store.count()

    # ═══════════════════════════════════════════════════════════════
    # Preview
    # ═══════════════════════════════════════════════════════════════

    def preview_rewind(
        self,
        seq: int,
        conversation: "ConversationManager | None" = None,
    ) -> RewindPreview | None:
        """Preview what a rewind to `seq` would change — without executing.

        Returns None if the checkpoint doesn't exist.
        """
        cp = self.get_checkpoint(seq)
        if cp is None:
            return None

        # Map checkpoint seq to FileHistory snapshot index (seq - 1)
        snapshots = self._fh.get_snapshots()
        snapshot_idx = seq - 1
        if snapshot_idx < 0 or snapshot_idx >= len(snapshots):
            return None
        snapshot = snapshots[snapshot_idx]

        # Compare backup contents against current files
        files_to_change: list[FileChange] = []
        for file_path, backup in snapshot.backups.items():
            bp = Path(backup.backup_path)
            fp = Path(file_path)
            try:
                current = fp.read_bytes() if fp.exists() else b""
            except OSError:
                current = b""
            try:
                backup_data = bp.read_bytes()
            except (FileNotFoundError, OSError):
                continue

            if current == backup_data:
                continue  # 文件未变化

            if len(current) == 0 and len(backup_data) > 0:
                action: Literal["modify", "delete", "create"] = "create"
            elif len(current) > 0 and len(backup_data) == 0:
                action = "delete"
            else:
                action = "modify"

            files_to_change.append(FileChange(
                path=file_path,
                action=action,
                current_size=len(current),
                backup_size=len(backup_data),
            ))

        # Compute conversation truncation info
        messages_to_remove = 0
        message_snapshot = ""
        if conversation is not None:
            current_len = len(conversation.history)
            messages_to_remove = max(0, current_len - cp.message_index)
            if cp.message_index > 0 and cp.message_index <= current_len:
                last_msg = conversation.history[cp.message_index - 1]
                preview_text = (
                    last_msg.content[:80] + "…"
                    if last_msg.content and len(last_msg.content) > 80
                    else (last_msg.content or "(tool call)")
                )
                message_snapshot = f"[{last_msg.role}] {preview_text}"

        return RewindPreview(
            checkpoint=cp,
            files_to_change=files_to_change,
            messages_to_remove=messages_to_remove,
            message_snapshot=message_snapshot,
        )

    # ═══════════════════════════════════════════════════════════════
    # Execute Rewind
    # ═══════════════════════════════════════════════════════════════

    def execute_rewind(
        self,
        seq: int,
        option: Literal["both", "code", "conv"] = "both",
        conversation: "ConversationManager | None" = None,
    ) -> RewindResult:
        """Execute a rewind to the checkpoint with the given seq number.

        Args:
            seq: The checkpoint sequence number (1-based, user-visible).
            option: "both" (default), "code" (files only), or "conv" (conversation only).
            conversation: Required for "both" and "conv" options.

        Returns:
            RewindResult with success status and change summary.
        """
        cp = self.get_checkpoint(seq)
        if cp is None:
            return RewindResult(success=False, error=f"Checkpoint {seq} not found")

        # Save pre-rewind state for potential undo.
        # We create a new checkpoint representing the current state.
        if conversation is not None:
            pre_state = self._capture_agent_state_from_conversation()
            pre_cp = Checkpoint(
                id=new_checkpoint_id(),
                seq=cp.seq,  # same seq as the target (for undo mapping)
                label=f"[undo] before rewind to #{seq}",
                trigger="manual",
                message_index=len(conversation.history),
                file_count=len(self._fh.get_snapshots()[-1].backups)
                    if self._fh.get_snapshots() else 0,
                agent_state=pre_state,
                created_at=time.time(),
            )
            self._store.save_undo(pre_cp)

            # Also snapshot current files for undo
            current_msg_index = len(conversation.history)
            self._fh.make_snapshot(
                current_msg_index,
                f"[undo] before rewind to #{seq}",
            )

        # Execute file rewind (via FileHistory)
        changed_files: list[str] = []
        if option in ("both", "code"):
            # seq → snapshot_index: seq is 1-based, snapshot index is 0-based
            snapshot_idx = seq - 1
            changed_files = self._fh.rewind(snapshot_idx)

        # Execute conversation truncation
        messages_removed = 0
        if option in ("both", "conv") and conversation is not None:
            old_len = len(conversation.history)
            conversation.replace_history(conversation.history[: cp.message_index])
            messages_removed = old_len - len(conversation.history)

        # Clean up checkpoints after the rewind target
        self._store.delete_from(seq + 1)

        # Reset seq counter to the rewind target
        self._seq_counter = seq

        return RewindResult(
            success=True,
            changed_files=changed_files,
            messages_removed=messages_removed,
            checkpoint=cp,
        )

    # ═══════════════════════════════════════════════════════════════
    # Undo
    # ═══════════════════════════════════════════════════════════════

    def undo_last_rewind(
        self,
        conversation: "ConversationManager | None" = None,
    ) -> RewindResult:
        """Undo the most recent rewind operation.

        Restores files to the state captured just before the rewind,
        and truncates the store's undo record.
        """
        undo_cp = self._store.load_undo()
        if undo_cp is None:
            return RewindResult(success=False, error="No rewind to undo")

        # Restore files to the pre-rewind state
        # The pre-rewind snapshot is the LAST snapshot in FileHistory
        # (we appended it just before executing rewind)
        snapshots = self._fh.get_snapshots()
        if snapshots:
            undo_snapshot_idx = len(snapshots) - 1
            changed_files = self._fh.rewind(undo_snapshot_idx)
        else:
            changed_files = []

        # Restore conversation if available
        if conversation is not None:
            conversation.replace_history(
                conversation.history[: undo_cp.message_index]
            )

        self._store.clear_undo()

        return RewindResult(
            success=True,
            changed_files=changed_files,
            messages_removed=0,
            checkpoint=undo_cp,
        )

    # ═══════════════════════════════════════════════════════════════
    # Session Resume
    # ═══════════════════════════════════════════════════════════════

    def load_from_disk(self) -> int:
        """Restore checkpoint state from disk after a session resume.

        Returns the number of checkpoints loaded.
        """
        checkpoints = self._store.load_all()
        if checkpoints:
            self._seq_counter = max(cp.seq for cp in checkpoints)
        return len(checkpoints)

    # ═══════════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _capture_agent_state(agent: "Agent") -> dict:
        """Extract a snapshot of relevant agent state."""
        state: dict = {
            "iteration": getattr(agent, "iteration", 0),
        }
        # Permission mode
        pc = getattr(agent, "permission_checker", None)
        if pc is not None and hasattr(pc, "mode"):
            state["permission_mode"] = pc.mode.value if hasattr(pc.mode, "value") else str(pc.mode)
        else:
            state["permission_mode"] = "default"

        # Plan mode
        state["plan_mode"] = getattr(agent, "plan_mode", False)

        return state

    @staticmethod
    def _capture_agent_state_from_conversation() -> dict:
        """Minimal state capture when we don't have an agent reference."""
        return {"permission_mode": "unknown", "plan_mode": False, "iteration": 0}


def _is_risky_bash(command: str) -> bool:
    """Heuristic: does this Bash command look potentially destructive?

    Used by the agent loop to decide whether to auto-checkpoint before execution.
    """
    if not command or not command.strip():
        return False

    risky_patterns = [
        "rm ", "rmdir", "mv ", ">", ">>",
        "pip install", "pip3 install",
        "npm install", "npm uninstall",
        "yarn add", "yarn remove",
        "git reset", "git clean",
        "chmod ", "chown ",
        "make ", "cmake ",
        "docker rm", "docker rmi",
        "curl", "wget",
    ]
    cmd_lower = command.lower()
    return any(pattern in cmd_lower for pattern in risky_patterns)
