from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from mewcode.context.manager import build_semantic_compact_plan
from mewcode.context.manager import _compute_keep_start_index
from mewcode.conversation import Message, ToolResultBlock, ToolUseBlock, estimate_tokens


CHARS_PER_TOKEN = 3.5


@dataclass(frozen=True)
class ExperimentCase:
    name: str
    description: str
    messages: list[Message]
    required_terms: list[str]
    noise_indices: list[int]


def _text_tokens(prefix: str, tokens: int) -> str:
    filler_len = max(0, int(tokens * CHARS_PER_TOKEN) - len(prefix))
    return prefix + ("x" * filler_len)


def _tool_noise(tool_use_id: str, label: str, tokens: int) -> Message:
    return Message(
        role="user",
        content="",
        tool_results=[
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content=(label + "\n") * max(1, int(tokens * CHARS_PER_TOKEN / (len(label) + 1))),
            )
        ],
    )


def _assistant_tool(tool_use_id: str, tool_name: str, **arguments: object) -> Message:
    return Message(
        role="assistant",
        content=f"调用 {tool_name} 读取上下文。",
        tool_uses=[ToolUseBlock(tool_use_id=tool_use_id, tool_name=tool_name, arguments=dict(arguments))],
    )


def _joined(messages: list[Message], indices: list[int]) -> str:
    chunks: list[str] = []
    for i in indices:
        msg = messages[i]
        chunks.append(msg.content)
        for tu in msg.tool_uses:
            chunks.append(tu.tool_name)
            chunks.append(json.dumps(tu.arguments, ensure_ascii=False))
        for tr in msg.tool_results:
            chunks.append(tr.content)
    return "\n".join(chunks)


def _case_constraint_buried_by_tail_noise() -> ExperimentCase:
    messages = [
        Message(role="user", content="用户目标：审计代码智能体项目。必须形成 md 文档，并且每一次修改都要留档。"),
        Message(role="assistant", content="设计选择：先按依赖顺序讲基础工具层，因为后续 Agent 依赖这些抽象。"),
        Message(role="assistant", content="已完成第一模块 mewcode/tools/base.py 的逐段讲解。"),
        Message(role="assistant", content="已完成第二模块 mewcode/tools/__init__.py 的逐段讲解。"),
        Message(role="assistant", content="下一步计划：构造数据集进行压缩策略前后对比实验。"),
        _tool_noise("toolu_tail_noise", "irrelevant build log line", 42_000),
    ]
    return ExperimentCase(
        name="constraint_buried_by_tail_noise",
        description="早期用户约束和当前 TODO 被最后一条超大工具噪音挤出旧尾部窗口。",
        messages=messages,
        required_terms=["必须形成 md 文档", "每一次修改都要留档", "下一步计划"],
        noise_indices=[5],
    )


def _case_file_facts_vs_recent_chatter() -> ExperimentCase:
    messages = [
        Message(role="user", content="要求：测试压缩策略前后效果，输出 docs/compact-strategy-experiment-results.md。"),
        _assistant_tool("toolu_read_ctx", "ReadFile", file_path="mewcode/context/manager.py"),
        Message(
            role="user",
            content="",
            tool_results=[
                ToolResultBlock(
                    tool_use_id="toolu_read_ctx",
                    content="关键代码事实：mewcode/context/manager.py 中 build_semantic_compact_plan() 生成语义计划。",
                )
            ],
        ),
        Message(role="assistant", content="设计理由：旧策略只按时间尾部保留，新策略显式保护 constraint、todo、code_fact。"),
        Message(role="assistant", content=_text_tokens("普通进展：整理实验指标。", 3_000)),
        Message(role="assistant", content=_text_tokens("普通进展：整理实验表格。", 3_000)),
        Message(role="assistant", content=_text_tokens("普通进展：准备写结果文档。", 3_000)),
        Message(role="assistant", content=_text_tokens("普通进展：复核留档要求。", 3_000)),
        Message(role="assistant", content=_text_tokens("普通进展：检查输出路径。", 3_000)),
    ]
    return ExperimentCase(
        name="file_facts_vs_recent_chatter",
        description="早期文件事实被多条近期普通对话推远，旧尾部窗口容易只保留最近聊天。",
        messages=messages,
        required_terms=["docs/compact-strategy-experiment-results.md", "mewcode/context/manager.py", "build_semantic_compact_plan"],
        noise_indices=[],
    )


