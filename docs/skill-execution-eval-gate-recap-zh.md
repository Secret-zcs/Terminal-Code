# Skill Execution Eval Gate 复盘

> 日期：2026-07-21
> 目标：让候选 skill 在启用前至少通过多轮任务评估，并把测试效果展示给用户。

## 1. 背景

此前 candidate skill 已有两层门禁：

```text
candidate -> validate -> add-eval-case -> eval -> approve -> promote
```

这能证明候选 `SKILL.md` 格式正确、没有明显危险命令、且 SOP 文本覆盖至少一个 eval case 的关键要求。但它还不足以回答两个问题：

- 候选 skill 是否经过多轮任务场景验证？
- 用户在 approve/promote 前能否看到候选 skill 的测试效果？

本次新增 execution eval gate，把 skill 启用路径升级为：

```text
candidate
  -> validate
  -> add-eval-case 至少 3 个任务 case
  -> eval
  -> run-eval 生成多轮报告
  -> show-eval 展示报告
  -> approve
  -> promote
```

## 2. 实现内容

### 2.1 引擎层

`mewcode/evolution/engine.py` 新增：

```python
MIN_EXECUTION_EVAL_CASES = 3
execution_eval_report_path(proposal_id)
execution_eval_markdown_path(proposal_id)
run_execution_eval(proposal_id)
read_execution_eval_report(proposal_id)
```

`run_execution_eval()` 的门禁顺序：

1. proposal 必须存在。
2. proposal target 必须是 `skill`。
3. 普通 `/evolve eval` 必须已通过。
4. candidate `SKILL.md` 必须能解析。
5. eval case 数量必须不少于 3。
6. 每个 case 必须通过 `must_contain` / `must_not_contain` 检查。
7. 写出 JSON 和 Markdown 报告。
8. 更新 candidate manifest。

`promote()` 现在同时要求：

```text
eval_status == "passed"
execution_eval_status == "passed"
```

### 2.2 报告文件

每个 candidate 会新增：

```text
.mewcode/evolution/candidates/<proposal_id>/eval_report.json
.mewcode/evolution/candidates/<proposal_id>/eval_report.md
```

JSON 报告记录结构化结果：

```json
{
  "proposal_id": "prop_xxx",
  "skill_name": "debug-regression-loop",
  "status": "passed",
  "min_cases_required": 3,
  "rounds": [
    {
      "round": 1,
      "case_id": "case_xxx",
      "task": "修复复杂回归 bug 时应该先做什么？",
      "status": "passed",
      "errors": [],
      "execution_summary": "Candidate skill SOP was loaded and checked against this task case. Required behavior was covered."
    }
  ],
  "summary": {
    "total": 3,
    "passed": 3,
    "failed": 0
  }
}
```

Markdown 报告用于 `/evolve show-eval` 直接展示给用户，避免用户只看到一个抽象的 `passed` 状态。

### 2.3 命令层

`mewcode/commands/handlers/evolve.py` 新增：

```text
/evolve run-eval <proposal_id>
/evolve show-eval <proposal_id>
```

`run-eval` 成功时返回：

```text
Execution eval passed: skill execution eval passed: <proposal_id>
```

`show-eval` 直接展示 `eval_report.md`。

### 2.4 `/learn` 提示

`mewcode/commands/handlers/learn.py` 的创建提示和 help 已同步为：

```text
add-eval-case -> eval -> run-eval -> show-eval -> approve -> promote
```

这样用户从显式学习入口创建 candidate 后，不会跳过测试报告查看步骤。

## 3. 测试覆盖

新增和调整的核心测试位于 `tests/test_evolution.py`：

- `test_run_execution_eval_requires_multiple_eval_cases`：少于 3 个 eval case 时拒绝，且不写报告。
- `test_run_execution_eval_writes_user_visible_report`：通过 3 个 case 后写入 JSON/Markdown 报告，并更新 manifest。
- `test_promote_approved_and_evaluated_skill_candidate_to_project_skill`：普通 eval 通过但 execution eval 未通过时仍拒绝 promote。
- `test_promote_approved_evaluated_and_execution_tested_skill`：eval + execution eval + approve 后允许 promote。
- `test_promote_skill_patch_updates_existing_project_skill`：patch skill 也必须经过 execution eval。
- `test_propose_skill_command_promotes_and_reloads_loader`：命令层完整执行 `eval -> run-eval -> show-eval -> promote`，并验证用户消息中出现报告内容。
- `test_learn_command_points_to_eval_promote_flow`：`/learn` 输出必须提示 `run-eval/show-eval`。
- `test_add_eval_case_invalidates_existing_execution_eval`：新增 eval case 后必须失效既有 eval/execution eval，防止旧报告被复用。

