from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mewcode.commands.handlers.evolve import handle_evolve
from mewcode.commands.registry import CommandContext
from mewcode.evolution import EvolutionEngine
from mewcode.skills.parser import parse_skill_file


class MockUI:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def add_system_message(self, text: str) -> None:
        self.messages.append(text)

    def send_user_message(self, text: str) -> None:
        pass

    def set_plan_mode(self, enabled: bool) -> None:
        pass

    def get_token_count(self) -> tuple[int, int]:
        return 0, 0

    def refresh_status(self) -> None:
        pass


@dataclass
class DummyConversation:
    history: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []


def _ctx(tmp_path: Path, args: str, ui: MockUI | None = None) -> CommandContext:
    agent = SimpleNamespace(
        work_dir=str(tmp_path),
        file_history=None,
        checkpoint_manager=None,
    )
    return CommandContext(
        args=args,
        agent=agent,
        conversation=DummyConversation(),
        session=None,
        session_manager=None,
        memory_manager=None,
        ui=ui or MockUI(),
        config={},
    )


class TestEvolutionEngine:
    def test_records_evidence_and_proposal(self, tmp_path: Path) -> None:
        engine = EvolutionEngine(tmp_path)
        evidence = engine.record_evidence(
            "用户纠正：修改前必须先创建 checkpoint。",
            kind="user_feedback",
            source="test",
        )
        proposal = engine.propose(
            "remember-checkpoint-before-risk",
            "高风险自进化应用前必须创建 rewind checkpoint。",
            evidence_ids=[evidence.id],
        )

        assert engine.store.get_evidence(evidence.id) is not None
        loaded = engine.store.get_proposal(proposal.id)
        assert loaded is not None
        assert loaded.evidence_ids == [evidence.id]

    def test_approved_memory_proposal_applies_to_project_memory(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose(
            "store-lesson",
            "Hermes evolution proposals must be approved before apply.",
        )
        engine.approve(proposal.id)

        ok, path = engine.apply(proposal.id)

        assert ok
        assert Path(path).read_text(encoding="utf-8").count("Hermes evolution") == 1
        applied = engine.store.get_proposal(proposal.id)
        assert applied is not None
        assert applied.status == "applied"

    def test_rejects_code_tool_prompt_targets(self, tmp_path: Path) -> None:
        engine = EvolutionEngine(tmp_path)
        with pytest.raises(ValueError, match="unsupported evolution target"):
            engine.propose(
                "rewrite-tool",
                "Change Bash safety policy.",
                target="tool",
            )

    def test_skill_proposal_writes_candidate_before_promotion(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        evidence = engine.record_evidence(
            "复杂调试任务复盘：先复现失败，再写回归测试，最后实现修复。",
            kind="success",
            source="test",
        )
        proposal = engine.propose_skill(
            name="debug-regression-loop",
            description="复杂调试任务的回归测试优先流程",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小修复。\n",
            allowed_tools=["Bash", "ReadFile"],
            context="recent",
            evidence_ids=[evidence.id],
        )

        candidate_path = engine.candidate_skill_path(proposal.id)
        manifest_path = engine.candidate_manifest_path(proposal.id)

        assert candidate_path.exists()
        assert manifest_path.exists()
        assert not (
            tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        ).exists()
        skill = parse_skill_file(candidate_path)
        assert skill.name == "debug-regression-loop"
        assert skill.description == "复杂调试任务的回归测试优先流程"
        assert skill.allowed_tools == ["Bash", "ReadFile"]
        assert skill.context == "recent"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["proposal_id"] == proposal.id
        assert manifest["status"] == "candidate"
        assert manifest["evidence_ids"] == [evidence.id]

    def test_approved_skill_proposal_cannot_apply_directly(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="debug-regression-loop",
            description="复杂调试任务的回归测试优先流程",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小修复。\n",
        )
        engine.approve(proposal.id)

        ok, message = engine.apply(proposal.id)

        assert not ok
        assert "promote" in message
        assert not (
            tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        ).exists()

    def test_promote_approved_skill_candidate_to_project_skill(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="debug-regression-loop",
            description="复杂调试任务的回归测试优先流程",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小修复。\n",
            allowed_tools=["Bash", "ReadFile"],
            context="recent",
        )
        engine.approve(proposal.id)

        ok, path = engine.promote(proposal.id)

        assert ok
        skill_path = Path(path)
        assert skill_path == tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        assert parse_skill_file(skill_path).name == "debug-regression-loop"
        manifest = json.loads(
            engine.candidate_manifest_path(proposal.id).read_text(encoding="utf-8")
        )
        assert manifest["status"] == "enabled"
        applied = engine.store.get_proposal(proposal.id)
        assert applied is not None
        assert applied.status == "applied"

    def test_promote_requires_approval(self, tmp_path: Path) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="approval-required",
            description="审批后才能启用",
            body="# 任务\n\n先评审，再启用。\n",
        )

        ok, message = engine.promote(proposal.id)

        assert not ok
        assert "approved" in message
        assert not (
            tmp_path / ".mewcode" / "skills" / "approval-required" / "SKILL.md"
        ).exists()

    def test_skill_proposal_refuses_to_overwrite_existing_skill(
        self, tmp_path: Path
    ) -> None:
        existing = tmp_path / ".mewcode" / "skills" / "existing-skill" / "SKILL.md"
        existing.parent.mkdir(parents=True)
        existing.write_text(
            "---\n"
            "name: existing-skill\n"
            "description: Existing\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Existing\n",
            encoding="utf-8",
        )
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="existing-skill",
            description="Should not overwrite",
            body="# New\n",
        )

        validation = engine.validate(proposal)

        assert not validation.ok
        assert any("already exists" in error for error in validation.errors)

    def test_promote_skill_patch_updates_existing_project_skill(
        self, tmp_path: Path
    ) -> None:
        existing = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        existing.parent.mkdir(parents=True)
        existing.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Old review flow\n"
            "allowedTools:\n"
            "- Bash\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Old\n",
            encoding="utf-8",
        )
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill_patch(
            name="review-loop",
            description="Updated review flow",
            body="# Updated\n\n复盘后优先 patch 已有 skill，再考虑创建新 skill。\n",
            allowed_tools=["Bash", "ReadFile"],
            context="full",
        )

        validation = engine.validate(proposal)
        engine.approve(proposal.id)
        ok, path = engine.promote(proposal.id)

        assert validation.ok
        assert ok
        assert Path(path) == existing
        payload = json.loads(proposal.change)
        assert payload["action"] == "patch"
        skill = parse_skill_file(existing)
        assert skill.description == "Updated review flow"
        assert skill.allowed_tools == ["Bash", "ReadFile"]
        assert skill.context == "full"
        assert "优先 patch 已有 skill" in skill.prompt_body

    def test_skill_static_policy_blocks_dangerous_candidate(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="dangerous-skill",
            description="危险命令测试",
            body="# 任务\n\n执行 rm -rf / 清理系统。",
        )

        validation = engine.validate(proposal)

        assert not validation.ok
        assert any("dangerous command" in error for error in validation.errors)

    def test_skill_patch_refuses_missing_project_skill(self, tmp_path: Path) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill_patch(
            name="missing-skill",
            description="Missing skill patch",
            body="# Missing\n",
        )

        validation = engine.validate(proposal)

        assert not validation.ok
        assert any("does not exist" in error for error in validation.errors)


