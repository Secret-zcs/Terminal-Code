"""JSONL-backed persistent storage for checkpoint metadata.

Follows the same atomic-write pattern as memory/session.py:
write to .tmp first, then rename — crash-safe by construction.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from mewcode.checkpoint.models import Checkpoint


class CheckpointStore:
    """Persistent JSONL store for checkpoint metadata.

    Each line is a JSON object representing one Checkpoint.
    The file grows append-only; rewind truncates by rewriting.
    """

    def __init__(self, session_dir: Path) -> None:
        self._path = session_dir / "checkpoints.jsonl"
        self._undo_path = session_dir / "undo_checkpoint.json"
        self._lock = threading.Lock()

    # -----------------------------------------------------------------
    # 基本 CRUD
    # -----------------------------------------------------------------

    def save(self, cp: Checkpoint) -> None:
        """Append a checkpoint to the JSONL file (atomic write)."""
        line = cp.to_jsonl() + "\n"
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            # 如果文件已存在，先复制内容再追加
            if self._path.exists():
                existing = self._path.read_text(encoding="utf-8")
                tmp.write_text(existing + line, encoding="utf-8")
            else:
                tmp.write_text(line, encoding="utf-8")
            tmp.rename(self._path)

    def load_all(self) -> list[Checkpoint]:
        """Load all checkpoints from disk, ordered by seq."""
        if not self._path.exists():
            return []
        checkpoints: list[Checkpoint] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                checkpoints.append(Checkpoint.from_jsonl(stripped))
            except (json.JSONDecodeError, KeyError):
                # 跳过损坏的行
                continue
        return checkpoints

    def delete_from(self, seq: int) -> None:
        """Delete all checkpoints with seq >= N (truncation after rewind).

        Rewrites the entire file, keeping only checkpoints with seq < N.
        """
        with self._lock:
            all_cps = self.load_all()
            kept = [cp for cp in all_cps if cp.seq < seq]
            tmp = self._path.with_suffix(".tmp")
            content = "".join(cp.to_jsonl() + "\n" for cp in kept)
            tmp.write_text(content, encoding="utf-8")
            tmp.rename(self._path)

    def count(self) -> int:
        return len(self.load_all())

    def last_seq(self) -> int:
        """Return the highest seq number, or 0 if empty."""
        all_cps = self.load_all()
        if not all_cps:
            return 0
        return max(cp.seq for cp in all_cps)

    # -----------------------------------------------------------------
    # Undo checkpoint (single file, not JSONL)
    # -----------------------------------------------------------------

    def save_undo(self, cp: Checkpoint) -> None:
        """Save a checkpoint for potential undo of the last rewind."""
        with self._lock:
            tmp = self._undo_path.with_suffix(".tmp")
            tmp.write_text(cp.to_jsonl(), encoding="utf-8")
            tmp.rename(self._undo_path)

    def load_undo(self) -> Checkpoint | None:
        """Load the undo checkpoint, if one exists."""
        if not self._undo_path.exists():
            return None
        try:
            line = self._undo_path.read_text(encoding="utf-8").strip()
            if line:
                return Checkpoint.from_jsonl(line)
        except (json.JSONDecodeError, KeyError, OSError):
            pass
        return None

    def clear_undo(self) -> None:
        """Remove the undo checkpoint file."""
        with self._lock:
            if self._undo_path.exists():
                self._undo_path.unlink()
