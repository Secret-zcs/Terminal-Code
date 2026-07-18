"""Data models for Hermes-style self-evolution.

The evolution system is intentionally proposal-driven: observations become
evidence, evidence becomes proposals, and only approved proposals may be
applied to project state.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

EvidenceKind = Literal[
    "manual",
    "success",
    "failure",
    "user_feedback",
    "test_result",
    "rewind",
]

ProposalTarget = Literal["memory", "skill"]
ProposalStatus = Literal["proposed", "approved", "rejected", "applied"]
ProposalRisk = Literal["low", "medium", "high"]


def new_evolution_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class EvolutionEvidence:
    id: str
    kind: EvidenceKind
    summary: str
    source: str = "manual"
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary,
            "source": self.source,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "EvolutionEvidence":
        return cls(
            id=data["id"],
            kind=data["kind"],
            summary=data["summary"],
            source=data.get("source", "manual"),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", 0.0),
        )

    @classmethod
    def from_jsonl(cls, line: str) -> "EvolutionEvidence":
        return cls.from_dict(json.loads(line))


@dataclass
class EvolutionProposal:
    id: str
    title: str
    rationale: str
    target: ProposalTarget
    change: str
    evidence_ids: list[str]
    risk: ProposalRisk = "low"
    status: ProposalStatus = "proposed"
    created_at: float = field(default_factory=time.time)
    applied_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "rationale": self.rationale,
            "target": self.target,
            "change": self.change,
            "evidence_ids": self.evidence_ids,
            "risk": self.risk,
            "status": self.status,
            "created_at": self.created_at,
            "applied_at": self.applied_at,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "EvolutionProposal":
        return cls(
            id=data["id"],
            title=data["title"],
            rationale=data["rationale"],
            target=data["target"],
            change=data["change"],
            evidence_ids=list(data.get("evidence_ids", [])),
            risk=data.get("risk", "low"),
            status=data.get("status", "proposed"),
            created_at=data.get("created_at", 0.0),
            applied_at=data.get("applied_at", 0.0),
        )

    @classmethod
    def from_jsonl(cls, line: str) -> "EvolutionProposal":
        return cls.from_dict(json.loads(line))


@dataclass
class EvolutionValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
