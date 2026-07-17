"""Checkpoint data models for the rewind/snapshot system.

A Checkpoint captures the full state of a session at a point in time:
file backups (delegated to FileHistory) + conversation position + agent state.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

TriggerType = Literal[
    "manual",         # /checkpoint command
    "turn_end",       # after each LLM turn completes
    "pre_write",      # before WriteFile / EditFile
    "pre_bash",       # before a potentially destructive Bash command
    "pre_delegate",   # before AgentDelegate forks a sub-agent
    "pre_compact",    # before context compaction
]


@dataclass
class Checkpoint:
    """Complete metadata for a single rewind checkpoint."""

    id: str                             # short UUID (12 hex chars)
    seq: int                            # monotonic sequence number (user-visible, 1-based)
    label: str                          # user label or auto-generated description
    trigger: TriggerType                # what triggered this checkpoint
    message_index: int                  # position in conversation.history
    file_count: int                     # number of files tracked in this snapshot
    agent_state: dict                   # {plan_mode, permission_mode, iteration}
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "seq": self.seq,
            "label": self.label,
            "trigger": self.trigger,
            "message_index": self.message_index,
            "file_count": self.file_count,
            "agent_state": self.agent_state,
            "created_at": self.created_at,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Checkpoint":
        return cls(
            id=data["id"],
            seq=data["seq"],
            label=data["label"],
            trigger=data["trigger"],
            message_index=data["message_index"],
            file_count=data["file_count"],
            agent_state=data.get("agent_state", {}),
            created_at=data.get("created_at", 0.0),
        )

    @classmethod
    def from_jsonl(cls, line: str) -> "Checkpoint":
        return cls.from_dict(json.loads(line))


@dataclass
class FileChange:
    """Describes a single file that would be changed by a rewind operation."""

    path: str
    action: Literal["modify", "delete", "create"]
    current_size: int
    backup_size: int

    def summary(self) -> str:
        if self.action == "delete":
            return f"{self.path} ({self.current_size} bytes → deleted)"
        elif self.action == "create":
            return f"{self.path} (created, {self.backup_size} bytes)"
        else:
            delta = self.current_size - self.backup_size
            sign = "+" if delta > 0 else ""
            return f"{self.path} ({self.current_size} bytes → {self.backup_size} bytes, {sign}{delta})"


@dataclass
class RewindPreview:
    """Preview of what a rewind operation would do, without executing it."""

    checkpoint: Checkpoint
    files_to_change: list[FileChange]
    messages_to_remove: int
    message_snapshot: str       # preview of the last message that would remain

    def has_changes(self) -> bool:
        return len(self.files_to_change) > 0 or self.messages_to_remove > 0


@dataclass
class RewindResult:
    """Result of an executed rewind operation."""

    success: bool
    changed_files: list[str] = field(default_factory=list)
    messages_removed: int = 0
    checkpoint: Checkpoint | None = None
    error: str = ""


def new_checkpoint_id() -> str:
    """Generate a short unique checkpoint ID."""
    return uuid.uuid4().hex[:12]
