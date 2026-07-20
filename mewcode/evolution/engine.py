"""Hermes-style self-evolution engine.

This module implements a conservative self-evolution loop:

observe -> propose -> validate -> approve -> apply

Memory proposals and validated project skill proposals can be applied.
Runtime self-evolution intentionally excludes code, prompt, and tool targets.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

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

PROJECT_MEMORY_HEADER = "### 项目知识"
SUPPORTED_EVOLUTION_TARGETS = {"memory", "skill"}
DANGEROUS_SKILL_PATTERNS = (
    "rm -rf /",
    "sudo rm -rf",
    "chmod 777 /",
    "curl | sh",
    "curl -s | sh",
    "wget -qO-",
)


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

    def candidate_dir(self, proposal_id: str) -> Path:
        return self.candidate_skills_path / proposal_id

    def candidate_skill_path(self, proposal_id: str) -> Path:
        return self.candidate_dir(proposal_id) / "SKILL.md"

    def candidate_manifest_path(self, proposal_id: str) -> Path:
        return self.candidate_dir(proposal_id) / "manifest.json"

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

        eval_case = {
            "id": case_id or new_evolution_id("case"),
            "proposal_id": proposal.id,
            "skill_name": skill_name,
            "task": clean_task,
            "must_contain": required,
            "must_not_contain": forbidden,
            "created_at": time.time(),
        }
        path = self.eval_cases_path(skill_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(
            existing + json.dumps(eval_case, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return str(eval_case["id"])

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