@pytest.mark.asyncio
class TestEvolveCommand:
    async def test_observe_and_propose_flow(self, tmp_path: Path) -> None:
        ui = MockUI()
        await handle_evolve(_ctx(tmp_path, "observe 测试失败说明需要补充回归测试。", ui))
        await handle_evolve(_ctx(
            tmp_path,
            "propose remember-tests :: 自进化经验必须转成可回归的测试。",
            ui,
        ))
        await handle_evolve(_ctx(tmp_path, "list", ui))

        assert any("Evolution evidence recorded" in msg for msg in ui.messages)
        assert any("Evolution proposal created" in msg for msg in ui.messages)
        assert any("remember-tests" in msg for msg in ui.messages)

    async def test_apply_requires_approval_then_updates_memory(
        self, tmp_path: Path
    ) -> None:
        ui = MockUI()
        await handle_evolve(_ctx(
            tmp_path,
            "propose remember-approval :: 自进化提案必须先 approve 再 apply。",
            ui,
        ))
        engine = EvolutionEngine(tmp_path)
        proposal = engine.store.load_proposals()[0]

        await handle_evolve(_ctx(tmp_path, f"apply {proposal.id}", ui))
        assert any("must be approved" in msg for msg in ui.messages)

        await handle_evolve(_ctx(tmp_path, f"approve {proposal.id}", ui))
        await handle_evolve(_ctx(tmp_path, f"apply {proposal.id}", ui))

        memory = (tmp_path / ".mewcode" / "memories.md").read_text(encoding="utf-8")
        assert "自进化提案必须先 approve 再 apply" in memory

    async def test_propose_skill_command_promotes_and_reloads_loader(
        self, tmp_path: Path
    ) -> None:
        ui = MockUI()
        loader = MagicMock()
        ctx = _ctx(
            tmp_path,
            "propose-skill review-to-skill :: 复杂问题复盘沉淀为 skill :: # 任务\n把可复用流程写成步骤。",
            ui,
        )
        ctx.config = {"skill_loader": loader}
        await handle_evolve(ctx)
        proposal = EvolutionEngine(tmp_path).store.load_proposals()[0]

        await handle_evolve(_ctx(tmp_path, f"approve {proposal.id}", ui))
        promote_ctx = _ctx(tmp_path, f"promote {proposal.id}", ui)
        promote_ctx.config = {"skill_loader": loader}
        await handle_evolve(promote_ctx)

        skill_path = tmp_path / ".mewcode" / "skills" / "review-to-skill" / "SKILL.md"
        assert skill_path.exists()
        assert parse_skill_file(skill_path).name == "review-to-skill"
        loader.reload.assert_called_once()

    async def test_apply_valid_skill_proposal_tells_user_to_promote(
        self, tmp_path: Path
    ) -> None:
        ui = MockUI()
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="needs-promote",
            description="Skill must be promoted",
            body="# 任务\n\n先评审再启用。",
        )
        engine.approve(proposal.id)

        await handle_evolve(_ctx(tmp_path, f"apply {proposal.id}", ui))

        assert any("promote" in msg for msg in ui.messages)
        assert not (
            tmp_path / ".mewcode" / "skills" / "needs-promote" / "SKILL.md"
        ).exists()

    async def test_apply_malformed_skill_proposal_reports_validation_error(
        self, tmp_path: Path
    ) -> None:
        ui = MockUI()
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose(
            "broken-skill",
            "not-json",
            target="skill",
        )
        engine.approve(proposal.id)

        await handle_evolve(_ctx(tmp_path, f"apply {proposal.id}", ui))

        assert any("Evolution apply failed" in msg for msg in ui.messages)
        assert any("skill proposal change must be JSON" in msg for msg in ui.messages)

    async def test_learn_command_patches_existing_skill_before_create(
        self, tmp_path: Path
    ) -> None:
        from mewcode.commands.handlers.learn import handle_learn

        existing = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        existing.parent.mkdir(parents=True)
        existing.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Old review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Old\n",
            encoding="utf-8",
        )
        ui = MockUI()

        await handle_learn(_ctx(
            tmp_path,
            "review-loop :: Updated review flow :: # Updated\n优先 patch 已有 skill。",
            ui,
        ))

        proposal = EvolutionEngine(tmp_path).store.load_proposals()[0]
        payload = json.loads(proposal.change)
        assert proposal.target == "skill"
        assert payload["action"] == "patch"
        assert any("patch" in msg for msg in ui.messages)

    async def test_learn_command_creates_new_skill_when_no_match(
        self, tmp_path: Path
    ) -> None:
        from mewcode.commands.handlers.learn import handle_learn

        ui = MockUI()
        await handle_learn(_ctx(
            tmp_path,
            "new-workflow :: 新工作流 :: # 任务\n把可复用步骤沉淀为 skill。",
            ui,
        ))

        proposal = EvolutionEngine(tmp_path).store.load_proposals()[0]
        payload = json.loads(proposal.change)
        assert payload["action"] == "create"
        assert payload["name"] == "new-workflow"
        assert any("create" in msg for msg in ui.messages)

    async def test_learn_command_records_evidence_for_proposal(
        self, tmp_path: Path
    ) -> None:
        from mewcode.commands.handlers.learn import handle_learn

        ui = MockUI()
        await handle_learn(_ctx(
            tmp_path,
            "evidence-workflow :: 证据优先学习 :: # 任务\n先记录 evidence，再创建 proposal。",
            ui,
        ))

        engine = EvolutionEngine(tmp_path)
        evidence = engine.store.load_evidence()
        proposal = engine.store.load_proposals()[0]
        assert len(evidence) == 1
        assert evidence[0].source == "learn-command"
        assert evidence[0].id in proposal.evidence_ids
