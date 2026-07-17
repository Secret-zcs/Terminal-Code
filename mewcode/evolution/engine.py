"""Hermes-style self-evolution engine.

This module implements a conservative self-evolution loop:

observe -> propose -> validate -> approve -> apply

Only memory proposals are directly applied in the first implementation. More
invasive targets such as code, prompts, tools, or skills remain auditable
proposals until a dedicated implementation path exists.
"""

from __future__ import annotations

import time
from pathlib import Path

from mewcode.evolution.models import (
    EvolutionEvidence,
    EvolutionProposal,
    EvolutionValidation,
    EvidenceKind,
    ProposalRisk,
    ProposalTarget,
    new_evolution_id,
)
from mewcode.evolution.store import EvolutionStore

PROJECT_MEMORY_HEADER = "### 项目知识"


class EvolutionEngine:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self.store = EvolutionStore(self.project_root)

    @property
    def project_memory_path(self) -> Path:
        return self.project_root / ".mewcode" / "memories.md"

    def record_evidence(
        self,
        summary: str,
        *,
        kind: EvidenceKind = "manual",
        source: str = "manual",
        metadata: dict | None = None,
    ) -> EvolutionEvidence:
        clean = summary.strip()
        if not clean:
            raise ValueError("evidence summary cannot be empty")
        evidence = EvolutionEvidence(
            id=new_evolution_id("ev"),
            kind=kind,
            summary=clean,
            source=source,
            metadata=metadata or {},
        )
        self.store.save_evidence(evidence)
        return evidence

    def propose(
        self,
        title: str,
        change: str,
        *,
        rationale: str = "",
        target: ProposalTarget = "memory",
        evidence_ids: list[str] | None = None,
        risk: ProposalRisk = "low",
    ) -> EvolutionProposal:
        clean_title = title.strip()
        clean_change = change.strip()
        if not clean_title:
            raise ValueError("proposal title cannot be empty")
        if not clean_change:
            raise ValueError("proposal change cannot be empty")
        ids = evidence_ids if evidence_ids is not None else self.store.recent_evidence_ids()
        proposal = EvolutionProposal(
            id=new_evolution_id("prop"),
            title=clean_title,
            rationale=rationale.strip() or "Generated from recorded evolution evidence.",
            target=target,
            change=clean_change,
            evidence_ids=ids,
            risk=risk,
        )
        self.store.save_proposal(proposal)
        return proposal

    def validate(self, proposal: EvolutionProposal) -> EvolutionValidation:
        errors: list[str] = []
        warnings: list[str] = []

        if proposal.status not in {"proposed", "approved"}:
            errors.append(f"proposal status must be proposed or approved, got {proposal.status}")
        if proposal.target != "memory":
            errors.append(
                f"target '{proposal.target}' is proposal-only in this implementation"
            )
        if not proposal.change.strip():
            errors.append("proposal change is empty")
        if len(proposal.change) > 500:
            warnings.append("memory change is long; consider splitting it")

        known = {e.id for e in self.store.load_evidence()}
        missing = [e for e in proposal.evidence_ids if e not in known]
        if missing:
            warnings.append("proposal references missing evidence: " + ", ".join(missing))
        if not proposal.evidence_ids:
            warnings.append("proposal has no evidence ids")
        if proposal.risk != "low":
            warnings.append(f"risk is {proposal.risk}; require extra review before applying")

        return EvolutionValidation(ok=not errors, errors=errors, warnings=warnings)

    def approve(self, proposal_id: str) -> EvolutionProposal | None:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            return None
        if proposal.status != "proposed":
            return proposal
        proposal.status = "approved"
        self.store.update_proposal(proposal)
        return proposal

    def reject(self, proposal_id: str) -> EvolutionProposal | None:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            return None
        if proposal.status != "applied":
            proposal.status = "rejected"
            self.store.update_proposal(proposal)
        return proposal

    def apply(self, proposal_id: str) -> tuple[bool, str]:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            return False, f"proposal {proposal_id} not found"
        if proposal.status != "approved":
            return False, f"proposal {proposal_id} must be approved before apply"

        validation = self.validate(proposal)
        if not validation.ok:
            return False, "; ".join(validation.errors)

        if proposal.target == "memory":
            self._append_project_memory(proposal.change)
        else:
            return False, f"target {proposal.target} cannot be applied automatically"

        proposal.status = "applied"
        proposal.applied_at = time.time()
        self.store.update_proposal(proposal)
        return True, str(self.project_memory_path)

    def _append_project_memory(self, change: str) -> None:
        path = self.project_memory_path
        path.parent.mkdir(parents=True, exist_ok=True)
        bullet = change.strip()
        if not bullet.startswith("- "):
            bullet = "- " + bullet

        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if bullet in existing.splitlines():
            return

        if not existing.strip():
            path.write_text(PROJECT_MEMORY_HEADER + "\n" + bullet + "\n", encoding="utf-8")
            return

        if PROJECT_MEMORY_HEADER not in existing:
            suffix = "" if existing.endswith("\n") else "\n"
            path.write_text(
                existing + suffix + "\n" + PROJECT_MEMORY_HEADER + "\n" + bullet + "\n",
                encoding="utf-8",
            )
            return

        lines = existing.splitlines()
        out: list[str] = []
        inserted = False
        for i, line in enumerate(lines):
            out.append(line)
            if line.strip() == PROJECT_MEMORY_HEADER and not inserted:
                next_is_item = i + 1 < len(lines) and lines[i + 1].startswith("- ")
                if not next_is_item:
                    out.append(bullet)
                    inserted = True
        if not inserted:
            out.append(bullet)
        path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
