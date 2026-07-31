from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
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


def _install_fake_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp_module = ModuleType("mcp")
    mcp_module.ClientSession = object  # type: ignore[attr-defined]
    mcp_module.types = SimpleNamespace()  # type: ignore[attr-defined]
    mcp_client_module = ModuleType("mcp.client")
    mcp_stdio_module = ModuleType("mcp.client.stdio")
    mcp_stdio_module.StdioServerParameters = object  # type: ignore[attr-defined]
    mcp_stdio_module.stdio_client = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    mcp_http_module = ModuleType("mcp.client.streamable_http")
    mcp_http_module.streamable_http_client = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.client", mcp_client_module)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", mcp_stdio_module)
    monkeypatch.setitem(
        sys.modules,
        "mcp.client.streamable_http",
        mcp_http_module,
    )


def _add_debug_eval_case(engine: EvolutionEngine, proposal_id: str) -> str:
    return engine.add_eval_case(
        proposal_id,
        task="修复复杂回归 bug 时应该遵循什么流程？",
        must_contain=["复现失败", "回归测试"],
        must_not_contain=["跳过测试"],
    )


def _add_debug_eval_cases(engine: EvolutionEngine, proposal_id: str) -> list[str]:
    return [
        engine.add_eval_case(
            proposal_id,
            task="修复复杂回归 bug 时应该先做什么？",
            must_contain=["复现失败", "回归测试"],
            must_not_contain=["跳过测试"],
        ),
        engine.add_eval_case(
            proposal_id,
            task="修复用户反馈的线上缺陷时如何防止回归？",
            must_contain=["复现失败", "回归测试"],
            must_not_contain=["跳过测试"],
        ),
        engine.add_eval_case(
            proposal_id,
            task="复杂调试结束前如何确认修复可靠？",
            must_contain=["复现失败", "回归测试"],
            must_not_contain=["跳过测试"],
        ),
    ]


def _make_ready_skill_candidate(engine: EvolutionEngine):
    proposal = engine.propose_skill(
        name="debug-regression-loop",
        description="复杂调试任务的回归测试优先流程",
        body="# 任务\n\n先复现失败，再写回归测试，最后实现最小修复。\n",
        allowed_tools=["Bash", "ReadFile"],
        context="recent",
    )
    _add_debug_eval_cases(engine, proposal.id)
    ok, message = engine.evaluate(proposal.id)
    assert ok, message
    ok, message = engine.run_execution_eval(proposal.id)
    assert ok, message
    return proposal