def _case_error_and_decision_recovery() -> ExperimentCase:
    messages = [
        Message(role="user", content="目标：完整对比语义压缩前后效果，不能只写单元测试，要形成测试结果文档。"),
        Message(role="assistant", content="错误记录：git status 失败，原因是当前目录不是 git repository。"),
        Message(role="assistant", content="修正方案：改用项目内 Markdown 变更留档，而不是依赖 git diff。"),
        Message(role="assistant", content="设计选择：实验脚本放到 scripts/，结果文档放到 docs/。"),
        _tool_noise("toolu_mid_noise", "pytest collection noise", 15_000),
        Message(role="assistant", content=_text_tokens("近期普通消息：准备执行脚本。", 4_000)),
        Message(role="assistant", content=_text_tokens("近期普通消息：准备整理输出。", 4_000)),
        Message(role="assistant", content=_text_tokens("近期普通消息：准备追加文档。", 4_000)),
    ]
    return ExperimentCase(
        name="error_and_decision_recovery",
        description="早期错误结论和设计选择对恢复任务有价值，但不一定落在旧策略尾部。",
        messages=messages,
        required_terms=["不能只写单元测试", "git status 失败", "Markdown 变更留档", "scripts/"],
        noise_indices=[4],
    )


def build_dataset() -> list[ExperimentCase]:
    builders: list[Callable[[], ExperimentCase]] = [
        _case_constraint_buried_by_tail_noise,
        _case_file_facts_vs_recent_chatter,
        _case_error_and_decision_recovery,
    ]
    return [builder() for builder in builders]


def _semantic_retained_indices(case: ExperimentCase) -> list[int]:
    plan = build_semantic_compact_plan(case.messages)
    retained = set(plan.keep_verbatim)
    retained.update(plan.structure_extract)
    return sorted(retained)


def _old_retained_indices(case: ExperimentCase) -> list[int]:
    keep_start = _compute_keep_start_index(case.messages)
    return list(range(keep_start, len(case.messages)))


def _evaluate_case(case: ExperimentCase) -> dict[str, object]:
    old_indices = _old_retained_indices(case)
    semantic_indices = _semantic_retained_indices(case)
    plan = build_semantic_compact_plan(case.messages)

    old_text = _joined(case.messages, old_indices)
    semantic_text = _joined(case.messages, semantic_indices)

    old_required_hits = sum(1 for term in case.required_terms if term in old_text)
    semantic_required_hits = sum(1 for term in case.required_terms if term in semantic_text)

    old_noise_kept = sum(1 for i in case.noise_indices if i in old_indices)
    semantic_noise_kept = sum(1 for i in case.noise_indices if i in semantic_indices)

    return {
        "name": case.name,
        "description": case.description,
        "message_count": len(case.messages),
        "total_tokens": estimate_tokens(case.messages),
        "required_terms": len(case.required_terms),
        "old": {
            "retained_indices": old_indices,
            "retained_messages": len(old_indices),
            "retained_tokens": estimate_tokens([case.messages[i] for i in old_indices]),
            "required_hits": old_required_hits,
            "required_recall": old_required_hits / len(case.required_terms),
            "noise_messages_kept": old_noise_kept,
        },
        "semantic": {
            "retained_indices": semantic_indices,
            "retained_messages": len(semantic_indices),
            "retained_tokens": estimate_tokens([case.messages[i] for i in semantic_indices]),
            "required_hits": semantic_required_hits,
            "required_recall": semantic_required_hits / len(case.required_terms),
            "noise_messages_kept": semantic_noise_kept,
            "keep_verbatim": plan.keep_verbatim,
            "structure_extract": plan.structure_extract,
            "summarize": plan.summarize,
            "drop": plan.drop,
            "must_keep_facts": plan.must_keep_facts,
            "meta_summary": [
                {
                    "index": meta.index,
                    "role": meta.role,
                    "tags": sorted(meta.tags),
                    "importance": meta.importance,
                    "tokens": meta.token_estimate,
                    "reason": meta.reason,
                }
                for meta in plan.metas
            ],
        },
    }


def run_experiment() -> dict[str, object]:
    cases = build_dataset()
    results = [_evaluate_case(case) for case in cases]

    old_hits = sum(int(r["old"]["required_hits"]) for r in results)  # type: ignore[index]
    semantic_hits = sum(int(r["semantic"]["required_hits"]) for r in results)  # type: ignore[index]
    required_total = sum(int(r["required_terms"]) for r in results)
    old_noise = sum(int(r["old"]["noise_messages_kept"]) for r in results)  # type: ignore[index]
    semantic_noise = sum(int(r["semantic"]["noise_messages_kept"]) for r in results)  # type: ignore[index]

    return {
        "dataset": {
            "case_count": len(cases),
            "total_messages": sum(len(case.messages) for case in cases),
            "total_required_terms": required_total,
        },
        "summary": {
            "old_required_recall": old_hits / required_total,
            "semantic_required_recall": semantic_hits / required_total,
            "old_required_hits": old_hits,
            "semantic_required_hits": semantic_hits,
            "old_noise_messages_kept": old_noise,
            "semantic_noise_messages_kept": semantic_noise,
        },
        "cases": results,
    }


def main() -> None:
    print(json.dumps(run_experiment(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
