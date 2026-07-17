from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from mewcode.commands.handlers.evolve import handle_evolve
from mewcode.commands.registry import CommandContext
from mewcode.evolution import EvolutionEngine


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

    def test_non_memory_target_is_proposal_only(self, tmp_path: Path) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose(
            "rewrite-tool",
            "Change Bash safety policy.",
            target="tool",
        )

        validation = engine.validate(proposal)

        assert not validation.ok
        assert "proposal-only" in validation.errors[0]


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