TDD 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
1 failed, 27 passed
```

失败原因：命令层未接入 `run-eval/show-eval`，导致完整 promote 流程失败。

追加红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_learn_command_points_to_eval_promote_flow -q
1 failed
```

失败原因：`/learn` 未提示 `run-eval/show-eval`。

绿灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
34 passed
```

扩展回归记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
208 passed
```

格式检查记录：

```text
git diff --check
```

命令无输出，表示未发现 diff whitespace 问题。

全量测试记录：

```text
PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突，和本次 execution eval gate 修改无直接依赖。

### 2026-07-23 Sandbox Artifact Runner 追加记录

本次继续补齐 execution eval 的可审计性：`run-eval` 不再只写汇总报告，而是为每一轮任务评估生成隔离 sandbox 产物。

新增路径：

```text
.mewcode/evolution/candidates/<proposal_id>/execution_sandbox/
  round_01_<case_id>/
    task.md
    SKILL.md
    rendered_prompt.md
    result.json
```

新增报告字段：

```json
{
  "runner": "sandbox_deterministic",
  "sandbox_root": ".mewcode/evolution/candidates/prop_xxx/execution_sandbox",
  "rounds": [
    {
      "sandbox_dir": ".../round_01_case_xxx",
      "artifacts": {
        "task": ".../task.md",
        "skill": ".../SKILL.md",
        "rendered_prompt": ".../rendered_prompt.md",
        "result": ".../result.json"
      }
    }
  ]
}
```

实现约束：

- sandbox 根目录固定在 candidate 目录下。
- 每次 `run-eval` 前清理旧 sandbox，避免混入历史产物。
- round 目录名会对 case id 做 slug 化，避免路径逃逸。
- 每轮都保存任务说明、候选 skill 快照、渲染后的 SOP 和结构化结果。

TDD 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_creates_sandbox_artifacts -q
1 failed
```

失败原因符合预期：报告缺少 `runner` / `sandbox_root`，也没有每轮 sandbox artifact。

绿灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_creates_sandbox_artifacts -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py -q
34 passed
```

## 4. 设计取舍

当前 execution eval 已经是确定性 sandbox artifact runner，但还不是完整 Hermes 式真实 fork agent 任务回放，也不是调用模型执行一组沙盒任务。它是一个确定性、可重复、低副作用的门禁：

- 优点：不需要 LLM key，不会修改真实项目，结果稳定，适合单元测试。
- 优点：能强制用户为 candidate skill 准备至少 3 个任务 case。
- 优点：能在 approve/promote 前展示每轮 case 的测试效果。
- 优点：每轮会落地 task、skill snapshot、rendered prompt 和 result，便于用户审计。
- 缺点：只能证明 SOP 文本覆盖关键策略，不能证明模型加载该 skill 后一定能完成真实任务。
- 缺点：sandbox 中的执行者仍是 deterministic checker，不是受限 fork agent。
- 缺点：`must_contain` / `must_not_contain` 仍依赖人工定义，case 质量决定评估质量。

因此它比原先的 eval case gate 更安全，但仍不是最终形态。

## 5. 后续建议

下一阶段应把 execution eval 从 deterministic sandbox runner 升级为受限 fork agent runner：

- 使用受限 fork agent 执行 eval case。
- 限制工具白名单，禁止写真实项目。
- 记录模型输出、工具调用、失败原因和最终判定。
- 将当前 sandbox artifacts 扩展为真实执行轨迹。
- 通过多轮 case 后只提交应用申请，不自动 promote。
- 用户在 `show-eval` 中看到真实执行轨迹后，再决定 approve/promote。

核心原则保持不变：

```text
候选 skill 不是正式能力；
只有经过多轮测试、报告展示和用户授权，才能进入长期 skill 库。
```
