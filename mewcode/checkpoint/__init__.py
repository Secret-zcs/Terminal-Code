"""Checkpoint / Rewind system — snapshot and restore session state.

Provides:
- CheckpointManager: orchestrator that wraps FileHistory + CheckpointStore
- CheckpointStore: JSONL-backed persistent storage for checkpoint metadata
- Checkpoint, RewindPreview, RewindResult: core data models
"""

from mewcode.checkpoint.manager import CheckpointManager
from mewcode.checkpoint.models import (
    Checkpoint,
    FileChange,
    RewindPreview,
    RewindResult,
    TriggerType,
    new_checkpoint_id,
)
from mewcode.checkpoint.store import CheckpointStore

__all__ = [
    "Checkpoint",
    "CheckpointManager",
    "CheckpointStore",
    "FileChange",
    "RewindPreview",
    "RewindResult",
    "TriggerType",
    "new_checkpoint_id",
]
