"""Persistent storage for Hermes-style evolution evidence and proposals."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable, TypeVar

from mewcode.evolution.models import EvolutionEvidence, EvolutionProposal

T = TypeVar("T")


class EvolutionStore:
    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root)
        self.dir = self.root / ".mewcode" / "evolution"
        self.evidence_path = self.dir / "evidence.jsonl"
        self.proposals_path = self.dir / "proposals.jsonl"
        self._lock = threading.Lock()

    def save_evidence(self, evidence: EvolutionEvidence) -> None:
        self._append(self.evidence_path, evidence.to_jsonl())

    def load_evidence(self) -> list[EvolutionEvidence]:
        return self._load_jsonl(self.evidence_path, EvolutionEvidence.from_jsonl)

    def save_proposal(self, proposal: EvolutionProposal) -> None:
        self._append(self.proposals_path, proposal.to_jsonl())

    def load_proposals(self) -> list[EvolutionProposal]:
        return self._load_jsonl(self.proposals_path, EvolutionProposal.from_jsonl)

    def get_evidence(self, evidence_id: str) -> EvolutionEvidence | None:
        for evidence in self.load_evidence():
            if evidence.id == evidence_id:
                return evidence
        return None

    def get_proposal(self, proposal_id: str) -> EvolutionProposal | None:
        for proposal in self.load_proposals():
            if proposal.id == proposal_id:
                return proposal
        return None

    def update_proposal(self, updated: EvolutionProposal) -> None:
        with self._lock:
            proposals = self.load_proposals()
            replaced = False
            for i, proposal in enumerate(proposals):
                if proposal.id == updated.id:
                    proposals[i] = updated
                    replaced = True
                    break
            if not replaced:
                proposals.append(updated)
            self._rewrite(self.proposals_path, [p.to_jsonl() for p in proposals])

    def recent_evidence_ids(self, limit: int = 5) -> list[str]:
        evidence = self.load_evidence()
        evidence.sort(key=lambda e: e.created_at, reverse=True)
        return [e.id for e in evidence[:limit]]

    def _append(self, path: Path, line: str) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            tmp.write_text(existing + line + "\n", encoding="utf-8")
            tmp.rename(path)

    def _rewrite(self, path: Path, lines: list[str]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        tmp.rename(path)

    @staticmethod
    def _load_jsonl(path: Path, parser: Callable[[str], T]) -> list[T]:
        if not path.exists():
            return []
        results: list[T] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                results.append(parser(stripped))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return results
