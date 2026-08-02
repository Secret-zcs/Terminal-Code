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
SkillApprovalStatus = Literal["pending", "approved", "rejected"]
SelfEvolutionReviewRunStatus = Literal[
    "running",
    "idle",
    "generated",
    "submitted",
    "failed",
]


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


@dataclass
class SkillApprovalRequest:
    id: str
    proposal_id: str
    skill_name: str
    approval_mode: str
    candidate_skill: str
    eval_report: str
    eval_report_markdown: str
    source: str = "self-evolution-review"
    status: SkillApprovalStatus = "pending"
    created_at: float = field(default_factory=time.time)
    resolved_at: float = 0.0
    reviewer: str = ""
    resolution_reason: str = ""
    result_path: str = ""
    usage_baseline_count: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "proposal_id": self.proposal_id,
            "skill_name": self.skill_name,
            "approval_mode": self.approval_mode,
            "candidate_skill": self.candidate_skill,
            "eval_report": self.eval_report,
            "eval_report_markdown": self.eval_report_markdown,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "reviewer": self.reviewer,
            "resolution_reason": self.resolution_reason,
            "result_path": self.result_path,
            "usage_baseline_count": self.usage_baseline_count,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "SkillApprovalRequest":
        return cls(
            id=data["id"],
            proposal_id=data["proposal_id"],
            skill_name=data["skill_name"],
            approval_mode=data["approval_mode"],
            candidate_skill=data["candidate_skill"],
            eval_report=data["eval_report"],
            eval_report_markdown=data["eval_report_markdown"],
            source=data.get("source", "self-evolution-review"),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", 0.0),
            resolved_at=data.get("resolved_at", 0.0),
            reviewer=data.get("reviewer", ""),
            resolution_reason=data.get("resolution_reason", ""),
            result_path=data.get("result_path", ""),
            usage_baseline_count=data.get("usage_baseline_count"),
        )

    @classmethod
    def from_jsonl(cls, line: str) -> "SkillApprovalRequest":
        return cls.from_dict(json.loads(line))


@dataclass
class SelfEvolutionReviewRun:
    id: str
    mode: str
    status: SelfEvolutionReviewRunStatus
    approval_mode: str
    artifacts: dict = field(default_factory=dict)
    policy: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "approval_mode": self.approval_mode,
            "artifacts": self.artifacts,
            "policy": self.policy,
            "summary": self.summary,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "SelfEvolutionReviewRun":
        return cls(
            id=data["id"],
            mode=data.get("mode", "fork_reviewer"),
            status=data.get("status", "running"),
            approval_mode=data.get("approval_mode", "manual"),
            artifacts=data.get("artifacts", {}),
            policy=data.get("policy", {}),
            summary=data.get("summary", {}),
            created_at=data.get("created_at", 0.0),
            completed_at=data.get("completed_at", 0.0),
            error=data.get("error", ""),
        )

    @classmethod
    def from_jsonl(cls, line: str) -> "SelfEvolutionReviewRun":
        return cls.from_dict(json.loads(line))
