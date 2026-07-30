"""Hermes-style self-evolution engine.

This module implements a conservative self-evolution loop:

observe -> propose -> validate -> approve -> apply

Memory proposals and validated project skill proposals can be applied.
Runtime self-evolution intentionally excludes code, prompt, and tool targets.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from difflib import unified_diff
from pathlib import Path
from typing import Any, AsyncIterator

import yaml

from mewcode.client import LLMClient
from mewcode.conversation import ConversationManager
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
from mewcode.skills.parser import (
    VALID_CONTEXTS,
    VALID_MODES,
    VALID_NAME_RE,
    SkillParseError,
    parse_skill_file,
    substitute_arguments,
)
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete

PROJECT_MEMORY_HEADER = "### 项目知识"
SUPPORTED_EVOLUTION_TARGETS = {"memory", "skill"}
SUPPORTED_EXECUTION_RUNNERS = {"deterministic_replay", "agent_loop_scripted"}
MIN_EXECUTION_EVAL_CASES = 3
NEGATIVE_SKILL_USAGE_EVENTS = {"failure", "user_feedback"}
DANGEROUS_SKILL_PATTERNS = (
    "rm -rf /",
    "sudo rm -rf",
    "chmod 777 /",
    "curl | sh",
    "curl -s | sh",
    "wget -qO-",
)


def _resolve_workspace_relative_path(workspace_path: Path, relative_path: str) -> Path:
    if not relative_path:
        raise ValueError("workspace path cannot be empty")
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"workspace path must be relative: {relative_path}")
    root = workspace_path.resolve()
    resolved = (workspace_path / candidate).resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f"workspace path escapes sandbox: {relative_path}")
    return resolved


class _ScriptedAgentLoopClient(LLMClient):
    def __init__(self, workspace_path: Path, turns: list) -> None:
        self.workspace_path = workspace_path
        self.turns = turns
        self.next_turn = 0
        self.turn_records: list[dict] = []
        self.tool_to_turn: dict[str, dict] = {}
        self.tool_paths: dict[str, str] = {}
        self.errors: list[str] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if self.next_turn < len(self.turns):
            raw_turn = self.turns[self.next_turn]
            self.next_turn += 1
            if isinstance(raw_turn, dict):
                assistant = str(raw_turn.get("assistant", ""))
                raw_tool_calls = raw_turn.get("tool_calls", [])
                if not isinstance(raw_tool_calls, list):
                    self.errors.append(
                        f"scripted agent turn {self.next_turn} tool_calls must be a list"
                    )
                    raw_tool_calls = []
            else:
                assistant = ""
                raw_tool_calls = []
                self.errors.append(
                    f"scripted agent turn {self.next_turn} must be an object"
                )
        else:
            self.next_turn += 1
            assistant = "完成。"
            raw_tool_calls = []

        record = {
            "turn": self.next_turn,
            "assistant": assistant,
            "events": [],
            "tool_results": [],
        }
        self.turn_records.append(record)

        yield TextDelta(assistant)
        for call_index, raw_call in enumerate(raw_tool_calls, 1):
            if not isinstance(raw_call, dict):
                self.errors.append(
                    f"scripted tool call {call_index} in turn {self.next_turn} must be an object"
                )
                continue
            tool_name = str(raw_call.get("tool") or raw_call.get("name") or "").strip()
            tool_id = f"scripted_turn_{self.next_turn}_tool_{call_index}"
            arguments, original_path = self._build_arguments(tool_name, raw_call)
            self.tool_to_turn[tool_id] = record
            self.tool_paths[tool_id] = original_path
            yield ToolCallComplete(
                tool_id=tool_id,
                tool_name=tool_name,
                arguments=arguments,
            )

        yield StreamEnd(
            stop_reason="tool_use" if raw_tool_calls else "end_turn",
            input_tokens=1,
            output_tokens=1,
        )

    def _build_arguments(self, tool_name: str, call: dict) -> tuple[dict[str, Any], str]:
        raw_arguments = call.get("arguments", {})
        arguments: dict[str, Any] = (
            dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        )
        path_text = str(
            call.get("path")
            or call.get("file_path")
            or arguments.get("file_path")
            or ""
        )
        original_path = path_text
        if tool_name in {"ReadFile", "WriteFile"}:
            try:
                target = _resolve_workspace_relative_path(self.workspace_path, path_text)
                arguments["file_path"] = str(target)
            except ValueError as exc:
                self.errors.append(str(exc))
                arguments["file_path"] = str(self.workspace_path / "__invalid_path__")
            if tool_name == "WriteFile":
                arguments["content"] = str(
                    call.get("content", arguments.get("content", ""))
                )
            if "offset" in call:
                arguments["offset"] = call["offset"]
            if "limit" in call:
                arguments["limit"] = call["limit"]
        return arguments, original_path


class EvolutionEngine:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)
        self.store = EvolutionStore(self.project_root)

    @property
    def project_memory_path(self) -> Path:
        return self.project_root / ".mewcode" / "memories.md"

    @property
    def project_skills_path(self) -> Path:
        return self.project_root / ".mewcode" / "skills"

    @property
    def candidate_skills_path(self) -> Path:
        return self.project_root / ".mewcode" / "evolution" / "candidates"

    @property
    def evals_path(self) -> Path:
        return self.project_root / ".mewcode" / "evolution" / "evals"

    @property
    def skill_usage_path(self) -> Path:
        return self.project_root / ".mewcode" / "evolution" / "skill_usage.jsonl"

    @property
    def quarantine_skills_path(self) -> Path:
        return self.project_root / ".mewcode" / "evolution" / "quarantine"

    def candidate_dir(self, proposal_id: str) -> Path:
        return self.candidate_skills_path / proposal_id

    def candidate_skill_path(self, proposal_id: str) -> Path:
        return self.candidate_dir(proposal_id) / "SKILL.md"

    def candidate_manifest_path(self, proposal_id: str) -> Path:
        return self.candidate_dir(proposal_id) / "manifest.json"

    def execution_eval_report_path(self, proposal_id: str) -> Path:
        return self.candidate_dir(proposal_id) / "eval_report.json"

    def execution_eval_markdown_path(self, proposal_id: str) -> Path:
        return self.candidate_dir(proposal_id) / "eval_report.md"

    def execution_eval_sandbox_path(self, proposal_id: str) -> Path:
        return self.candidate_dir(proposal_id) / "execution_sandbox"

    def eval_cases_path(self, skill_name: str) -> Path:
        return self.evals_path / skill_name / "cases.jsonl"

    def proposal_target_path(self, proposal: EvolutionProposal) -> Path:
        if proposal.target == "memory":
            return self.project_memory_path
        if proposal.target == "skill":
            payload = self._decode_skill_change(proposal.change)
            return self._skill_target_path(payload)
        return self.project_root

    def has_project_skill(self, name: str) -> bool:
        return self._existing_project_skill_path(name.strip()) is not None

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
        if target not in SUPPORTED_EVOLUTION_TARGETS:
            raise ValueError(
                f"unsupported evolution target '{target}'; "
                "Hermes-style evolution only supports memory and skill"
            )
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

    def propose_skill(
        self,
        *,
        name: str,
        description: str,
        body: str,
        allowed_tools: list[str] | None = None,
        mode: str = "inline",
        context: str = "recent",
        rationale: str = "",
        evidence_ids: list[str] | None = None,
        risk: ProposalRisk = "medium",
    ) -> EvolutionProposal:
        payload = {
            "action": "create",
            "name": name.strip(),
            "description": description.strip(),
            "mode": mode.strip(),
            "context": context.strip(),
            "allowedTools": allowed_tools or [],
            "body": body.strip(),
        }
        proposal = self.propose(
            title=f"create-skill-{payload['name']}",
            change=json.dumps(payload, ensure_ascii=False, indent=2),
            target="skill",
            rationale=(
                rationale.strip()
                or "Hermes-style reusable workflow distilled into a project skill."
            ),
            evidence_ids=evidence_ids,
            risk=risk,
        )
        self._write_candidate_skill(proposal, payload)
        return proposal

    def propose_skill_patch(
        self,
        *,
        name: str,
        description: str,
        body: str,
        allowed_tools: list[str] | None = None,
        mode: str | None = None,
        context: str | None = None,
        rationale: str = "",
        evidence_ids: list[str] | None = None,
        risk: ProposalRisk = "medium",
    ) -> EvolutionProposal:
        clean_name = name.strip()
        existing = self._load_existing_project_skill(clean_name)
        payload = {
            "action": "patch",
            "name": clean_name,
            "description": description.strip() or (
                existing.description if existing is not None else ""
            ),
            "mode": (mode.strip() if mode else None)
            or (existing.mode if existing is not None else "inline"),
            "context": (context.strip() if context else None)
            or (existing.context if existing is not None else "recent"),
            "allowedTools": allowed_tools
            if allowed_tools is not None
            else (existing.allowed_tools if existing is not None else []),
            "body": body.strip(),
        }
        proposal = self.propose(
            title=f"patch-skill-{payload['name']}",
            change=json.dumps(payload, ensure_ascii=False, indent=2),
            target="skill",
            rationale=(
                rationale.strip()
                or "Hermes-style learning patched an existing project skill first."
            ),
            evidence_ids=evidence_ids,
            risk=risk,
        )
        self._write_candidate_skill(proposal, payload)
        return proposal

    def propose_skill_patch_from_usage(
        self,
        skill_name: str,
        *,
        failure_threshold: int = 2,
    ) -> EvolutionProposal:
        clean_name = skill_name.strip()
        existing = self._load_existing_project_skill(clean_name)
        if existing is None:
            raise ValueError(f"skill '{clean_name}' does not exist as a project skill")

        records: list[dict] = []
        for record in self.load_skill_usage():
            name = str(record.get("skill_name", "")).strip()
            event = str(record.get("event", "")).strip()
            if name != clean_name:
                continue
            if event == "quarantine":
                records = []
                continue
            if event in NEGATIVE_SKILL_USAGE_EVENTS:
                records.append(record)
        if len(records) < max(1, failure_threshold):
            raise ValueError(
                f"skill '{clean_name}' does not have enough negative usage events"
            )

        summaries = [
            str(record.get("metadata", {}).get("summary", "")).strip()
            for record in records
            if isinstance(record.get("metadata"), dict)
        ]
        summaries = [summary for summary in summaries if summary]
        feedback = "\n".join(f"- {summary}" for summary in summaries)
        body = (
            existing.prompt_body.rstrip()
            + "\n\n## Usage Feedback Patch Notes\n\n"
            + "Address these observed failures before this skill is used again:\n"
            + (feedback or "- Negative usage was recorded without a summary.")
            + "\n\n## Required Patch Behavior\n\n"
            + "- Make the SOP auditable against the listed feedback.\n"
            + "- Add or update eval cases before promotion.\n"
        )
        evidence = self.record_evidence(
            f"Usage feedback suggests patching skill '{clean_name}'.",
            kind="user_feedback",
            source="skill-usage",
            metadata={"skill": clean_name, "summaries": summaries},
        )
        return self.propose_skill_patch(
            name=clean_name,
            description=existing.description,
            body=body,
            allowed_tools=existing.allowed_tools,
            mode=existing.mode,
            context=existing.context,
            rationale="Usage feedback generated a conservative skill patch proposal.",
            evidence_ids=[evidence.id],
        )

    def validate(self, proposal: EvolutionProposal) -> EvolutionValidation:
        errors: list[str] = []
        warnings: list[str] = []

        if proposal.status not in {"proposed", "approved"}:
            errors.append(f"proposal status must be proposed or approved, got {proposal.status}")

        if proposal.target == "memory":
            self._validate_memory_proposal(proposal, errors, warnings)
        elif proposal.target == "skill":
            self._validate_skill_proposal(proposal, errors, warnings)
        else:
            errors.append(
                f"unsupported evolution target '{proposal.target}'"
            )

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

    def add_eval_case(
        self,
        proposal_id: str,
        *,
        task: str,
        must_contain: list[str],
        must_not_contain: list[str] | None = None,
        workspace_files: dict[str, str] | None = None,
        scripted_tool_calls: list[dict] | None = None,
        scripted_agent_turns: list[dict] | None = None,
        expected_files: dict[str, str] | None = None,
        execution_runner: str = "deterministic_replay",
        case_id: str | None = None,
    ) -> str:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"proposal {proposal_id} not found")
        if proposal.target != "skill":
            raise ValueError(f"proposal {proposal_id} is not a skill proposal")

        payload = self._decode_skill_change(proposal.change)
        skill_name = str(payload["name"])
        if not VALID_NAME_RE.match(skill_name):
            raise ValueError("invalid skill name for eval case")
        clean_task = task.strip()
        required = [term.strip() for term in must_contain if term.strip()]
        forbidden = [
            term.strip()
            for term in (must_not_contain or [])
            if term.strip()
        ]
        if not clean_task:
            raise ValueError("eval case task cannot be empty")
        if not required:
            raise ValueError("eval case must_contain cannot be empty")
        runner = execution_runner.strip() or "deterministic_replay"
        if runner not in SUPPORTED_EXECUTION_RUNNERS:
            raise ValueError(
                "execution_runner must be one of "
                f"{sorted(SUPPORTED_EXECUTION_RUNNERS)}"
            )

        eval_case = {
            "id": case_id or new_evolution_id("case"),
            "proposal_id": proposal.id,
            "skill_name": skill_name,
            "task": clean_task,
            "must_contain": required,
            "must_not_contain": forbidden,
            "execution_runner": runner,
            "created_at": time.time(),
        }
        if workspace_files:
            eval_case["workspace_files"] = dict(workspace_files)
        if scripted_tool_calls:
            eval_case["scripted_tool_calls"] = list(scripted_tool_calls)
        if scripted_agent_turns:
            eval_case["scripted_agent_turns"] = list(scripted_agent_turns)
        if expected_files:
            eval_case["expected_files"] = dict(expected_files)
        path = self.eval_cases_path(skill_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(
            existing + json.dumps(eval_case, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._invalidate_candidate_eval(proposal)
        return str(eval_case["id"])

    def suggest_eval_cases(
        self,
        proposal_id: str,
        *,
        count: int = MIN_EXECUTION_EVAL_CASES,
    ) -> list[dict]:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"proposal {proposal_id} not found")
        if proposal.target != "skill":
            raise ValueError(f"proposal {proposal_id} is not a skill proposal")

        payload = self._decode_skill_change(proposal.change)
        skill_name = str(payload["name"])
        if not VALID_NAME_RE.match(skill_name):
            raise ValueError("invalid skill name for eval case suggestions")

        terms = self._suggestion_terms_for_proposal(proposal, payload)
        target_count = max(1, count)
        suggestions: list[dict] = []
        for index in range(target_count):
            term_info = terms[index % len(terms)]
            term = term_info["term"]
            task = f"验证 {skill_name} 能处理 usage 反馈：{term}"
            must_contain = [term]
            suggestion = {
                "proposal_id": proposal.id,
                "skill_name": skill_name,
                "task": task,
                "must_contain": must_contain,
                "must_not_contain": [],
                "quality": term_info["quality"],
                "score": term_info["score"],
                "coverage": term_info["coverage"],
                "rationale": term_info["rationale"],
            }
            suggestion["command"] = self._render_add_eval_case_command(
                proposal.id,
                task,
                must_contain,
                [],
            )
            suggestions.append(suggestion)
        return suggestions

    def review_eval_case_suggestions(
        self,
        proposal_id: str,
        *,
        count: int = MIN_EXECUTION_EVAL_CASES,
    ) -> dict:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError(f"proposal {proposal_id} not found")
        if proposal.target != "skill":
            raise ValueError(f"proposal {proposal_id} is not a skill proposal")
        suggestions = self.suggest_eval_cases(proposal_id, count=count)
        quality_counts = {"high": 0, "medium": 0, "low": 0}
        coverage_counts: dict[str, int] = {}
        for suggestion in suggestions:
            quality = str(suggestion.get("quality", "low"))
            quality_counts[quality] = quality_counts.get(quality, 0) + 1
            coverage = str(suggestion.get("coverage", "unknown"))
            coverage_counts[coverage] = coverage_counts.get(coverage, 0) + 1

        warnings: list[str] = []
        if quality_counts.get("high", 0) == 0:
            warnings.append("no high-quality usage feedback eval case suggestions")
        if coverage_counts.get("usage_feedback", 0) == 0:
            warnings.append("no suggestion directly covers recorded usage feedback")
        usage_feedback_terms = self._usage_feedback_terms_for_proposal(proposal)
        covered_feedback = {
            term
            for suggestion in suggestions
            if suggestion.get("coverage") == "usage_feedback"
            for term in suggestion.get("must_contain", [])
        }
        uncovered_usage_feedback = [
            term for term in usage_feedback_terms if term not in covered_feedback
        ]
        if uncovered_usage_feedback:
            warnings.append(
                f"{len(uncovered_usage_feedback)} usage feedback summaries are not "
                "covered by suggested eval cases"
            )

        proposal_id_value = suggestions[0]["proposal_id"] if suggestions else proposal_id
        skill_name = suggestions[0]["skill_name"] if suggestions else ""
        if uncovered_usage_feedback:
            recommendation = (
                "Increase count or add manual eval cases for uncovered usage feedback "
                "before adding these suggestions."
            )
        elif quality_counts.get("high", 0):
            recommendation = (
                "Add high-quality usage feedback cases first, then review medium/low "
                "structural guards before adding them."
            )
        else:
            recommendation = (
                "Add real usage-feedback eval cases before relying on these suggestions."
            )
        return {
            "proposal_id": proposal_id_value,
            "skill_name": skill_name,
            "suggestions": suggestions,
            "quality_counts": quality_counts,
            "coverage_counts": coverage_counts,
            "uncovered_usage_feedback": uncovered_usage_feedback,
            "warnings": warnings,
            "recommendation": recommendation,
        }

    def record_skill_usage(
        self,
        skill_name: str,
        *,
        event: str,
        source: str = "manual",
        metadata: dict | None = None,
    ) -> dict:
        clean_name = skill_name.strip()
        clean_event = event.strip()
        if not VALID_NAME_RE.match(clean_name):
            raise ValueError("invalid skill name for usage log")
        if not clean_event:
            raise ValueError("skill usage event cannot be empty")
        record = {
            "id": new_evolution_id("use"),
            "skill_name": clean_name,
            "event": clean_event,
            "source": source.strip() or "manual",
            "metadata": metadata or {},
            "created_at": time.time(),
        }
        self.skill_usage_path.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            self.skill_usage_path.read_text(encoding="utf-8")
            if self.skill_usage_path.exists()
            else ""
        )
        self.skill_usage_path.write_text(
            existing + json.dumps(record, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return record

    def load_skill_usage(self) -> list[dict]:
        if not self.skill_usage_path.exists():
            return []
        records: list[dict] = []
        for line in self.skill_usage_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(data)
        return records

    def suggest_quarantine(
        self,
        skill_name: str | None = None,
        *,
        failure_threshold: int = 2,
    ) -> list[dict]:
        threshold = max(1, failure_threshold)
        wanted = skill_name.strip() if skill_name else ""
        if wanted and not VALID_NAME_RE.match(wanted):
            return []

        by_skill: dict[str, list[dict]] = {}
        for record in self.load_skill_usage():
            name = str(record.get("skill_name", "")).strip()
            event = str(record.get("event", "")).strip()
            if not VALID_NAME_RE.match(name):
                continue
            if wanted and name != wanted:
                continue
            if event == "quarantine":
                by_skill[name] = []
                continue
            if event in NEGATIVE_SKILL_USAGE_EVENTS:
                by_skill.setdefault(name, []).append(record)

        suggestions: list[dict] = []
        for name, records in sorted(by_skill.items()):
            if len(records) < threshold:
                continue
            if self._existing_project_skill_path(name) is None:
                continue
            summaries = [
                str(record.get("metadata", {}).get("summary", "")).strip()
                for record in records
                if isinstance(record.get("metadata"), dict)
            ]
            summaries = [summary for summary in summaries if summary]
            suggestions.append({
                "skill_name": name,
                "negative_events": len(records),
                "events": [str(record.get("event", "")) for record in records],
                "summaries": summaries,
                "command": (
                    f"/evolve quarantine {name} :: "
                    f"{len(records)} negative usage events"
                ),
            })
        return suggestions

    def quarantine_skill(self, skill_name: str, *, reason: str = "") -> tuple[bool, str]:
        clean_name = skill_name.strip()
        if not VALID_NAME_RE.match(clean_name):
            return False, "invalid skill name"
        existing_skill = self._existing_project_skill_path(clean_name)
        if existing_skill is None:
            return False, f"project skill '{clean_name}' not found"

        if existing_skill.name == "SKILL.md" and existing_skill.parent.parent == self.project_skills_path:
            source_path = existing_skill.parent
            destination_path = self.quarantine_skills_path / clean_name
            quarantined_skill = destination_path / "SKILL.md"
        else:
            source_path = existing_skill
            destination_path = self.quarantine_skills_path / clean_name
            quarantined_skill = destination_path / existing_skill.name

        if destination_path.exists():
            destination_path = (
                self.quarantine_skills_path
                / f"{clean_name}-{new_evolution_id('q')}"
            )
            quarantined_skill = (
                destination_path / "SKILL.md"
                if source_path.is_dir()
                else destination_path / existing_skill.name
            )

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.is_dir():
            shutil.move(str(source_path), str(destination_path))
        else:
            destination_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(quarantined_skill))

        self.record_skill_usage(
            clean_name,
            event="quarantine",
            source="evolve",
            metadata={
                "reason": reason.strip(),
                "quarantine_path": str(quarantined_skill),
            },
        )
        return True, str(quarantined_skill)

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
            applied_path = self.project_memory_path
        elif proposal.target == "skill":
            return (
                False,
                "skill proposals must be promoted with /evolve promote after review",
            )
        else:
            return False, f"target {proposal.target} cannot be applied automatically"

        proposal.status = "applied"
        proposal.applied_at = time.time()
        self.store.update_proposal(proposal)
        return True, str(applied_path)

    def preview(self, proposal_id: str) -> tuple[bool, str]:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            return False, f"proposal {proposal_id} not found"
        if proposal.target == "memory":
            return True, self._render_memory_preview(proposal)
        if proposal.target == "skill":
            try:
                return True, self._render_skill_preview(proposal)
            except ValueError as e:
                return False, str(e)
        return False, f"target {proposal.target} cannot be previewed"

    def promote(self, proposal_id: str) -> tuple[bool, str]:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            return False, f"proposal {proposal_id} not found"
        if proposal.target != "skill":
            return False, f"proposal {proposal_id} is not a skill proposal"
        if proposal.status != "approved":
            return False, f"proposal {proposal_id} must be approved before promote"

        validation = self.validate(proposal)
        if not validation.ok:
            return False, "; ".join(validation.errors)

        if not self._candidate_eval_passed(proposal.id):
            return False, f"proposal {proposal_id} must pass eval before promote"
        if not self._candidate_execution_eval_passed(proposal.id):
            return False, (
                f"proposal {proposal_id} must pass execution eval before promote"
            )

        candidate_path = self.candidate_skill_path(proposal.id)
        if not candidate_path.exists():
            payload = self._decode_skill_change(proposal.change)
            self._write_candidate_skill(proposal, payload)
        try:
            parse_skill_file(candidate_path)
        except SkillParseError as e:
            return False, f"candidate skill is invalid: {e}"

        applied_path = self._write_project_skill_from_candidate(proposal)
        proposal.status = "applied"
        proposal.applied_at = time.time()
        self.store.update_proposal(proposal)
        self._update_candidate_manifest(proposal, status="enabled")
        return True, str(applied_path)

    def evaluate(self, proposal_id: str) -> tuple[bool, str]:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            return False, f"proposal {proposal_id} not found"
        if proposal.target != "skill":
            return False, f"proposal {proposal_id} is not a skill proposal"

        validation = self.validate(proposal)
        if not validation.ok:
            self._write_eval_result(proposal, "failed", [], validation.errors, [])
            return False, "; ".join(validation.errors)

        payload = self._decode_skill_change(proposal.change)
        candidate_path = self.candidate_skill_path(proposal.id)
        if not candidate_path.exists():
            self._write_candidate_skill(proposal, payload)

        checks: list[str] = []
        errors: list[str] = []
        case_results: list[dict] = []
        skill = None
        try:
            skill = parse_skill_file(candidate_path)
            checks.append("parse_skill_file")
        except SkillParseError as e:
            errors.append(f"candidate skill is invalid: {e}")

        if skill is not None:
            cases, case_errors = self._load_eval_cases(proposal)
            errors.extend(case_errors)
            if not cases and not case_errors:
                errors.append(f"no eval case found for skill '{payload['name']}'")
            for eval_case in cases:
                result = self._evaluate_eval_case(skill, eval_case)
                case_results.append(result)
                if result["status"] == "passed":
                    checks.append(f"eval_case:{result['id']}")
                else:
                    errors.extend(
                        f"{result['id']}: {error}" for error in result["errors"]
                    )

        if errors:
            self._write_eval_result(proposal, "failed", checks, errors, case_results)
            return False, "; ".join(errors)

        self._write_eval_result(proposal, "passed", checks, [], case_results)
        return True, f"skill candidate eval passed: {proposal.id}"

    def run_execution_eval(
        self,
        proposal_id: str,
        *,
        min_cases: int = MIN_EXECUTION_EVAL_CASES,
    ) -> tuple[bool, str]:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            return False, f"proposal {proposal_id} not found"
        if proposal.target != "skill":
            return False, f"proposal {proposal_id} is not a skill proposal"
        if not self._candidate_eval_passed(proposal.id):
            return False, f"proposal {proposal_id} must pass eval before execution eval"

        payload = self._decode_skill_change(proposal.change)
        candidate_path = self.candidate_skill_path(proposal.id)
        try:
            skill = parse_skill_file(candidate_path)
        except SkillParseError as e:
            return False, f"candidate skill is invalid: {e}"

        cases, case_errors = self._load_eval_cases(proposal)
        if case_errors:
            return False, "; ".join(case_errors)
        if len(cases) < min_cases:
            return (
                False,
                f"execution eval requires at least {min_cases} eval cases, "
                f"got {len(cases)}",
            )

        sandbox_root = self._reset_execution_eval_sandbox(proposal.id)
        rounds: list[dict] = []
        for index, eval_case in enumerate(cases, 1):
            base_result = self._evaluate_eval_case(skill, eval_case)
            case_slug = self._artifact_slug(str(eval_case["id"]))
            round_dir = sandbox_root / f"round_{index:02d}_{case_slug}"
            round_record = {
                "round": index,
                "case_id": eval_case["id"],
                "task": eval_case["task"],
                "status": base_result["status"],
                "errors": base_result["errors"],
                "must_contain": eval_case["must_contain"],
                "must_not_contain": eval_case.get("must_not_contain", []),
                "sandbox_dir": str(round_dir),
                "artifacts": {
                    "task": str(round_dir / "task.md"),
                    "skill": str(round_dir / "SKILL.md"),
                    "rendered_prompt": str(round_dir / "rendered_prompt.md"),
                    "result": str(round_dir / "result.json"),
                    "child_agent": str(round_dir / "child_agent"),
                },
                "execution_summary": (
                    "Forked deterministic child agent loaded the candidate skill "
                    "inside the round sandbox and covered required behavior."
                    if base_result["status"] == "passed"
                    else "Forked deterministic child agent failed this task case."
                ),
            }
            self._write_execution_round_artifacts(
                round_dir,
                candidate_path,
                skill,
                eval_case,
                round_record,
            )
            rounds.append(round_record)

        passed = all(round_["status"] == "passed" for round_ in rounds)
        round_runners = {
            str(round_.get("runner", "deterministic_replay"))
            for round_ in rounds
        }
        if round_runners == {"agent_loop_scripted"}:
            report_runner = "fork_agent_sandbox_scripted_agent_loop"
        elif round_runners == {"deterministic_replay"}:
            report_runner = "fork_agent_sandbox_deterministic"
        else:
            report_runner = "fork_agent_sandbox_mixed"
        report = {
            "proposal_id": proposal.id,
            "skill_name": payload["name"],
            "status": "passed" if passed else "failed",
            "runner": report_runner,
            "min_cases_required": min_cases,
            "candidate_skill": str(candidate_path),
            "sandbox_root": str(sandbox_root),
            "generated_at": time.time(),
            "rounds": rounds,
            "summary": {
                "total": len(rounds),
                "passed": sum(1 for round_ in rounds if round_["status"] == "passed"),
                "failed": sum(1 for round_ in rounds if round_["status"] == "failed"),
            },
        }
        self._write_execution_eval_report(proposal, report)
        if not passed:
            return False, f"skill execution eval failed: {proposal.id}"
        return True, f"skill execution eval passed: {proposal.id}"

    def read_execution_eval_report(self, proposal_id: str) -> tuple[bool, str]:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            return False, f"proposal {proposal_id} not found"
        manifest = self._load_candidate_manifest(proposal_id)
        if manifest.get("execution_eval_status") != "passed":
            return False, f"execution eval not passed for {proposal_id}"
        report_path = manifest.get("execution_eval_markdown")
        path = Path(report_path) if report_path else self.execution_eval_markdown_path(proposal_id)
        if not path.exists():
            return False, f"execution eval report not found for {proposal_id}"
        return True, path.read_text(encoding="utf-8")

    def _validate_memory_proposal(
        self,
        proposal: EvolutionProposal,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        if not proposal.change.strip():
            errors.append("proposal change is empty")
        if len(proposal.change) > 500:
            warnings.append("memory change is long; consider splitting it")

    def _validate_skill_proposal(
        self,
        proposal: EvolutionProposal,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        try:
            payload = self._decode_skill_change(proposal.change)
        except ValueError as e:
            errors.append(str(e))
            return

        name = payload.get("name")
        description = payload.get("description")
        body = payload.get("body")
        mode = payload.get("mode", "inline")
        context = payload.get("context", "recent")
        allowed_tools = payload.get("allowedTools", [])
        action = payload.get("action", "create")

        if action not in {"create", "patch"}:
            errors.append("skill action must be create or patch")
        if not isinstance(name, str) or not VALID_NAME_RE.match(name):
            errors.append(
                "skill name must be lowercase letters, digits, and hyphens, "
                "starting with a letter"
            )
        if not isinstance(description, str) or not description.strip():
            errors.append("skill description cannot be empty")
        if not isinstance(body, str) or not body.strip():
            errors.append("skill body cannot be empty")
        if isinstance(body, str):
            self._validate_skill_static_policy(body, errors, warnings)
        if mode not in VALID_MODES:
            errors.append(f"skill mode must be one of {sorted(VALID_MODES)}")
        if context not in VALID_CONTEXTS:
            errors.append(f"skill context must be one of {sorted(VALID_CONTEXTS)}")
        if not isinstance(allowed_tools, list) or not all(
            isinstance(tool, str) and tool.strip() for tool in allowed_tools
        ):
            errors.append("skill allowedTools must be a list of non-empty strings")

        if isinstance(name, str) and VALID_NAME_RE.match(name):
            target_dir = self.project_skills_path / name
            flat_skill = self.project_skills_path / f"{name}.md"
            existing_skill = self._existing_project_skill_path(name)
            if action == "create" and (target_dir.exists() or flat_skill.exists()):
                errors.append(f"skill '{name}' already exists")
            if action == "patch" and existing_skill is None:
                errors.append(f"skill '{name}' does not exist as a project skill")

    def _validate_skill_static_policy(
        self,
        body: str,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        lower = body.lower()
        for pattern in DANGEROUS_SKILL_PATTERNS:
            if pattern.lower() in lower:
                errors.append(f"skill body contains dangerous command pattern: {pattern}")
        for word in ("永远", "所有任务", "必须", "禁止"):
            if word in body:
                warnings.append(
                    f"skill body contains broad rule wording '{word}'; review scope"
                )

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

    def _render_memory_preview(self, proposal: EvolutionProposal) -> str:
        bullet = proposal.change.strip()
        if not bullet.startswith("- "):
            bullet = "- " + bullet
        existing = self.project_memory_path.read_text(
            encoding="utf-8"
        ) if self.project_memory_path.exists() else ""
        status = "already present" if bullet in existing.splitlines() else "will append"
        return "\n".join([
            "# Evolution Preview",
            "",
            f"Proposal: {proposal.id}",
            "Target: memory",
            f"File: {self.project_memory_path}",
            f"Status: {status}",
            "",
            "## Change",
            "",
            bullet,
            "",
        ])

    def _render_skill_preview(self, proposal: EvolutionProposal) -> str:
        payload = self._decode_skill_change(proposal.change)
        candidate_path = self.candidate_skill_path(proposal.id)
        target_path = self._skill_target_path(payload)
        candidate_text = (
            candidate_path.read_text(encoding="utf-8")
            if candidate_path.exists()
            else self._render_skill_markdown(payload)
        )
        existing_text = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
        action = payload.get("action", "create")
        if target_path.exists():
            diff_lines = list(unified_diff(
                existing_text.splitlines(),
                candidate_text.splitlines(),
                fromfile="formal",
                tofile="candidate",
                lineterm="",
            ))
        else:
            diff_lines = list(unified_diff(
                [],
                candidate_text.splitlines(),
                fromfile="formal",
                tofile="candidate",
                lineterm="",
            ))
        body = "\n".join(diff_lines) if diff_lines else "(no content changes)"
        return "\n".join([
            "# Skill Preview",
            "",
            f"Proposal: {proposal.id}",
            f"Action: {action}",
            f"Skill: {payload.get('name')}",
            f"Candidate: {candidate_path}",
            f"Formal target: {target_path}",
            "",
            "## Diff",
            "",
            body,
            "",
        ])

    def _write_project_skill(self, proposal: EvolutionProposal) -> Path:
        payload = self._decode_skill_change(proposal.change)
        target_path = self._skill_target_path(payload)
        if payload.get("action", "create") == "create":
            target_path.parent.mkdir(parents=True, exist_ok=False)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(self._render_skill_markdown(payload), encoding="utf-8")
        return target_path

    def _write_project_skill_from_candidate(self, proposal: EvolutionProposal) -> Path:
        payload = self._decode_skill_change(proposal.change)
        candidate_text = self.candidate_skill_path(proposal.id).read_text(encoding="utf-8")
        target_path = self._skill_target_path(payload)
        if payload.get("action", "create") == "create":
            target_path.parent.mkdir(parents=True, exist_ok=False)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(candidate_text, encoding="utf-8")
        return target_path

    def _write_candidate_skill(
        self,
        proposal: EvolutionProposal,
        payload: dict,
    ) -> Path:
        candidate_dir = self.candidate_dir(proposal.id)
        candidate_dir.mkdir(parents=True, exist_ok=True)
        skill_path = self.candidate_skill_path(proposal.id)
        skill_path.write_text(self._render_skill_markdown(payload), encoding="utf-8")
        self._write_candidate_manifest(proposal, payload, status="candidate")
        return skill_path

    def _write_candidate_manifest(
        self,
        proposal: EvolutionProposal,
        payload: dict,
        *,
        status: str,
    ) -> None:
        existing = self._load_candidate_manifest(proposal.id)
        manifest = {
            "proposal_id": proposal.id,
            "skill_name": payload.get("name"),
            "action": payload.get("action", "create"),
            "status": status,
            "evidence_ids": proposal.evidence_ids,
            "formal_target": str(self._skill_target_path(payload)),
            "candidate_skill": str(self.candidate_skill_path(proposal.id)),
            "created_at": proposal.created_at,
            "promoted_at": proposal.applied_at if status == "enabled" else 0.0,
            "eval_status": existing.get("eval_status", "pending"),
            "eval_checks": existing.get("eval_checks", []),
            "eval_errors": existing.get("eval_errors", []),
            "eval_case_results": existing.get("eval_case_results", []),
            "evaluated_at": existing.get("evaluated_at", 0.0),
            "execution_eval_status": existing.get("execution_eval_status", "pending"),
            "execution_eval_report": existing.get("execution_eval_report", ""),
            "execution_eval_markdown": existing.get("execution_eval_markdown", ""),
            "execution_eval_rounds": existing.get("execution_eval_rounds", []),
            "execution_evaluated_at": existing.get("execution_evaluated_at", 0.0),
        }
        self.candidate_manifest_path(proposal.id).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _update_candidate_manifest(
        self,
        proposal: EvolutionProposal,
        *,
        status: str,
    ) -> None:
        payload = self._decode_skill_change(proposal.change)
        self._write_candidate_manifest(proposal, payload, status=status)

    def _load_candidate_manifest(self, proposal_id: str) -> dict:
        path = self.candidate_manifest_path(proposal_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _candidate_eval_passed(self, proposal_id: str) -> bool:
        return self._load_candidate_manifest(proposal_id).get("eval_status") == "passed"

    def _candidate_execution_eval_passed(self, proposal_id: str) -> bool:
        return (
            self._load_candidate_manifest(proposal_id)
            .get("execution_eval_status") == "passed"
        )

    def _invalidate_candidate_eval(self, proposal: EvolutionProposal) -> None:
        manifest = self._load_candidate_manifest(proposal.id)
        if not manifest:
            return
        manifest["eval_status"] = "pending"
        manifest["eval_checks"] = []
        manifest["eval_errors"] = []
        manifest["eval_case_results"] = []
        manifest["evaluated_at"] = 0.0
        manifest["execution_eval_status"] = "pending"
        manifest["execution_eval_report"] = ""
        manifest["execution_eval_markdown"] = ""
        manifest["execution_eval_rounds"] = []
        manifest["execution_evaluated_at"] = 0.0
        self.candidate_manifest_path(proposal.id).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_eval_result(
        self,
        proposal: EvolutionProposal,
        status: str,
        checks: list[str],
        errors: list[str],
        case_results: list[dict],
    ) -> None:
        payload = self._decode_skill_change(proposal.change)
        self._write_candidate_manifest(proposal, payload, status="candidate")
        manifest = self._load_candidate_manifest(proposal.id)
        manifest["eval_status"] = status
        manifest["eval_checks"] = checks
        manifest["eval_errors"] = errors
        manifest["eval_case_results"] = case_results
        manifest["evaluated_at"] = time.time()
        self.candidate_manifest_path(proposal.id).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_execution_eval_report(
        self,
        proposal: EvolutionProposal,
        report: dict,
    ) -> None:
        report_path = self.execution_eval_report_path(proposal.id)
        markdown_path = self.execution_eval_markdown_path(proposal.id)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(
            self._render_execution_eval_markdown(report),
            encoding="utf-8",
        )

        payload = self._decode_skill_change(proposal.change)
        self._write_candidate_manifest(proposal, payload, status="candidate")
        manifest = self._load_candidate_manifest(proposal.id)
        manifest["execution_eval_status"] = report["status"]
        manifest["execution_eval_report"] = str(report_path)
        manifest["execution_eval_markdown"] = str(markdown_path)
        manifest["execution_eval_rounds"] = report["rounds"]
        manifest["execution_evaluated_at"] = report["generated_at"]
        self.candidate_manifest_path(proposal.id).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _reset_execution_eval_sandbox(self, proposal_id: str) -> Path:
        sandbox_root = self.execution_eval_sandbox_path(proposal_id)
        candidate_root = self.candidate_dir(proposal_id).resolve()
        resolved = sandbox_root.resolve()
        if resolved != candidate_root and not resolved.is_relative_to(candidate_root):
            raise ValueError("execution eval sandbox must be under candidate dir")
        if sandbox_root.exists():
            shutil.rmtree(sandbox_root)
        sandbox_root.mkdir(parents=True, exist_ok=True)
        return sandbox_root

    def _write_execution_round_artifacts(
        self,
        round_dir: Path,
        candidate_path: Path,
        skill,
        eval_case: dict,
        round_record: dict,
    ) -> None:
        sandbox_root = round_dir.parent.resolve()
        resolved = round_dir.resolve()
        if resolved != sandbox_root and not resolved.is_relative_to(sandbox_root):
            raise ValueError("execution eval round dir must be under sandbox root")
        round_dir.mkdir(parents=True, exist_ok=False)
        rendered = substitute_arguments(skill.prompt_body, eval_case["task"])
        task_lines = [
            f"# Eval Task {eval_case['id']}",
            "",
            eval_case["task"],
            "",
            "## Must contain",
            "",
            *(f"- {term}" for term in eval_case["must_contain"]),
            "",
            "## Must not contain",
            "",
            *(f"- {term}" for term in eval_case.get("must_not_contain", [])),
        ]
        (round_dir / "task.md").write_text(
            "\n".join(task_lines).rstrip() + "\n",
            encoding="utf-8",
        )
        (round_dir / "SKILL.md").write_text(
            candidate_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (round_dir / "rendered_prompt.md").write_text(
            rendered.rstrip() + "\n",
            encoding="utf-8",
        )
        fork_agent = self._write_child_agent_artifacts(
            round_dir,
            skill,
            eval_case,
            rendered,
            round_record,
        )
        result = {
            "case_id": round_record["case_id"],
            "status": round_record["status"],
            "errors": round_record["errors"],
            "execution_summary": round_record["execution_summary"],
            "checks": {
                "must_contain": round_record["must_contain"],
                "must_not_contain": round_record["must_not_contain"],
            },
            "fork_agent": fork_agent,
        }
        (round_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_child_agent_artifacts(
        self,
        round_dir: Path,
        skill,
        eval_case: dict,
        rendered: str,
        round_record: dict,
    ) -> dict:
        child_dir = round_dir / "child_agent"
        child_dir.mkdir(parents=True, exist_ok=False)
        input_path = child_dir / "input.json"
        tool_policy_path = child_dir / "tool_policy.json"
        transcript_path = child_dir / "transcript.md"
        final_answer_path = child_dir / "final_answer.md"
        input_payload = {
            "case_id": eval_case["id"],
            "task": eval_case["task"],
            "skill_name": skill.name,
            "skill_description": skill.description,
            "rendered_prompt": rendered,
            "must_contain": eval_case["must_contain"],
            "must_not_contain": eval_case.get("must_not_contain", []),
            "workspace_files": eval_case.get("workspace_files", {}),
            "scripted_tool_calls": eval_case.get("scripted_tool_calls", []),
            "scripted_agent_turns": eval_case.get("scripted_agent_turns", []),
            "expected_files": eval_case.get("expected_files", {}),
            "execution_runner": eval_case.get("execution_runner", "deterministic_replay"),
        }
        runner = str(eval_case.get("execution_runner", "deterministic_replay"))
        tool_policy = {
            "allowed_tools": skill.allowed_tools,
            "network": "disabled",
            "write_scope": "round_sandbox_only",
            "project_write": "disabled",
            "max_retries": 1,
            "runner": runner,
        }
        workspace_path = child_dir / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=False)
        workspace_errors = self._write_workspace_seed_files(
            workspace_path,
            eval_case.get("workspace_files", {}),
        )
        child_errors = list(workspace_errors)
        agent_loop = runner == "agent_loop_scripted"
        if agent_loop:
            agent_loop_result = self._run_agent_loop_scripted(
                workspace_path,
                skill,
                rendered,
                eval_case,
            )
            turns = agent_loop_result["turns"]
            tool_results = agent_loop_result["tool_results"]
            child_errors.extend(agent_loop_result["errors"])
        else:
            turns = self._run_scripted_agent_turns(
                workspace_path,
                skill.allowed_tools,
                eval_case.get("scripted_agent_turns", []),
            )
            if turns:
                tool_results = [
                    result
                    for turn in turns
                    for result in turn.get("tool_results", [])
                ]
            else:
                tool_results = self._run_scripted_tool_calls(
                    workspace_path,
                    skill.allowed_tools,
                    eval_case.get("scripted_tool_calls", []),
                )
        assertions = self._assert_expected_files(
            workspace_path,
            eval_case.get("expected_files", {}),
        )
        child_errors.extend(
            result["error"] for result in tool_results if result["status"] == "failed"
        )
        child_errors.extend(
            assertion["error"]
            for assertion in assertions
            if assertion["status"] == "failed"
        )
        if child_errors:
            round_record["errors"].extend(child_errors)
        round_record["status"] = "failed" if round_record["errors"] else "passed"
        round_record["runner"] = runner
        if round_record["status"] == "passed":
            round_record["execution_summary"] = (
                "Scripted LLM drove the real Agent loop in an isolated workspace "
                "and all workspace assertions passed."
                if agent_loop
                else (
                    "Scripted child agent executed in the isolated workspace and "
                    "all workspace assertions passed."
                )
            )
        else:
            round_record["execution_summary"] = (
                "Scripted LLM Agent loop failed one or more workspace assertions."
                if agent_loop
                else "Scripted child agent failed one or more workspace assertions."
            )
        transcript = [
            "# Forked Child Agent Transcript",
            "",
            f"- Case: `{eval_case['id']}`",
            f"- Skill: `{skill.name}`",
            f"- Runner: {runner}",
            "- Network: disabled",
            "- Project writes: disabled",
            "",
            "## User Task",
            "",
            eval_case["task"],
            "",
            "## Loaded Skill Prompt",
            "",
            rendered.rstrip(),
            "",
            "## Verification",
            "",
            f"- Status: `{round_record['status']}`",
            f"- Required terms: {', '.join(round_record['must_contain'])}",
            f"- Forbidden terms: {', '.join(round_record['must_not_contain']) or '(none)'}",
            "",
            "## Scripted Tool Calls",
            "",
            *(f"- {result['tool']} {result['path']}: {result['status']}" for result in tool_results),
            "",
            "## Agent Turns",
            "",
            *self._render_agent_turn_transcript(turns),
            "",
            "## Workspace Assertions",
            "",
            *(f"- {assertion['path']}: {assertion['status']}" for assertion in assertions),
        ]
        if round_record["errors"]:
            transcript.append("- Errors: " + "; ".join(round_record["errors"]))
        final_answer = [
            "# Child Agent Final Answer",
            "",
            round_record["execution_summary"],
            "",
            f"Status: {round_record['status']}",
        ]
        input_path.write_text(
            json.dumps(input_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tool_policy_path.write_text(
            json.dumps(tool_policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        transcript_path.write_text("\n".join(transcript).rstrip() + "\n", encoding="utf-8")
        final_answer_path.write_text(
            "\n".join(final_answer).rstrip() + "\n",
            encoding="utf-8",
        )
        return {
            "input": str(input_path),
            "tool_policy": str(tool_policy_path),
            "transcript": str(transcript_path),
            "final_answer": str(final_answer_path),
            "workspace": str(workspace_path),
            "runner": runner,
            "agent_loop": agent_loop,
            "tool_results": tool_results,
            "turns": turns,
            "assertions": assertions,
        }

    def _write_workspace_seed_files(
        self,
        workspace_path: Path,
        files: dict,
    ) -> list[str]:
        errors: list[str] = []
        if not isinstance(files, dict):
            return ["workspace_files must be an object"]
        for relative_path, content in files.items():
            try:
                target = self._workspace_child_path(workspace_path, str(relative_path))
            except ValueError as exc:
                errors.append(str(exc))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        return errors

    def _run_scripted_tool_calls(
        self,
        workspace_path: Path,
        allowed_tools: list[str],
        tool_calls: list,
    ) -> list[dict]:
        if not isinstance(tool_calls, list):
            return [{
                "tool": "(invalid)",
                "path": "",
                "status": "failed",
                "error": "scripted_tool_calls must be a list",
            }]
        results: list[dict] = []
        allowed = set(allowed_tools)
        for index, call in enumerate(tool_calls, 1):
            if not isinstance(call, dict):
                results.append({
                    "tool": "(invalid)",
                    "path": "",
                    "status": "failed",
                    "error": f"scripted tool call {index} must be an object",
                })
                continue
            tool = str(call.get("tool") or call.get("name") or "").strip()
            path_text = str(call.get("path") or call.get("file_path") or "").strip()
            result = {"tool": tool, "path": path_text, "status": "passed", "error": ""}
            if tool not in {"ReadFile", "WriteFile"}:
                result.update(status="failed", error=f"unsupported scripted tool: {tool}")
                results.append(result)
                continue
            if tool not in allowed:
                result.update(status="failed", error=f"tool not allowed by skill: {tool}")
                results.append(result)
                continue
            try:
                target = self._workspace_child_path(workspace_path, path_text)
            except ValueError as exc:
                result.update(status="failed", error=str(exc))
                results.append(result)
                continue
            if tool == "ReadFile":
                if not target.is_file():
                    result.update(status="failed", error=f"file not found: {path_text}")
                else:
                    result["output"] = target.read_text(encoding="utf-8")
            elif tool == "WriteFile":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(call.get("content", "")), encoding="utf-8")
                result["output"] = f"wrote {path_text}"
            results.append(result)
        return results

    def _run_agent_loop_scripted(
        self,
        workspace_path: Path,
        skill,
        rendered: str,
        eval_case: dict,
    ) -> dict:
        turns = eval_case.get("scripted_agent_turns", [])
        if not isinstance(turns, list):
            return {
                "turns": [{
                    "turn": 1,
                    "assistant": "",
                    "events": [],
                    "tool_results": [{
                        "tool": "(invalid)",
                        "path": "",
                        "status": "failed",
                        "error": "scripted_agent_turns must be a list",
                    }],
                }],
                "tool_results": [{
                    "tool": "(invalid)",
                    "path": "",
                    "status": "failed",
                    "error": "scripted_agent_turns must be a list",
                }],
                "errors": ["scripted_agent_turns must be a list"],
            }

        async def collect() -> dict:
            from mewcode.agent import (
                Agent,
                ErrorEvent,
                PermissionRequest,
                PermissionResponse,
                ToolResultEvent,
                ToolUseEvent,
            )
            from mewcode.permissions import (
                DangerousCommandDetector,
                PathSandbox,
                PermissionChecker,
                PermissionMode,
                RuleEngine,
            )
            from mewcode.tools import create_default_registry

            client = _ScriptedAgentLoopClient(workspace_path, turns)
            registry = create_default_registry()
            safe_allowed_tools = set(skill.allowed_tools) & {"ReadFile", "WriteFile"}
            for tool in registry.list_tools():
                if tool.name not in safe_allowed_tools:
                    registry.disable(tool.name)

            checker = PermissionChecker(
                DangerousCommandDetector(),
                PathSandbox(str(workspace_path)),
                RuleEngine(),
                mode=PermissionMode.ACCEPT_EDITS,
            )
            agent = Agent(
                client=client,
                registry=registry,
                protocol="anthropic",
                work_dir=str(workspace_path),
                max_iterations=max(len(turns) + 2, 2),
                permission_checker=checker,
                context_window=1_000_000,
                instructions_content=rendered,
            )
            conversation = ConversationManager()
            conversation.add_user_message(str(eval_case["task"]))
            errors: list[str] = []

            async for event in agent.run(conversation):
                if isinstance(event, ToolUseEvent):
                    record = client.tool_to_turn.get(event.tool_id)
                    if record is not None:
                        record["events"].append({
                            "type": "ToolUseEvent",
                            "tool": event.tool_name,
                            "path": client.tool_paths.get(event.tool_id, ""),
                            "arguments": event.arguments,
                        })
                elif isinstance(event, ToolResultEvent):
                    record = client.tool_to_turn.get(event.tool_id)
                    tool_result = {
                        "tool": event.tool_name,
                        "path": client.tool_paths.get(event.tool_id, ""),
                        "status": "failed" if event.is_error else "passed",
                        "error": event.output if event.is_error else "",
                        "output": event.output,
                    }
                    if record is not None:
                        record["tool_results"].append(tool_result)
                        record["events"].append({
                            "type": "ToolResultEvent",
                            "tool": event.tool_name,
                            "path": tool_result["path"],
                            "status": tool_result["status"],
                            "is_error": event.is_error,
                        })
                elif isinstance(event, PermissionRequest):
                    event.future.set_result(PermissionResponse.DENY)
                    errors.append(f"permission request denied for {event.tool_name}")
                elif isinstance(event, ErrorEvent):
                    errors.append(event.message)

            tool_results = [
                result
                for turn_record in client.turn_records
                for result in turn_record.get("tool_results", [])
            ]
            errors.extend(client.errors)
            return {
                "turns": client.turn_records,
                "tool_results": tool_results,
                "errors": errors,
            }

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(collect())
            except RuntimeError as exc:
                error = f"agent loop execution failed: {exc}"
                return {
                    "turns": [],
                    "tool_results": [],
                    "errors": [error],
                }

        result: dict[str, Any] = {}
        failure: list[BaseException] = []

        def run_in_thread() -> None:
            try:
                result.update(asyncio.run(collect()))
            except BaseException as exc:
                failure.append(exc)

        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
        thread.join()
        if not failure:
            return result
        exc = failure[0]
        if isinstance(exc, RuntimeError):
            error = f"agent loop execution failed: {exc}"
            return {
                "turns": [],
                "tool_results": [],
                "errors": [error],
            }
        raise exc

    def _run_scripted_agent_turns(
        self,
        workspace_path: Path,
        allowed_tools: list[str],
        turns: list,
    ) -> list[dict]:
        if not turns:
            return []
        if not isinstance(turns, list):
            return [{
                "turn": 1,
                "assistant": "",
                "tool_results": [{
                    "tool": "(invalid)",
                    "path": "",
                    "status": "failed",
                    "error": "scripted_agent_turns must be a list",
                }],
            }]
        records: list[dict] = []
        for index, turn in enumerate(turns, 1):
            if not isinstance(turn, dict):
                records.append({
                    "turn": index,
                    "assistant": "",
                    "tool_results": [{
                        "tool": "(invalid)",
                        "path": "",
                        "status": "failed",
                        "error": f"scripted agent turn {index} must be an object",
                    }],
                })
                continue
            tool_results = self._run_scripted_tool_calls(
                workspace_path,
                allowed_tools,
                turn.get("tool_calls", []),
            )
            records.append({
                "turn": index,
                "assistant": str(turn.get("assistant", "")),
                "tool_results": tool_results,
            })
        return records

    @staticmethod
    def _render_agent_turn_transcript(turns: list[dict]) -> list[str]:
        lines: list[str] = []
        for turn in turns:
            lines.append(f"### Turn {turn.get('turn', '')}")
            lines.append("")
            lines.append(f"Assistant: {turn.get('assistant', '')}")
            for event in turn.get("events", []):
                event_type = event.get("type", "")
                if event_type == "ToolUseEvent":
                    lines.append(
                        "ToolUseEvent: "
                        f"{event.get('tool', '')} {event.get('path', '')}"
                    )
                elif event_type == "ToolResultEvent":
                    lines.append(
                        "ToolResultEvent: "
                        f"{event.get('tool', '')} {event.get('path', '')} "
                        f"{event.get('status', '')}"
                    )
            for result in turn.get("tool_results", []):
                lines.append(
                    "ToolResult: "
                    f"{result.get('tool', '')} {result.get('path', '')} "
                    f"{result.get('status', '')}"
                )
                if result.get("error"):
                    lines.append(f"Error: {result['error']}")
            lines.append("")
        return lines

    def _assert_expected_files(
        self,
        workspace_path: Path,
        expected_files: dict,
    ) -> list[dict]:
        if not isinstance(expected_files, dict):
            return [{"path": "", "status": "failed", "error": "expected_files must be an object"}]
        assertions: list[dict] = []
        for relative_path, expected in expected_files.items():
            path_text = str(relative_path)
            assertion = {"path": path_text, "status": "passed", "error": ""}
            try:
                target = self._workspace_child_path(workspace_path, path_text)
            except ValueError as exc:
                assertion.update(status="failed", error=str(exc))
                assertions.append(assertion)
                continue
            if not target.is_file():
                assertion.update(status="failed", error=f"expected file missing: {path_text}")
            else:
                actual = target.read_text(encoding="utf-8")
                if actual != str(expected):
                    assertion.update(status="failed", error=f"expected file mismatch: {path_text}")
            assertions.append(assertion)
        return assertions

    @staticmethod
    def _workspace_child_path(workspace_path: Path, relative_path: str) -> Path:
        return _resolve_workspace_relative_path(workspace_path, relative_path)

    @staticmethod
    def _artifact_slug(value: str) -> str:
        slug = "".join(
            char if char.isalnum() or char in {"-", "_"} else "_"
            for char in value.strip()
        ).strip("_")
        return slug or "case"

    @staticmethod
    def _render_execution_eval_markdown(report: dict) -> str:
        lines = [
            "# Skill Execution Eval Report",
            "",
            f"- Proposal: `{report['proposal_id']}`",
            f"- Skill: `{report['skill_name']}`",
            f"- Status: `{report['status']}`",
            f"- Runner: `{report.get('runner', 'deterministic')}`",
            f"- Sandbox: `{report.get('sandbox_root', '(none)')}`",
            f"- Rounds: {report['summary']['passed']}/{report['summary']['total']} passed",
            "",
            "## Rounds",
        ]
        for round_ in report["rounds"]:
            lines.extend([
                "",
                f"### Round {round_['round']}: {round_['case_id']}",
                "",
                f"- Task: {round_['task']}",
                f"- Status: `{round_['status']}`",
                f"- Sandbox: `{round_.get('sandbox_dir', '(none)')}`",
                f"- Child Agent: `{round_.get('artifacts', {}).get('child_agent', '(none)')}`",
                f"- Must contain: {', '.join(round_['must_contain'])}",
                f"- Must not contain: {', '.join(round_['must_not_contain']) or '(none)'}",
                f"- Result: {round_['execution_summary']}",
            ])
            if round_["errors"]:
                lines.append("- Errors: " + "; ".join(round_["errors"]))
        return "\n".join(lines).rstrip() + "\n"

    def _load_eval_cases(self, proposal: EvolutionProposal) -> tuple[list[dict], list[str]]:
        payload = self._decode_skill_change(proposal.change)
        path = self.eval_cases_path(str(payload["name"]))
        if not path.exists():
            return [], []

        cases: list[dict] = []
        errors: list[str] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as e:
                errors.append(f"eval case line {line_no} is invalid JSON: {e}")
                continue
            error = self._validate_eval_case(data, line_no)
            if error is not None:
                errors.append(error)
                continue
            cases.append(data)
        return cases, errors

    @staticmethod
    def _validate_eval_case(data: dict, line_no: int) -> str | None:
        if not isinstance(data, dict):
            return f"eval case line {line_no} must be a JSON object"
        if not isinstance(data.get("id"), str) or not data["id"].strip():
            return f"eval case line {line_no} missing id"
        if not isinstance(data.get("task"), str) or not data["task"].strip():
            return f"eval case {data.get('id', line_no)} missing task"
        required = data.get("must_contain")
        if (
            not isinstance(required, list)
            or not required
            or not all(isinstance(term, str) and term.strip() for term in required)
        ):
            return f"eval case {data.get('id', line_no)} missing must_contain"
        forbidden = data.get("must_not_contain", [])
        if not isinstance(forbidden, list) or not all(
            isinstance(term, str) and term.strip() for term in forbidden
        ):
            return f"eval case {data.get('id', line_no)} has invalid must_not_contain"
        runner = data.get("execution_runner", "deterministic_replay")
        if runner not in SUPPORTED_EXECUTION_RUNNERS:
            return (
                f"eval case {data.get('id', line_no)} has invalid execution_runner"
            )
        return None

    @staticmethod
    def _evaluate_eval_case(skill, eval_case: dict) -> dict:
        rendered = substitute_arguments(skill.prompt_body, eval_case["task"])
        text = f"{skill.name}\n{skill.description}\n{rendered}".lower()
        errors: list[str] = []
        for term in eval_case["must_contain"]:
            if term.lower() not in text:
                errors.append(f"must contain '{term}'")
        for term in eval_case.get("must_not_contain", []):
            if term.lower() in text:
                errors.append(f"must not contain '{term}'")
        return {
            "id": eval_case["id"],
            "status": "failed" if errors else "passed",
            "errors": errors,
        }

    def _suggestion_terms_for_proposal(
        self,
        proposal: EvolutionProposal,
        payload: dict,
    ) -> list[dict]:
        terms: list[dict] = []
        for term in self._usage_feedback_terms_for_proposal(proposal):
            terms.append({
                "term": term,
                "quality": "high",
                "score": 3,
                "coverage": "usage_feedback",
                "rationale": (
                    "directly covers usage feedback recorded for this skill"
                ),
            })
        body = str(payload.get("body", ""))
        if "Usage Feedback Patch Notes" in body:
            terms.append({
                "term": "Usage Feedback Patch Notes",
                "quality": "medium",
                "score": 2,
                "coverage": "structural_patch_guard",
                "rationale": "checks that the patch keeps feedback traceability visible",
            })
        if "Required Patch Behavior" in body:
            terms.append({
                "term": "Required Patch Behavior",
                "quality": "medium",
                "score": 2,
                "coverage": "structural_patch_guard",
                "rationale": "checks that the patch keeps explicit promotion requirements",
            })
        if str(payload.get("description", "")).strip():
            terms.append({
                "term": str(payload["description"]).strip(),
                "quality": "low",
                "score": 1,
                "coverage": "skill_description",
                "rationale": "fallback coverage from the skill description",
            })

        deduped: list[dict] = []
        seen: set[str] = set()
        for term in terms:
            text = str(term["term"])
            if text in seen:
                continue
            seen.add(text)
            deduped.append(term)
        return deduped or [{
            "term": str(payload["name"]),
            "quality": "low",
            "score": 1,
            "coverage": "skill_name",
            "rationale": "fallback coverage from the skill name",
        }]

    def _usage_feedback_terms_for_proposal(
        self,
        proposal: EvolutionProposal,
    ) -> list[str]:
        terms: list[str] = []
        seen: set[str] = set()
        for evidence_id in proposal.evidence_ids:
            evidence = self.store.get_evidence(evidence_id)
            if evidence is None:
                continue
            summaries = evidence.metadata.get("summaries")
            if not isinstance(summaries, list):
                continue
            for summary in summaries:
                term = str(summary).strip()
                if not term or term in seen:
                    continue
                seen.add(term)
                terms.append(term)
        return terms

    @staticmethod
    def _render_add_eval_case_command(
        proposal_id: str,
        task: str,
        must_contain: list[str],
        must_not_contain: list[str],
    ) -> str:
        def clean(value: str) -> str:
            return value.replace("::", ":").replace("\n", " ").strip()

        command = (
            f"/evolve add-eval-case {proposal_id} :: "
            f"{clean(task)} :: "
            f"{','.join(clean(term) for term in must_contain)}"
        )
        if must_not_contain:
            command += " :: " + ",".join(clean(term) for term in must_not_contain)
        return command

    def _project_skill_path(self, name: str) -> Path:
        return self.project_skills_path / name / "SKILL.md"

    def _skill_target_path(self, payload: dict) -> Path:
        name = str(payload["name"])
        if payload.get("action", "create") == "patch":
            existing = self._existing_project_skill_path(name)
            if existing is not None:
                return existing
        return self._project_skill_path(name)

    def _existing_project_skill_path(self, name: str) -> Path | None:
        if not VALID_NAME_RE.match(name):
            return None
        directory_skill = self._project_skill_path(name)
        if directory_skill.is_file():
            return directory_skill
        flat_skill = self.project_skills_path / f"{name}.md"
        if flat_skill.is_file():
            return flat_skill
        return None

    def _load_existing_project_skill(self, name: str):
        path = self._existing_project_skill_path(name)
        if path is None:
            return None
        try:
            return parse_skill_file(path)
        except SkillParseError:
            return None

    @staticmethod
    def _decode_skill_change(change: str) -> dict:
        try:
            payload = json.loads(change)
        except json.JSONDecodeError as e:
            raise ValueError(f"skill proposal change must be JSON: {e}") from e
        if not isinstance(payload, dict):
            raise ValueError("skill proposal change must be a JSON object")
        if "name" not in payload:
            raise ValueError("skill proposal missing name")
        return payload

    @staticmethod
    def _render_skill_markdown(payload: dict) -> str:
        meta = {
            "name": payload["name"],
            "description": payload["description"],
            "allowedTools": payload.get("allowedTools", []),
            "mode": payload.get("mode", "inline"),
            "context": payload.get("context", "recent"),
        }
        frontmatter = yaml.safe_dump(
            meta,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        return f"---\n{frontmatter}\n---\n\n{payload['body'].strip()}\n"