def _usage_patch_proposal(tmp_path: Path, summaries: list[str] | None = None):
    skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: review-loop\n"
        "description: Review flow\n"
        "mode: inline\n"
        "context: recent\n"
        "---\n\n"
        "# Review\n\n原流程。\n",
        encoding="utf-8",
    )
    engine = EvolutionEngine(tmp_path)
    usage_summaries = summaries or [
        "错误地跳过复盘文档。",
        "用户纠正：遗漏验证。",
    ]
    for index, summary in enumerate(usage_summaries):
        engine.record_skill_usage(
            "review-loop",
            event="failure" if index == 0 else "user_feedback",
            source="test",
            metadata={"summary": summary},
        )
    return engine, engine.propose_skill_patch_from_usage("review-loop")


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

    def test_preview_memory_proposal_shows_append_without_writing(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose(
            "remember-preview",
            "预览必须展示将追加的 memory 内容。",
        )

        ok, preview = engine.preview(proposal.id)

        assert ok
        assert "Evolution Preview" in preview
        assert "Target: memory" in preview
        assert str(engine.project_memory_path) in preview
        assert "- 预览必须展示将追加的 memory 内容。" in preview
        assert not engine.project_memory_path.exists()

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
        assert manifest["eval_status"] == "pending"
        assert manifest["evidence_ids"] == [evidence.id]

    def test_evaluate_skill_candidate_requires_eval_case(self, tmp_path: Path) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="debug-regression-loop",
            description="复杂调试任务的回归测试优先流程",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小修复。\n",
        )

        ok, message = engine.evaluate(proposal.id)

        assert not ok
        assert "eval case" in message
        manifest = json.loads(
            engine.candidate_manifest_path(proposal.id).read_text(encoding="utf-8")
        )
        assert manifest["eval_status"] == "failed"
        assert any("eval case" in error for error in manifest["eval_errors"])

    def test_evaluate_skill_candidate_runs_eval_cases(self, tmp_path: Path) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="debug-regression-loop",
            description="复杂调试任务的回归测试优先流程",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小修复。\n",
        )
        case_id = _add_debug_eval_case(engine, proposal.id)

        ok, message = engine.evaluate(proposal.id)

        assert ok
        assert "passed" in message
        manifest = json.loads(
            engine.candidate_manifest_path(proposal.id).read_text(encoding="utf-8")
        )
        assert manifest["eval_status"] == "passed"
        assert "parse_skill_file" in manifest["eval_checks"]
        assert f"eval_case:{case_id}" in manifest["eval_checks"]
        assert manifest["eval_case_results"] == [{
            "id": case_id,
            "status": "passed",
            "errors": [],
        }]

    def test_evaluate_skill_candidate_fails_failed_eval_case(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="debug-regression-loop",
            description="复杂调试任务的回归测试优先流程",
            body="# 任务\n\n直接修改代码，然后提交。\n",
        )
        _add_debug_eval_case(engine, proposal.id)

        ok, message = engine.evaluate(proposal.id)

        assert not ok
        assert "must contain" in message
        manifest = json.loads(
            engine.candidate_manifest_path(proposal.id).read_text(encoding="utf-8")
        )
        assert manifest["eval_status"] == "failed"
        assert manifest["eval_case_results"][0]["status"] == "failed"
        assert any("must contain" in error for error in manifest["eval_errors"])

    def test_add_eval_case_rejects_invalid_skill_name(self, tmp_path: Path) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="../escape",
            description="非法 skill 名称不能写 eval case 路径",
            body="# 任务\n\n不要写出 evals 目录。\n",
        )

        with pytest.raises(ValueError, match="invalid skill name"):
            engine.add_eval_case(
                proposal.id,
                task="非法名称不能写入 eval case。",
                must_contain=["不要写出 evals 目录"],
            )

        assert not (tmp_path / ".mewcode" / "evolution" / "escape").exists()

    def test_run_execution_eval_requires_multiple_eval_cases(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="debug-regression-loop",
            description="复杂调试任务的回归测试优先流程",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小修复。\n",
        )
        _add_debug_eval_case(engine, proposal.id)
        engine.evaluate(proposal.id)

        ok, message = engine.run_execution_eval(proposal.id)

        assert not ok
        assert "at least 3" in message
        assert not engine.execution_eval_report_path(proposal.id).exists()

    def test_run_execution_eval_writes_user_visible_report(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="debug-regression-loop",
            description="复杂调试任务的回归测试优先流程",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小修复。\n",
        )
        case_ids = _add_debug_eval_cases(engine, proposal.id)
        engine.evaluate(proposal.id)

        ok, message = engine.run_execution_eval(proposal.id)

        assert ok
        assert "passed" in message
        report = json.loads(
            engine.execution_eval_report_path(proposal.id).read_text(encoding="utf-8")
        )
        assert report["status"] == "passed"
        assert report["proposal_id"] == proposal.id
        assert report["min_cases_required"] == 3
        assert [round_["case_id"] for round_ in report["rounds"]] == case_ids
        assert all(round_["status"] == "passed" for round_ in report["rounds"])
        markdown = engine.execution_eval_markdown_path(proposal.id).read_text(
            encoding="utf-8"
        )
        assert "Skill Execution Eval Report" in markdown
        assert "修复复杂回归 bug" in markdown
        manifest = json.loads(
            engine.candidate_manifest_path(proposal.id).read_text(encoding="utf-8")
        )
        assert manifest["execution_eval_status"] == "passed"
        assert manifest["execution_eval_report"].endswith("eval_report.json")

    def test_run_execution_eval_creates_sandbox_artifacts(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="debug-regression-loop",
            description="复杂调试任务的回归测试优先流程",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小修复。\n",
        )
        _add_debug_eval_cases(engine, proposal.id)
        engine.evaluate(proposal.id)

        ok, _ = engine.run_execution_eval(proposal.id)

        assert ok
        report = json.loads(
            engine.execution_eval_report_path(proposal.id).read_text(encoding="utf-8")
        )
        assert report["runner"] == "fork_agent_sandbox_deterministic"
        sandbox_root = Path(report["sandbox_root"])
        assert sandbox_root == engine.execution_eval_sandbox_path(proposal.id)
        assert sandbox_root.is_dir()
        for round_ in report["rounds"]:
            round_dir = Path(round_["sandbox_dir"])
            child_agent_dir = round_dir / "child_agent"
            assert round_dir.is_relative_to(sandbox_root)
            assert (round_dir / "task.md").is_file()
            assert (round_dir / "SKILL.md").is_file()
            assert (round_dir / "rendered_prompt.md").is_file()
            assert (round_dir / "result.json").is_file()
            assert child_agent_dir.is_dir()
            assert (child_agent_dir / "input.json").is_file()
            assert (child_agent_dir / "tool_policy.json").is_file()
            assert (child_agent_dir / "transcript.md").is_file()
            assert (child_agent_dir / "final_answer.md").is_file()
            tool_policy = json.loads(
                (child_agent_dir / "tool_policy.json").read_text(encoding="utf-8")
            )
            assert tool_policy["network"] == "disabled"
            assert tool_policy["write_scope"] == "round_sandbox_only"
            result = json.loads((round_dir / "result.json").read_text(encoding="utf-8"))
            assert result["case_id"] == round_["case_id"]
            assert result["status"] == "passed"
            assert result["fork_agent"]["transcript"].endswith("transcript.md")
        markdown = engine.execution_eval_markdown_path(proposal.id).read_text(
            encoding="utf-8"
        )
        assert "Runner: `fork_agent_sandbox_deterministic`" in markdown
        assert "Child Agent" in markdown
        assert "Sandbox" in markdown

    def test_run_execution_eval_executes_scripted_workspace_assertions(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="scripted-repair-loop",
            description="脚本化修复任务的隔离评测流程",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小补丁。\n",
            allowed_tools=["ReadFile", "WriteFile"],
        )
        case_id = engine.add_eval_case(
            proposal.id,
            task="在隔离工作区写出修复说明文件。",
            must_contain=["复现失败", "最小补丁"],
            workspace_files={"bug.txt": "broken\n"},
            scripted_tool_calls=[
                {"tool": "ReadFile", "path": "bug.txt"},
                {
                    "tool": "WriteFile",
                    "path": "result.txt",
                    "content": "fixed\n",
                },
            ],
            expected_files={"result.txt": "fixed\n"},
        )
        engine.evaluate(proposal.id)

        ok, message = engine.run_execution_eval(proposal.id, min_cases=1)

        assert ok, message
        report = json.loads(
            engine.execution_eval_report_path(proposal.id).read_text(encoding="utf-8")
        )
        round_ = report["rounds"][0]
        round_dir = Path(round_["sandbox_dir"])
        result = json.loads((round_dir / "result.json").read_text(encoding="utf-8"))
        assert round_["case_id"] == case_id
        assert round_["status"] == "passed"
        assert result["fork_agent"]["workspace"].endswith("workspace")
        assert result["fork_agent"]["assertions"][0]["status"] == "passed"
        assert (round_dir / "child_agent" / "workspace" / "result.txt").read_text(
            encoding="utf-8"
        ) == "fixed\n"

    def test_run_execution_eval_replays_scripted_agent_turns(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="turn-repair-loop",
            description="多轮子 Agent 回放评测流程",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小补丁。\n",
            allowed_tools=["ReadFile", "WriteFile"],
        )
        engine.add_eval_case(
            proposal.id,
            task="多轮读取输入并写出修复文件。",
            must_contain=["复现失败", "最小补丁"],
            workspace_files={"bug.txt": "broken\n"},
            scripted_agent_turns=[
                {
                    "assistant": "先读取失败输入。",
                    "tool_calls": [{"tool": "ReadFile", "path": "bug.txt"}],
                },
                {
                    "assistant": "写出修复结果。",
                    "tool_calls": [
                        {
                            "tool": "WriteFile",
                            "path": "result.txt",
                            "content": "fixed\n",
                        }
                    ],
                },
            ],
            expected_files={"result.txt": "fixed\n"},
        )
        engine.evaluate(proposal.id)

        ok, message = engine.run_execution_eval(proposal.id, min_cases=1)

        assert ok, message
        report = json.loads(
            engine.execution_eval_report_path(proposal.id).read_text(encoding="utf-8")
        )
        round_dir = Path(report["rounds"][0]["sandbox_dir"])
        result = json.loads((round_dir / "result.json").read_text(encoding="utf-8"))
        assert result["fork_agent"]["turns"][0]["assistant"] == "先读取失败输入。"
        assert result["fork_agent"]["turns"][0]["tool_results"][0]["tool"] == "ReadFile"
        assert result["fork_agent"]["turns"][1]["tool_results"][0]["tool"] == "WriteFile"
        transcript = (round_dir / "child_agent" / "transcript.md").read_text(
            encoding="utf-8"
        )
        assert "## Agent Turns" in transcript
        assert "ToolResult" in transcript

    def test_run_execution_eval_can_drive_agent_loop_with_scripted_llm(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="agent-loop-repair",
            description="通过真实 Agent loop 执行候选 skill 评测",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小补丁。\n",
            allowed_tools=["ReadFile", "WriteFile"],
        )
        engine.add_eval_case(
            proposal.id,
            task="用 Agent loop 读取输入并写出修复文件。",
            must_contain=["复现失败", "最小补丁"],
            workspace_files={"bug.txt": "broken\n"},
            scripted_agent_turns=[
                {
                    "assistant": "先读取失败输入。",
                    "tool_calls": [{"tool": "ReadFile", "path": "bug.txt"}],
                },
                {
                    "assistant": "写出修复结果。",
                    "tool_calls": [
                        {
                            "tool": "WriteFile",
                            "path": "result.txt",
                            "content": "fixed\n",
                        }
                    ],
                },
                {"assistant": "修复已完成。"},
            ],
            expected_files={"result.txt": "fixed\n"},
            execution_runner="agent_loop_scripted",
        )
        engine.evaluate(proposal.id)

        ok, message = engine.run_execution_eval(proposal.id, min_cases=1)

        assert ok, message
        report = json.loads(
            engine.execution_eval_report_path(proposal.id).read_text(encoding="utf-8")
        )
        assert report["runner"] == "fork_agent_sandbox_scripted_agent_loop"
        round_dir = Path(report["rounds"][0]["sandbox_dir"])
        result = json.loads((round_dir / "result.json").read_text(encoding="utf-8"))
        fork_agent = result["fork_agent"]
        assert fork_agent["runner"] == "agent_loop_scripted"
        assert fork_agent["agent_loop"] is True
        assert fork_agent["turns"][0]["events"][0]["type"] == "ToolUseEvent"
        assert fork_agent["turns"][0]["events"][1]["type"] == "ToolResultEvent"
        assert fork_agent["turns"][0]["tool_results"][0]["tool"] == "ReadFile"
        assert fork_agent["turns"][1]["tool_results"][0]["tool"] == "WriteFile"
        transcript = (round_dir / "child_agent" / "transcript.md").read_text(
            encoding="utf-8"
        )
        assert "- Runner: agent_loop_scripted" in transcript
        assert "ToolUseEvent: ReadFile" in transcript
        assert "ToolResultEvent: WriteFile" in transcript

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

        assert not ok
        assert "eval" in path
        assert not (
            tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        ).exists()

    def test_promote_approved_and_evaluated_skill_candidate_to_project_skill(
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
        _add_debug_eval_case(engine, proposal.id)
        engine.evaluate(proposal.id)

        ok, path = engine.promote(proposal.id)

        assert not ok
        assert "execution eval" in path
        assert not (
            tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        ).exists()

    def test_promote_approved_evaluated_and_execution_tested_skill(
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
        _add_debug_eval_cases(engine, proposal.id)
        engine.evaluate(proposal.id)
        engine.run_execution_eval(proposal.id)

        ok, path = engine.promote(proposal.id)

        assert ok
        skill_path = Path(path)
        assert skill_path == tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        assert parse_skill_file(skill_path).name == "debug-regression-loop"
        manifest = json.loads(
            engine.candidate_manifest_path(proposal.id).read_text(encoding="utf-8")
        )
        assert manifest["status"] == "enabled"
        assert manifest["eval_status"] == "passed"
        applied = engine.store.get_proposal(proposal.id)
        assert applied is not None
        assert applied.status == "applied"

    def test_submit_skill_approval_request_records_pending_request(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = _make_ready_skill_candidate(engine)

        request = engine.submit_skill_approval_request(
            proposal.id,
            approval_mode="deferred",
            source="self-evolution-review",
        )

        assert request.proposal_id == proposal.id
        assert request.skill_name == "debug-regression-loop"
        assert request.approval_mode == "deferred"
        assert request.status == "pending"
        assert "eval_report.md" in request.eval_report_markdown
        stored = engine.store.load_skill_approval_requests()
        assert [item.id for item in stored] == [request.id]
        manifest = json.loads(
            engine.candidate_manifest_path(proposal.id).read_text(encoding="utf-8")
        )
        assert manifest["approval_request_id"] == request.id
        assert manifest["approval_status"] == "pending"
        assert engine.store.get_proposal(proposal.id).status == "proposed"
        assert not (
            tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        ).exists()

    def test_submit_skill_approval_request_requires_execution_eval(
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
        _add_debug_eval_cases(engine, proposal.id)
        ok, message = engine.evaluate(proposal.id)
        assert ok, message

        with pytest.raises(ValueError, match="execution eval"):
            engine.submit_skill_approval_request(proposal.id)

        assert engine.store.load_skill_approval_requests() == []

    def test_render_skill_approval_request_shows_review_materials(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = _make_ready_skill_candidate(engine)
        request = engine.submit_skill_approval_request(proposal.id)

        ok, review = engine.render_skill_approval_request(request.id)

        assert ok, review
        assert "# Self-Evolution Skill Approval" in review
        assert f"Request: {request.id}" in review
        assert "Status: pending" in review
        assert "Skill: debug-regression-loop" in review
        assert "## Candidate Diff" in review
        assert "+++ candidate" in review
        assert "+name: debug-regression-loop" in review
        assert "+# 任务" in review
        assert "## Execution Eval Report" in review
        assert "Execution Eval Report" in review

    def test_list_skill_approval_inbox_defaults_to_pending_requests(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = _make_ready_skill_candidate(engine)
        request = engine.submit_skill_approval_request(proposal.id)

        pending = engine.list_skill_approval_inbox()

        assert [item.id for item in pending] == [request.id]

        ok, message = engine.resolve_skill_approval_request(
            request.id,
            approved=False,
            reviewer="user",
            reason="暂不启用。",
        )
        assert ok, message

        assert engine.list_skill_approval_inbox() == []
        all_requests = engine.list_skill_approval_inbox(status=None)
        assert [item.id for item in all_requests] == [request.id]
        assert all_requests[0].status == "rejected"

    def test_resolve_skill_approval_request_approved_promotes_candidate(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = _make_ready_skill_candidate(engine)
        request = engine.submit_skill_approval_request(proposal.id)

        ok, message = engine.resolve_skill_approval_request(
            request.id,
            approved=True,
            reviewer="user",
            reason="评测通过，允许启用。",
        )

        assert ok, message
        assert "promoted" in message
        stored = engine.store.get_skill_approval_request(request.id)
        assert stored is not None
        assert stored.status == "approved"
        assert stored.reviewer == "user"
        assert stored.resolution_reason == "评测通过，允许启用。"
        assert stored.resolved_at > 0
        skill_path = tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        assert skill_path.exists()
        assert parse_skill_file(skill_path).name == "debug-regression-loop"
        assert engine.store.get_proposal(proposal.id).status == "applied"
        manifest = json.loads(
            engine.candidate_manifest_path(proposal.id).read_text(encoding="utf-8")
        )
        assert manifest["status"] == "enabled"
        assert manifest["approval_status"] == "approved"

    def test_resolve_skill_approval_request_rejected_rejects_without_promote(
        self, tmp_path: Path
    ) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = _make_ready_skill_candidate(engine)
        request = engine.submit_skill_approval_request(proposal.id)

        ok, message = engine.resolve_skill_approval_request(
            request.id,
            approved=False,
            reviewer="user",
            reason="范围太宽，不启用。",
        )

        assert ok, message
        assert "rejected" in message
        stored = engine.store.get_skill_approval_request(request.id)
        assert stored is not None
        assert stored.status == "rejected"
        assert stored.resolution_reason == "范围太宽，不启用。"
        assert engine.store.get_proposal(proposal.id).status == "rejected"
        assert not (
            tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        ).exists()
        manifest = json.loads(
            engine.candidate_manifest_path(proposal.id).read_text(encoding="utf-8")
        )
        assert manifest["approval_status"] == "rejected"

    def test_self_evolution_review_disabled_skips_ready_candidates(
        self, tmp_path: Path
    ) -> None:
        from mewcode.config import SelfEvolutionConfig
        from mewcode.evolution.auto_review import review_ready_skill_candidates

        engine = EvolutionEngine(tmp_path)
        _make_ready_skill_candidate(engine)

        result = review_ready_skill_candidates(
            tmp_path,
            SelfEvolutionConfig(enabled=False, skill_approval_mode="manual"),
        )

        assert result["status"] == "disabled"
        assert result["requests"] == []
        assert engine.store.load_skill_approval_requests() == []

    def test_self_evolution_review_submits_ready_candidates_once(
        self, tmp_path: Path
    ) -> None:
        from mewcode.config import SelfEvolutionConfig
        from mewcode.evolution.auto_review import (
            format_review_notification,
            review_ready_skill_candidates,
        )

        engine = EvolutionEngine(tmp_path)
        proposal = _make_ready_skill_candidate(engine)

        result = review_ready_skill_candidates(
            tmp_path,
            SelfEvolutionConfig(enabled=True, skill_approval_mode="deferred"),
        )

        assert result["status"] == "submitted"
        assert len(result["requests"]) == 1
        assert result["requests"][0].proposal_id == proposal.id
        assert result["requests"][0].approval_mode == "deferred"
        message = format_review_notification(result)
        assert "Self-evolution approval request" in message
        assert proposal.id in message
        assert "deferred" in message

        second = review_ready_skill_candidates(
            tmp_path,
            SelfEvolutionConfig(enabled=True, skill_approval_mode="deferred"),
        )

        assert second["status"] == "idle"
        assert second["requests"] == []
        assert format_review_notification(second) == ""
        assert len(engine.store.load_skill_approval_requests()) == 1

    def test_self_evolution_review_creates_usage_patch_candidate(
        self, tmp_path: Path
    ) -> None:
        from mewcode.config import SelfEvolutionConfig
        from mewcode.evolution.auto_review import review_ready_skill_candidates

        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n\n原流程。\n",
            encoding="utf-8",
        )
        engine = EvolutionEngine(tmp_path)
        engine.record_skill_usage(
            "review-loop",
            event="failure",
            source="test",
            metadata={"summary": "错误地跳过复盘文档。"},
        )
        engine.record_skill_usage(
            "review-loop",
            event="user_feedback",
            source="test",
            metadata={"summary": "用户纠正：遗漏验证。"},
        )

        result = review_ready_skill_candidates(
            tmp_path,
            SelfEvolutionConfig(enabled=True, skill_approval_mode="manual"),
        )

        proposals = EvolutionEngine(tmp_path).store.load_proposals()
        assert result["generated_candidates"] == [proposals[0].id]
        assert result["requests"] == []
        assert len(proposals) == 1
        payload = json.loads(proposals[0].change)
        assert payload["action"] == "patch"
        assert payload["name"] == "review-loop"
        assert "错误地跳过复盘文档" in payload["body"]
        assert "用户纠正：遗漏验证" in payload["body"]

    def test_self_evolution_review_does_not_duplicate_usage_patch_candidate(
        self, tmp_path: Path
    ) -> None:
        from mewcode.config import SelfEvolutionConfig
        from mewcode.evolution.auto_review import review_ready_skill_candidates

        _usage_patch_proposal(tmp_path)

        result = review_ready_skill_candidates(
            tmp_path,
            SelfEvolutionConfig(enabled=True, skill_approval_mode="manual"),
        )

        proposals = EvolutionEngine(tmp_path).store.load_proposals()
        assert result["status"] == "idle"
        assert result["generated_candidates"] == []
        assert result["requests"] == []
        assert len(proposals) == 1

    def test_self_evolution_review_returns_eval_suggestions_for_generated_patch(
        self, tmp_path: Path
    ) -> None:
        from mewcode.config import SelfEvolutionConfig
        from mewcode.evolution.auto_review import review_ready_skill_candidates

        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n\n原流程。\n",
            encoding="utf-8",
        )
        engine = EvolutionEngine(tmp_path)
        engine.record_skill_usage(
            "review-loop",
            event="failure",
            source="test",
            metadata={"summary": "错误地跳过复盘文档。"},
        )
        engine.record_skill_usage(
            "review-loop",
            event="user_feedback",
            source="test",
            metadata={"summary": "用户纠正：遗漏验证。"},
        )

        result = review_ready_skill_candidates(
            tmp_path,
            SelfEvolutionConfig(enabled=True, skill_approval_mode="manual"),
        )

        proposals = EvolutionEngine(tmp_path).store.load_proposals()
        reviews = result["generated_candidate_reviews"]
        assert len(reviews) == 1
        assert reviews[0]["proposal_id"] == proposals[0].id
        assert reviews[0]["skill_name"] == "review-loop"
        assert reviews[0]["quality_counts"]["high"] == 2
        assert reviews[0]["coverage_counts"]["usage_feedback"] == 2
        assert reviews[0]["warnings"] == []
        assert len(reviews[0]["suggestions"]) == 3
        assert not EvolutionEngine(tmp_path).eval_cases_path("review-loop").exists()

    def test_tui_self_evolution_review_opens_approval_widget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_mcp(monkeypatch)
        from mewcode.app import MewCodeApp
        from mewcode.config import ProviderConfig, SelfEvolutionConfig

        engine = EvolutionEngine(tmp_path)
        _make_ready_skill_candidate(engine)
        app = MewCodeApp(
            providers=[
                ProviderConfig(
                    name="test",
                    protocol="openai",
                    base_url="https://example.invalid",
                    model="test-model",
                )
            ],
            self_evolution_config=SelfEvolutionConfig(
                enabled=True,
                skill_approval_mode="manual",
            ),
        )
        app.agent = SimpleNamespace(work_dir=str(tmp_path))
        opened: list[str] = []
        app._show_self_evolution_approval = opened.append  # type: ignore[method-assign]
        app._show_system_message = lambda _text: None  # type: ignore[method-assign]

        app._run_self_evolution_review()

        requests = engine.store.load_skill_approval_requests()
        assert len(requests) == 1
        assert opened == [requests[0].id]

    def test_tui_self_evolution_review_opens_existing_pending_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_mcp(monkeypatch)
        from mewcode.app import MewCodeApp
        from mewcode.config import ProviderConfig, SelfEvolutionConfig

        engine = EvolutionEngine(tmp_path)
        proposal = _make_ready_skill_candidate(engine)
        request = engine.submit_skill_approval_request(proposal.id)
        app = MewCodeApp(
            providers=[
                ProviderConfig(
                    name="test",
                    protocol="openai",
                    base_url="https://example.invalid",
                    model="test-model",
                )
            ],
            self_evolution_config=SelfEvolutionConfig(
                enabled=True,
                skill_approval_mode="manual",
            ),
        )
        app.agent = SimpleNamespace(work_dir=str(tmp_path))
        opened: list[str] = []
        app._show_self_evolution_approval = opened.append  # type: ignore[method-assign]
        app._show_system_message = lambda _text: None  # type: ignore[method-assign]

        app._run_self_evolution_review()

        assert opened == [request.id]

    def test_tui_skill_approval_response_approves_and_reloads_skills(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_mcp(monkeypatch)
        from mewcode.app import MewCodeApp
        from mewcode.config import ProviderConfig
        from mewcode.self_evolution_dialog import (
            InlineSkillApprovalWidget,
            SkillApprovalChoice,
        )

        engine = EvolutionEngine(tmp_path)
        proposal = _make_ready_skill_candidate(engine)
        request = engine.submit_skill_approval_request(proposal.id)
        app = MewCodeApp(
            providers=[
                ProviderConfig(
                    name="test",
                    protocol="openai",
                    base_url="https://example.invalid",
                    model="test-model",
                )
            ],
        )
        app.agent = SimpleNamespace(work_dir=str(tmp_path), set_skill_catalog=MagicMock())
        app.skill_loader = SimpleNamespace(
            reload=MagicMock(),
            get_catalog=lambda: [("debug-regression-loop", "Debug flow")],
        )
        messages: list[str] = []
        app._show_system_message = messages.append  # type: ignore[method-assign]

        app.on_inline_skill_approval_widget_responded(
            InlineSkillApprovalWidget.Responded(
                request.id,
                SkillApprovalChoice.APPROVE,
            )
        )

        stored = EvolutionEngine(tmp_path).store.get_skill_approval_request(request.id)
        assert stored is not None
        assert stored.status == "approved"
        assert (
            tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        ).exists()
        app.skill_loader.reload.assert_called_once()
        app.agent.set_skill_catalog.assert_called_once()
        assert any("approved" in message for message in messages)

    def test_add_eval_case_invalidates_existing_execution_eval(
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
        _add_debug_eval_cases(engine, proposal.id)
        engine.evaluate(proposal.id)
        engine.run_execution_eval(proposal.id)

        engine.add_eval_case(
            proposal.id,
            task="新增上线缺陷处理流程必须覆盖什么？",
            must_contain=["线上复盘记录"],
        )

        manifest = json.loads(
            engine.candidate_manifest_path(proposal.id).read_text(encoding="utf-8")
        )
        assert manifest["eval_status"] == "pending"
        assert manifest["execution_eval_status"] == "pending"

        report_ok, report_message = engine.read_execution_eval_report(proposal.id)
        assert not report_ok
        assert "not passed" in report_message

        ok, message = engine.promote(proposal.id)

        assert not ok
        assert "eval" in message
        assert not (
            tmp_path / ".mewcode" / "skills" / "debug-regression-loop" / "SKILL.md"
        ).exists()

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
        engine.add_eval_case(
            proposal.id,
            task="复盘复杂任务后如何更新已有 skill？",
            must_contain=["优先 patch 已有 skill"],
        )
        engine.add_eval_case(
            proposal.id,
            task="遇到重复 skill 时如何处理？",
            must_contain=["优先 patch 已有 skill"],
        )
        engine.add_eval_case(
            proposal.id,
            task="已有项目 skill 需要更新时如何避免重复创建？",
            must_contain=["优先 patch 已有 skill"],
        )
        engine.evaluate(proposal.id)
        engine.run_execution_eval(proposal.id)
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

    def test_preview_skill_patch_shows_candidate_diff(self, tmp_path: Path) -> None:
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
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill_patch(
            name="review-loop",
            description="Updated review flow",
            body="# Updated\n\n优先 patch 已有 skill。\n",
        )

        ok, preview = engine.preview(proposal.id)

        assert ok
        assert "Skill Preview" in preview
        assert "Action: patch" in preview
        assert str(existing) in preview
        assert "--- formal" in preview
        assert "+++ candidate" in preview
        assert "-# Old" in preview
        assert "+# Updated" in preview
        assert existing.read_text(encoding="utf-8").endswith("# Old\n")

    def test_preview_missing_skill_candidate_is_read_only(self, tmp_path: Path) -> None:
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="review-loop",
            description="Review flow",
            body="# Review\n\nUse this SOP for review tasks.\n",
        )
        candidate_dir = engine.candidate_dir(proposal.id)
        shutil.rmtree(candidate_dir)

        ok, preview = engine.preview(proposal.id)

        assert ok
        assert "Skill Preview" in preview
        assert str(engine.candidate_skill_path(proposal.id)) in preview
        assert "# Review" in preview
        assert not candidate_dir.exists()

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

    def test_record_skill_usage_writes_jsonl(self, tmp_path: Path) -> None:
        engine = EvolutionEngine(tmp_path)

        record = engine.record_skill_usage(
            "review-loop",
            event="load",
            source="LoadSkill",
            metadata={"source_label": "project"},
        )

        assert record["skill_name"] == "review-loop"
        assert record["event"] == "load"
        assert record["source"] == "LoadSkill"
        usage = engine.load_skill_usage()
        assert usage == [record]
        assert engine.skill_usage_path.exists()

    def test_suggest_quarantine_after_repeated_negative_usage(
        self, tmp_path: Path
    ) -> None:
        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n",
            encoding="utf-8",
        )
        engine = EvolutionEngine(tmp_path)
        engine.record_skill_usage(
            "review-loop",
            event="load",
            source="LoadSkill",
        )
        engine.record_skill_usage(
            "review-loop",
            event="failure",
            source="test",
            metadata={"summary": "错误地跳过复盘文档。"},
        )
        engine.record_skill_usage(
            "review-loop",
            event="user_feedback",
            source="test",
            metadata={"summary": "用户纠正：该 skill 仍然遗漏验证。"},
        )

        suggestions = engine.suggest_quarantine(failure_threshold=2)

        assert len(suggestions) == 1
        suggestion = suggestions[0]
        assert suggestion["skill_name"] == "review-loop"
        assert suggestion["negative_events"] == 2
        assert suggestion["events"] == ["failure", "user_feedback"]
        assert suggestion["command"].startswith("/evolve quarantine review-loop ::")

    def test_propose_skill_patch_from_usage_creates_candidate(
        self, tmp_path: Path
    ) -> None:
        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n\n原流程。\n",
            encoding="utf-8",
        )
        engine = EvolutionEngine(tmp_path)
        engine.record_skill_usage(
            "review-loop",
            event="failure",
            source="test",
            metadata={"summary": "错误地跳过复盘文档。"},
        )
        engine.record_skill_usage(
            "review-loop",
            event="user_feedback",
            source="test",
            metadata={"summary": "用户纠正：遗漏验证。"},
        )

        proposal = engine.propose_skill_patch_from_usage("review-loop")

        payload = json.loads(proposal.change)
        assert payload["action"] == "patch"
        assert payload["name"] == "review-loop"
        assert "原流程" in payload["body"]
        assert "错误地跳过复盘文档" in payload["body"]
        assert "用户纠正：遗漏验证" in payload["body"]
        assert engine.candidate_skill_path(proposal.id).exists()
        validation = engine.validate(proposal)
        assert validation.ok

    def test_suggest_eval_cases_for_usage_patch_is_read_only(
        self, tmp_path: Path
    ) -> None:
        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n\n原流程。\n",
            encoding="utf-8",
        )
        engine = EvolutionEngine(tmp_path)
        engine.record_skill_usage(
            "review-loop",
            event="failure",
            source="test",
            metadata={"summary": "错误地跳过复盘文档。"},
        )
        engine.record_skill_usage(
            "review-loop",
            event="user_feedback",
            source="test",
            metadata={"summary": "用户纠正：遗漏验证。"},
        )
        proposal = engine.propose_skill_patch_from_usage("review-loop")

        suggestions = engine.suggest_eval_cases(proposal.id)

        assert len(suggestions) == 3
        assert suggestions[0]["proposal_id"] == proposal.id
        assert suggestions[0]["skill_name"] == "review-loop"
        assert suggestions[0]["must_contain"]
        assert suggestions[0]["quality"] == "high"
        assert suggestions[0]["coverage"] == "usage_feedback"
        assert "directly covers usage feedback" in suggestions[0]["rationale"]
        assert suggestions[1]["quality"] == "high"
        assert suggestions[1]["coverage"] == "usage_feedback"
        assert suggestions[2]["quality"] == "medium"
        assert suggestions[2]["coverage"] == "structural_patch_guard"
        assert suggestions[0]["command"].startswith(
            f"/evolve add-eval-case {proposal.id} ::"
        )
        assert not engine.eval_cases_path("review-loop").exists()

        for suggestion in suggestions:
            engine.add_eval_case(
                proposal.id,
                task=suggestion["task"],
                must_contain=suggestion["must_contain"],
                must_not_contain=suggestion["must_not_contain"],
            )
        ok, message = engine.evaluate(proposal.id)
        assert ok, message

    def test_review_eval_case_suggestions_summarizes_quality(
        self, tmp_path: Path
    ) -> None:
        engine, proposal = _usage_patch_proposal(tmp_path)

        review = engine.review_eval_case_suggestions(proposal.id)

        assert review["proposal_id"] == proposal.id
        assert review["skill_name"] == "review-loop"
        assert review["quality_counts"] == {"high": 2, "medium": 1, "low": 0}
        assert review["coverage_counts"]["usage_feedback"] == 2
        assert review["coverage_counts"]["structural_patch_guard"] == 1
        assert review["warnings"] == []
        assert review["recommendation"].startswith("Add high-quality")
        assert len(review["suggestions"]) == 3

    def test_review_eval_case_suggestions_reports_uncovered_usage_feedback(
        self, tmp_path: Path
    ) -> None:
        engine, proposal = _usage_patch_proposal(
            tmp_path,
            [
                "错误地跳过复盘文档。",
                "用户纠正：遗漏验证。",
                "没有展示测试结果。",
                "未解释为什么该 skill 应该更新。",
            ],
        )

        review = engine.review_eval_case_suggestions(proposal.id)

        assert review["quality_counts"] == {"high": 3, "medium": 0, "low": 0}
        assert review["coverage_counts"]["usage_feedback"] == 3
        assert review["uncovered_usage_feedback"] == [
            "未解释为什么该 skill 应该更新。"
        ]
        assert any("1 usage feedback summaries are not covered" in warning
                   for warning in review["warnings"])
        assert "Increase count" in review["recommendation"]
        assert "manual eval cases" in review["recommendation"]

    def test_quarantine_project_skill_moves_it_out_of_loader_path(
        self, tmp_path: Path
    ) -> None:
        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n",
            encoding="utf-8",
        )
        engine = EvolutionEngine(tmp_path)

        ok, message = engine.quarantine_skill(
            "review-loop",
            reason="用户纠正：该 skill 导致错误复盘流程。",
        )

        assert ok
        quarantine_path = Path(message)
        assert quarantine_path == (
            tmp_path
            / ".mewcode"
            / "evolution"
            / "quarantine"
            / "review-loop"
            / "SKILL.md"
        )
        assert quarantine_path.exists()
        assert not skill_path.exists()
        usage = engine.load_skill_usage()
        assert usage[-1]["event"] == "quarantine"
        assert usage[-1]["skill_name"] == "review-loop"
        assert usage[-1]["metadata"]["reason"] == "用户纠正：该 skill 导致错误复盘流程。"

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

    async def test_preview_command_shows_memory_change(self, tmp_path: Path) -> None:
        ui = MockUI()
        await handle_evolve(_ctx(
            tmp_path,
            "propose remember-preview :: 预览命令必须展示变更。",
            ui,
        ))
        proposal = EvolutionEngine(tmp_path).store.load_proposals()[0]

        await handle_evolve(_ctx(tmp_path, f"preview {proposal.id}", ui))

        message = "\n".join(ui.messages)
        assert "Evolution Preview" in message
        assert "Target: memory" in message
        assert "预览命令必须展示变更" in message

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
        await handle_evolve(_ctx(
            tmp_path,
            f"add-eval-case {proposal.id} :: 复盘复杂任务后怎么沉淀流程？ :: "
            "可复用流程",
            ui,
        ))
        await handle_evolve(_ctx(
            tmp_path,
            f"add-eval-case {proposal.id} :: 复杂任务结束后如何复用经验？ :: "
            "可复用流程",
            ui,
        ))
        await handle_evolve(_ctx(
            tmp_path,
            f"add-eval-case {proposal.id} :: 下次遇到类似问题时怎么复用？ :: "
            "可复用流程",
            ui,
        ))
        await handle_evolve(_ctx(tmp_path, f"eval {proposal.id}", ui))
        await handle_evolve(_ctx(tmp_path, f"run-eval {proposal.id}", ui))
        await handle_evolve(_ctx(tmp_path, f"show-eval {proposal.id}", ui))
        promote_ctx = _ctx(tmp_path, f"promote {proposal.id}", ui)
        promote_ctx.config = {"skill_loader": loader}
        await handle_evolve(promote_ctx)

        skill_path = tmp_path / ".mewcode" / "skills" / "review-to-skill" / "SKILL.md"
        assert skill_path.exists()
        assert parse_skill_file(skill_path).name == "review-to-skill"
        assert any("Execution eval passed" in msg for msg in ui.messages)
        assert any("Skill Execution Eval Report" in msg for msg in ui.messages)
        loader.reload.assert_called_once()

    async def test_add_eval_case_json_command_is_not_user_entrypoint(
        self, tmp_path: Path
    ) -> None:
        ui = MockUI()
        engine = EvolutionEngine(tmp_path)
        proposal = engine.propose_skill(
            name="agent-loop-json",
            description="JSON 命令添加真实 Agent loop 评测 case",
            body="# 任务\n\n先复现失败，再写回归测试，最后实现最小补丁。\n",
            allowed_tools=["ReadFile", "WriteFile"],
        )
        payload = {
            "task": "用 JSON case 驱动 Agent loop 修复文件。",
            "must_contain": ["复现失败", "最小补丁"],
            "workspace_files": {"bug.txt": "broken\n"},
            "scripted_agent_turns": [
                {
                    "assistant": "先读取失败输入。",
                    "tool_calls": [{"tool": "ReadFile", "path": "bug.txt"}],
                },
                {
                    "assistant": "写出修复结果。",
                    "tool_calls": [
                        {
                            "tool": "WriteFile",
                            "path": "result.txt",
                            "content": "fixed\n",
                        }
                    ],
                },
                {"assistant": "修复已完成。"},
            ],
            "expected_files": {"result.txt": "fixed\n"},
            "execution_runner": "agent_loop_scripted",
        }

        await handle_evolve(
            _ctx(
                tmp_path,
                f"add-eval-case-json {proposal.id} :: "
                f"{json.dumps(payload, ensure_ascii=False)}",
                ui,
            )
        )

        assert any("Unknown evolve subcommand" in msg for msg in ui.messages)
        assert not EvolutionEngine(tmp_path).eval_cases_path("agent-loop-json").exists()

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

    async def test_learn_command_points_to_eval_promote_flow(
        self, tmp_path: Path
    ) -> None:
        from mewcode.commands.handlers.learn import handle_learn

        ui = MockUI()
        await handle_learn(_ctx(
            tmp_path,
            "guided-workflow :: 正确启用流程 :: # 任务\n先 case eval，再 promote。",
            ui,
        ))

        message = "\n".join(ui.messages)
        assert "add-eval-case" in message
        assert "/evolve eval" in message
        assert "/evolve run-eval" in message
        assert "/evolve show-eval" in message
        assert "/evolve promote" in message
        assert "then apply" not in message

    async def test_quarantine_command_moves_skill_and_reloads_loader(
        self, tmp_path: Path
    ) -> None:
        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n",
            encoding="utf-8",
        )
        ui = MockUI()
        loader = MagicMock()
        ctx = _ctx(
            tmp_path,
            "quarantine review-loop :: 用户纠正：该 skill 不再可靠。",
            ui,
        )
        ctx.config = {"skill_loader": loader}

        await handle_evolve(ctx)

        assert not skill_path.exists()
        assert (
            tmp_path
            / ".mewcode"
            / "evolution"
            / "quarantine"
            / "review-loop"
            / "SKILL.md"
        ).exists()
        assert any("quarantined" in msg for msg in ui.messages)
        loader.reload.assert_called_once()

    async def test_record_usage_and_suggest_quarantine_commands(
        self, tmp_path: Path
    ) -> None:
        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n",
            encoding="utf-8",
        )
        ui = MockUI()

        await handle_evolve(_ctx(
            tmp_path,
            "record-usage review-loop :: failure :: 错误地跳过复盘文档。",
            ui,
        ))
        await handle_evolve(_ctx(
            tmp_path,
            "record-usage review-loop :: user_feedback :: 用户纠正：遗漏验证。",
            ui,
        ))
        await handle_evolve(_ctx(tmp_path, "suggest-quarantine review-loop", ui))

        message = "\n".join(ui.messages)
        assert "Skill usage recorded" in message
        assert "Quarantine suggestions" in message
        assert "/evolve quarantine review-loop" in message

    async def test_propose_patch_from_usage_command(self, tmp_path: Path) -> None:
        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n\n原流程。\n",
            encoding="utf-8",
        )
        ui = MockUI()
        await handle_evolve(_ctx(
            tmp_path,
            "record-usage review-loop :: failure :: 错误地跳过复盘文档。",
            ui,
        ))
        await handle_evolve(_ctx(
            tmp_path,
            "record-usage review-loop :: user_feedback :: 用户纠正：遗漏验证。",
            ui,
        ))

        await handle_evolve(_ctx(tmp_path, "propose-patch-from-usage review-loop", ui))

        proposal = EvolutionEngine(tmp_path).store.load_proposals()[0]
        payload = json.loads(proposal.change)
        message = "\n".join(ui.messages)
        assert "Usage-driven skill patch proposal created" in message
        assert payload["action"] == "patch"
        assert "用户纠正：遗漏验证" in payload["body"]

    async def test_suggest_eval_cases_command_is_read_only(self, tmp_path: Path) -> None:
        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n\n原流程。\n",
            encoding="utf-8",
        )
        ui = MockUI()
        await handle_evolve(_ctx(
            tmp_path,
            "record-usage review-loop :: failure :: 错误地跳过复盘文档。",
            ui,
        ))
        await handle_evolve(_ctx(
            tmp_path,
            "record-usage review-loop :: user_feedback :: 用户纠正：遗漏验证。",
            ui,
        ))
        await handle_evolve(_ctx(tmp_path, "propose-patch-from-usage review-loop", ui))
        proposal = EvolutionEngine(tmp_path).store.load_proposals()[0]

        await handle_evolve(_ctx(tmp_path, f"suggest-eval-cases {proposal.id}", ui))

        message = "\n".join(ui.messages)
        assert "Suggested eval cases" in message
        assert "Quality summary: high=2, medium=1, low=0" in message
        assert "Recommendation: Add high-quality" in message
        assert "Quality: high" in message
        assert "Coverage: usage_feedback" in message
        assert f"/evolve add-eval-case {proposal.id}" in message
        assert not EvolutionEngine(tmp_path).eval_cases_path("review-loop").exists()

    async def test_suggest_eval_cases_command_shows_uncovered_feedback(
        self, tmp_path: Path
    ) -> None:
        skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(
            "---\n"
            "name: review-loop\n"
            "description: Review flow\n"
            "mode: inline\n"
            "context: recent\n"
            "---\n\n"
            "# Review\n\n原流程。\n",
            encoding="utf-8",
        )
        ui = MockUI()
        for index, summary in enumerate([
            "错误地跳过复盘文档。",
            "用户纠正：遗漏验证。",
            "没有展示测试结果。",
            "未解释为什么该 skill 应该更新。",
        ]):
            event = "failure" if index == 0 else "user_feedback"
            await handle_evolve(_ctx(
                tmp_path,
                f"record-usage review-loop :: {event} :: {summary}",
                ui,
            ))
        await handle_evolve(_ctx(tmp_path, "propose-patch-from-usage review-loop", ui))
        proposal = EvolutionEngine(tmp_path).store.load_proposals()[0]

        await handle_evolve(_ctx(tmp_path, f"suggest-eval-cases {proposal.id}", ui))

        message = "\n".join(ui.messages)
        assert "Warnings: 1 usage feedback summaries are not covered" in message
        assert "Uncovered usage feedback:" in message
        assert "未解释为什么该 skill 应该更新。" in message

    async def test_suggest_eval_cases_command_accepts_count_to_cover_feedback(
        self, tmp_path: Path
    ) -> None:
        engine, proposal = _usage_patch_proposal(
            tmp_path,
            [
                "错误地跳过复盘文档。",
                "用户纠正：遗漏验证。",
                "没有展示测试结果。",
                "未解释为什么该 skill 应该更新。",
            ],
        )
        ui = MockUI()

        await handle_evolve(_ctx(tmp_path, f"suggest-eval-cases {proposal.id} 4", ui))

        message = "\n".join(ui.messages)
        assert "Quality summary: high=4, medium=0, low=0" in message
        assert "Uncovered usage feedback:" not in message
        assert "not covered" not in message
        assert "未解释为什么该 skill 应该更新。" in message
        assert not engine.eval_cases_path("review-loop").exists()
