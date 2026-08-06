# 自进化配置驱动与审批模式复盘

日期：2026-07-30

## 背景

此前实现把自进化逐步扩展成 `/evolve`、`/learn`、`add-eval-case`、`add-eval-case-json` 等用户命令。这个方向的问题是：它让用户手动提交 skill、评测用例甚至复杂 JSON，偏离 Hermes 风格的“系统自动从任务经验中抽取候选 skill，再交给用户审批”的产品边界。

本次收敛后的原则：

- 用户只能通过配置开启或关闭自进化。
- 自进化开启后，候选 skill 的抽取、评测用例生成和执行评测应由系统自动完成。
- candidate skill 不能自动启用，必须先生成评测报告，再进入用户审批。
- 用户可以配置审批模式，但审批模式不能绕过人工审批。

## 当前实现

新增配置段：

```yaml
self_evolution:
  enabled: false
  skill_approval_mode: manual
```

字段语义：

- `enabled: false`：默认关闭自进化，避免后台学习默认影响用户工作流。
- `enabled: true`：允许系统自动产生 candidate skill 和评测报告。
- `skill_approval_mode: manual`：每个通过评测的 candidate skill 单独提交审批。
- `skill_approval_mode: deferred`：系统可以先排队审批申请，但仍不能自动 promote。

实现位置：

- `mewcode/validator.py`：新增 `validate_self_evolution()`，校验 `enabled` 和 `skill_approval_mode`。
- `mewcode/config.py`：新增 `SelfEvolutionConfig`，并把配置挂到 `AppConfig.self_evolution`。
- `mewcode/commands/handlers/__init__.py`：普通命令注册表不再注册 `/evolve` 和 `/learn`。
- `mewcode/commands/handlers/evolve.py`：删除 `add-eval-case-json` 命令层入口。
- `README.md`：把自进化说明从命令驱动改成配置驱动。
- `mewcode/evolution/models.py`：新增 `SkillApprovalRequest`，记录待用户审批的 candidate skill。
- `mewcode/evolution/store.py`：新增 `approval_requests.jsonl` 的读写与 pending request 查询。
- `mewcode/evolution/engine.py`：新增 `submit_skill_approval_request()`，只为已通过 eval 和 execution eval 的 proposed skill candidate 创建审批申请。
- `mewcode/evolution/auto_review.py`：新增自动扫描器，开启自进化时把 ready candidate 提交为 pending approval request。
- `mewcode/app.py`、`mewcode/__main__.py`：在 TUI 轮次结束和 `mewcode -p` 执行结束后触发自动 review，并只展示申请提示，不审批、不 promote。

## 审批申请 Resolve

本次继续补齐审批申请的处理状态机：系统已有 pending approval request 后，用户审批结果会被写回 `approval_requests.jsonl` 和 candidate manifest。批准时才会执行 `approve -> promote`，拒绝时只会 reject proposal，不会写入正式 skill。

新增字段：

- `resolved_at`：审批处理时间戳。
- `reviewer`：处理人，默认 `user`。
- `resolution_reason`：批准或拒绝理由。
- `result_path`：批准并 promote 后的正式 skill 路径。

新增实现：

- `mewcode/evolution/store.py`：新增 `get_skill_approval_request()` 和 `update_skill_approval_request()`，支持按 request id 查询和覆盖更新。
- `mewcode/evolution/engine.py`：新增 `resolve_skill_approval_request()`，统一处理批准/拒绝。
- `mewcode/evolution/engine.py`：新增 `_mark_candidate_approval_resolved()`，把审批结果同步写回 candidate manifest。
- `tests/test_evolution.py`：新增批准后 promote、拒绝后不 promote 的行为测试。

边界：

- 该接口是系统内部审批处理能力，不重新暴露 `/evolve` 手动命令。
- 当前仍缺用户可见审批视图；后续 UI/API 只应调用该状态机，不应绕过评测门禁或直接 promote。

## 为什么保留 Engine 内部能力

删除用户命令不等于删除评测能力。`EvolutionEngine.add_eval_case()` 仍保留 `workspace_files`、`scripted_agent_turns`、`expected_files` 和 `execution_runner="agent_loop_scripted"` 等内部字段，因为自动自进化仍需要它们构造可回放任务评测。

关键区别：

- 用户不再手写高级 JSON case。
- 系统内部仍可以自动生成结构化 eval case。
- 审批前仍会向用户展示 candidate diff、执行评测报告和关键通过/失败原因。

## TDD 留档

新增红灯：

```text
PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution -q
3 failed  # AppConfig 尚无 self_evolution；非法 approval mode 未被拒绝

PYTHONPATH=. pytest tests/test_commands.py::TestRegisterAllCommands -q
2 failed  # /evolve 和 /learn 仍在普通命令注册表中

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_add_eval_case_json_command_is_not_user_entrypoint -q
1 failed  # add-eval-case-json 仍是命令层入口
```

实现后绿色：

```text
PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution -q
3 passed

PYTHONPATH=. pytest tests/test_commands.py::TestRegisterAllCommands -q
4 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_add_eval_case_json_command_is_not_user_entrypoint -q
1 passed
```

审批队列追加红灯：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_records_pending_request -q
1 failed  # 实现前红灯：EvolutionEngine 尚无 submit_skill_approval_request

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_requires_execution_eval -q
1 failed  # 实现前红灯：缺少审批申请 API

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_disabled_skips_ready_candidates tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_ready_candidates_once -q
2 failed  # 实现前红灯：缺少 mewcode.evolution.auto_review
```

审批队列实现后绿色：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_records_pending_request tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_requires_execution_eval tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_disabled_skips_ready_candidates tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_ready_candidates_once -q
4 passed
```

审批队列扩展验证：

```text
python3 -m py_compile mewcode/evolution/models.py mewcode/evolution/store.py mewcode/evolution/engine.py mewcode/evolution/auto_review.py mewcode/evolution/__init__.py mewcode/app.py mewcode/__main__.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
55 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
236 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次审批申请队列无直接关系。

审批 Resolve 红灯：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_resolve_skill_approval_request_approved_promotes_candidate -q
1 failed  # 实现前红灯：EvolutionEngine 尚无 resolve_skill_approval_request

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_resolve_skill_approval_request_rejected_rejects_without_promote -q
1 failed  # 实现前红灯：无法处理审批拒绝并保持正式 skill 不落地
```

审批 Resolve 实现后绿色：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_resolve_skill_approval_request_approved_promotes_candidate -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_resolve_skill_approval_request_rejected_rejects_without_promote -q
1 passed
```

审批 Resolve 扩展验证：

```text
python3 -m py_compile mewcode/evolution/models.py mewcode/evolution/store.py mewcode/evolution/engine.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
57 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
238 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次审批申请 Resolve 无直接关系。

审批详情红灯：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_review_materials -q
1 failed  # 实现前红灯：EvolutionEngine 尚无 render_skill_approval_request
```

审批详情实现后绿色：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_review_materials -q
1 passed
```

审批详情扩展验证：

```text
python3 -m py_compile mewcode/evolution/engine.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
58 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
239 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次审批详情 API 无直接关系。

审批详情边界：

- `render_skill_approval_request()` 是只读内部 API，用于后续 UI/API 展示审批材料。
- 输出包含 request/proposal/skill/status、candidate diff 和 execution eval Markdown 报告。
- 该 API 不 approve、不 reject、不 promote，也不新增 `/evolve` 等用户命令。

审批 Inbox 红灯：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_list_skill_approval_inbox_defaults_to_pending_requests -q
1 failed  # 实现前红灯：EvolutionEngine 尚无 list_skill_approval_inbox
```

审批 Inbox 实现后绿色：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_list_skill_approval_inbox_defaults_to_pending_requests -q
1 passed
```

审批 Inbox 扩展验证：

```text
python3 -m py_compile mewcode/evolution/engine.py
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
59 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
240 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次审批 Inbox API 无直接关系。

审批 Inbox 边界：

- `list_skill_approval_inbox()` 默认只返回 pending request，避免 UI 默认混入已处理申请。
- `status=None` 返回全部 request，用于审计视图。
- 该 API 只读，不会触发 approve、reject 或 promote。

## TUI 审批入口

本次把审批能力接入 Textual TUI，但没有新增用户命令。系统自动 review 发现 ready candidate 后，会打开内联审批组件展示 candidate diff 和 execution eval 报告；用户在组件中批准后，系统才会调用 `resolve_skill_approval_request()` 并 promote 到正式 skill。

新增实现：

- `mewcode/self_evolution_dialog.py`：新增 `InlineSkillApprovalWidget`，提供 approve/reject 选择和拒绝理由输入。
- `mewcode/app.py`：review 产生新 request 时自动打开审批组件。
- `mewcode/app.py`：已有 pending request 时也会打开审批组件，避免申请沉默滞留。
- `mewcode/app.py`：批准后 reload skill loader，并刷新 agent 的 skill catalog。
- `tests/test_self_evolution_dialog.py`：覆盖组件展示与事件。
- `tests/test_evolution.py`：覆盖 TUI 打开审批、已有 pending 打开、批准后 promote/reload。

TDD 与验证：

```text
PYTHONPATH=. pytest tests/test_self_evolution_dialog.py -q
1 error  # 实现前红灯：缺少 mewcode.self_evolution_dialog

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_approval_widget -q
1 failed  # 实现前红灯：TUI review 未打开审批组件

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills -q
1 failed  # 实现前红灯：缺少审批响应处理

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_existing_pending_request -q
1 failed  # 实现前红灯：已有 pending request 被忽略

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_approval_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_existing_pending_request tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills -q
5 passed

python3 -m py_compile mewcode/app.py mewcode/self_evolution_dialog.py
无输出

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py -q
64 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
245 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次 TUI 自进化审批入口无直接关系。

扩展验证：

```text
python3 -m py_compile mewcode/config.py mewcode/validator.py mewcode/commands/handlers/evolve.py mewcode/commands/handlers/__init__.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
232 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次自进化配置/命令入口收敛无直接关系。

## 自动 Usage Patch Candidate 生成

本次开始补齐“开启自进化后系统自动抽取候选 skill”的第一段能力。系统现在不需要用户通过命令提交 patch；`auto_review` 会扫描 `.mewcode/evolution/skill_usage.jsonl`，当某个项目级正式 skill 达到负向 usage 阈值后，自动生成隔离的 skill patch proposal。

新增实现：

- `mewcode/evolution/auto_review.py`：`review_ready_skill_candidates()` 返回 `generated_candidates`，让调用方能区分“提交了审批申请”和“刚生成了候选 proposal”。
- `mewcode/evolution/auto_review.py`：自进化开启时，复用 `suggest_quarantine(failure_threshold=2)` 找到失败或用户纠正次数足够的 skill。
- `mewcode/evolution/auto_review.py`：调用 `propose_skill_patch_from_usage()` 生成 patch proposal，patch body 会保留 usage feedback 摘要，方便后续审查。
- `mewcode/evolution/auto_review.py`：检测已有同名 open patch candidate，避免同一个 skill 反复生成重复 proposal。
- `tests/test_evolution.py`：覆盖自动生成候选和幂等去重。

审批边界：

- 该步骤只生成 proposal/candidate，不提交 approval request。
- 该步骤不会 promote，也不会修改正式 `.mewcode/skills/<name>/SKILL.md`。
- 后续仍必须补 eval case、通过 deterministic/execution eval，并由用户审批后才能启用。
- 用户仍只需要配置开启/关闭自进化和审批模式，不需要手动执行 `/evolve` 类命令。

TDD 与验证：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_creates_usage_patch_candidate -q
1 failed  # 实现前红灯：auto_review 返回值缺少 generated_candidates

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_creates_usage_patch_candidate -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_duplicate_usage_patch_candidate -q
1 passed

python3 -m py_compile mewcode/evolution/auto_review.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
64 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
247 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次 usage-driven patch candidate 自动生成无直接关系。

## 自动候选 Eval 建议摘要

本次继续把自动生成的 patch proposal 接入评测准备阶段。系统现在会在生成 usage-driven patch proposal 后，立即调用 `review_eval_case_suggestions()` 生成只读 eval 建议摘要，并通过 `generated_candidate_reviews` 返回给调用方。

新增实现：

- `mewcode/evolution/auto_review.py`：`review_ready_skill_candidates()` 新增 `generated_candidate_reviews` 字段。
- `mewcode/evolution/auto_review.py`：关闭自进化时也返回空 reviews，保持调用方处理逻辑稳定。
- `mewcode/evolution/auto_review.py`：每个新生成 patch proposal 都会返回 quality counts、coverage counts、warnings、recommendation 和 suggestions。
- `tests/test_evolution.py`：覆盖自动 review 返回 eval 建议摘要，并验证此阶段不会写入 eval case 文件。

审批边界：

- 该步骤仍然只读，不会把 suggestions 写入 `.mewcode/evolution/eval_cases/`。
- 该步骤不会触发 deterministic eval 或 execution eval。
- 该步骤不会提交 approval request，更不会 promote 正式 skill。
- 它的作用是让后续 TUI 或自动门禁能展示“为什么这个 candidate 需要进化、建议怎么测试”。

TDD 与验证：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_returns_eval_suggestions_for_generated_patch -q
1 failed  # 实现前红灯：结果缺少 generated_candidate_reviews

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_returns_eval_suggestions_for_generated_patch -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_creates_usage_patch_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_duplicate_usage_patch_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_ready_candidates_once -q
3 passed

python3 -m py_compile mewcode/evolution/auto_review.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
65 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
248 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次 eval 建议摘要返回无直接关系。

## 受控物化 Eval Case

本次把上一阶段的只读 eval 建议升级为受控写入。自动 review 只有在建议质量和覆盖都满足门禁时，才会把 suggestions 写入 eval case 文件，为后续 deterministic eval 和 execution eval 做准备。

新增实现：

- `mewcode/evolution/auto_review.py`：`review_ready_skill_candidates()` 新增 `generated_eval_cases` 字段。
- `mewcode/evolution/auto_review.py`：新增 `_materialize_safe_eval_suggestions()`，将通过门禁的 suggestions 写入 eval case。
- `mewcode/evolution/auto_review.py`：新增 `_review_is_safe_to_materialize()`，要求无 warnings、无 uncovered usage feedback、至少 3 条 suggestions、且没有 low quality suggestion。
- `tests/test_evolution.py`：覆盖安全建议写入 eval case。
- `tests/test_evolution.py`：覆盖 coverage 不足时拒绝写入 eval case。

审批边界：

- 该步骤只推进测试材料，不提交 approval request。
- 该步骤不会运行 execution eval，也不会 promote 正式 skill。
- 覆盖不足、存在 warning、数量不足或 low quality 时，系统保留 review，但不写 eval case。
- 这比 Hermes 原始“模型生成 skill 后直接复用”的风险更低，因为 candidate 进入审批前必须先形成可回放测试证据。

TDD 与验证：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_materializes_safe_eval_suggestions -q
1 failed  # 实现前红灯：结果缺少 generated_eval_cases，未写入 eval case

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_materializes_safe_eval_suggestions -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_returns_eval_suggestions_for_generated_patch tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_materializes_safe_eval_suggestions tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_materialize_uncovered_eval_suggestions tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_creates_usage_patch_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_duplicate_usage_patch_candidate -q
5 passed

python3 -m py_compile mewcode/evolution/auto_review.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
67 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
250 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次 eval case 受控物化无直接关系。

## 自动 Deterministic Eval

本次把受控写入的 eval cases 接入 deterministic eval。自动 review 只会对刚刚成功物化 eval case 的 candidate 执行 `evaluate()`，并通过 `generated_evaluations` 返回结果；这一步仍不触发 execution eval、approval request 或 promote。

新增实现：

- `mewcode/evolution/auto_review.py`：`review_ready_skill_candidates()` 新增 `generated_evaluations` 字段。
- `mewcode/evolution/auto_review.py`：关闭自进化时也返回空 evaluations，保持结构稳定。
- `mewcode/evolution/auto_review.py`：新增 `_evaluate_generated_candidates()`，执行 deterministic eval 并返回 `proposal_id`、`skill_name`、`ok`、`message`。
- `tests/test_evolution.py`：覆盖自动 eval 后 manifest 写入 `eval_status=passed`。
- `tests/test_evolution.py`：确认该阶段不会生成 execution eval report，也不会创建 approval request。

审批边界：

- deterministic eval 通过只是进入下一门禁，不等于可审批或可启用。
- 未生成 eval case 的 candidate 不会被自动 evaluate。
- deterministic eval 后仍必须通过 execution eval，用户审批时才能看到完整测试证据。

TDD 与验证：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_runs_eval_after_materializing_cases -q
1 failed  # 实现前红灯：结果缺少 generated_evaluations

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_runs_eval_after_materializing_cases -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_materializes_safe_eval_suggestions tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_materialize_uncovered_eval_suggestions -q
2 passed

python3 -m py_compile mewcode/evolution/auto_review.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
68 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
251 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次 deterministic eval 自动触发无直接关系。

## 自动 Execution Eval

本次把 deterministic eval 通过的 generated candidate 继续推进到 execution eval。自动 review 会调用 `run_execution_eval()`，生成 JSON/Markdown 报告，并通过 `generated_execution_evals` 返回结果。

新增实现：

- `mewcode/evolution/auto_review.py`：`review_ready_skill_candidates()` 新增 `generated_execution_evals` 字段。
- `mewcode/evolution/auto_review.py`：关闭自进化时也返回空 execution evals，保持结构稳定。
- `mewcode/evolution/auto_review.py`：新增 `_run_execution_evals_for_generated_candidates()`，只处理 deterministic eval 成功的 candidate。
- `tests/test_evolution.py`：覆盖 execution eval 自动通过、报告写入、manifest 写入 `execution_eval_status=passed`。
- `tests/test_evolution.py`：确认该阶段仍不创建 approval request。

审批边界：

- execution eval 通过后才具备提交审批的基础证据。
- execution eval 失败时应保留报告，但不能进入审批队列。
- 当前阶段仍不会自动 promote，用户仍必须审批。

TDD 与验证：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_runs_execution_eval_after_eval_passes -q
1 failed  # 实现前红灯：结果缺少 generated_execution_evals

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_runs_execution_eval_after_eval_passes -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_runs_eval_after_materializing_cases tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_materialize_uncovered_eval_suggestions -q
2 passed

python3 -m py_compile mewcode/evolution/auto_review.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
69 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
252 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次 execution eval 自动触发无直接关系。

## 自动 Approval Request

本次把 execution eval 通过后的 candidate 接入审批申请。自动 review 会调用 `submit_skill_approval_request()` 创建 pending request，并把该 request 放入 `requests` 返回字段；TUI 现有审批入口可以直接展示 candidate diff 和 execution eval 报告。

新增实现：

- `mewcode/evolution/auto_review.py`：新增 `_submit_generated_approval_requests()`，只对 execution eval 成功的 candidate 创建 request。
- `mewcode/evolution/auto_review.py`：自动生成路径复用 `requests` 字段，避免 TUI 另接一套入口。
- `tests/test_evolution.py`：新增审批申请测试，确认 `skill_approval_mode` 使用配置值。
- `tests/test_evolution.py`：更新阶段性测试断言，让完整链路反映 pending approval request。

审批边界：

- 自动 review 只创建 pending request，不 approve、不 promote。
- 用户仍必须在 TUI/审批入口中 approve 或 reject。
- coverage 不足、eval case 未写入、deterministic eval 失败或 execution eval 失败时，不会生成 approval request。

TDD 与验证：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_generated_candidate_after_execution_eval -q
1 failed  # 实现前红灯：execution eval 通过后 requests 仍为空

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_generated_candidate_after_execution_eval -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_creates_usage_patch_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_returns_eval_suggestions_for_generated_patch tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_materializes_safe_eval_suggestions tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_runs_eval_after_materializing_cases tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_runs_execution_eval_after_eval_passes tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_generated_candidate_after_execution_eval tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_materialize_uncovered_eval_suggestions tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_duplicate_usage_patch_candidate -q
8 passed

python3 -m py_compile mewcode/evolution/auto_review.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
70 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
253 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次 approval request 自动创建无直接关系。

## Evidence 自动归因到 Skill Usage

本次开始扩展自动候选来源。系统不再只依赖显式 `skill_usage.jsonl`；当 evolution evidence 是 `failure` 或 `user_feedback`，并且 metadata 明确标注 `skill_name` 或 `skill`，且目标是项目级正式 skill 时，auto review 会把 evidence 转成负向 skill usage，再进入已有的 candidate/eval/execution eval/approval request 闭环。

新增实现：

- `mewcode/evolution/auto_review.py`：新增 `ingested_usage` 返回字段。
- `mewcode/evolution/auto_review.py`：新增 `_ingest_evidence_as_skill_usage()`，负责结构化 evidence 到 skill usage 的归因。
- `mewcode/evolution/auto_review.py`：新增 `_skill_usage_evidence_ids()`，通过 `metadata.evidence_id` 做幂等去重。
- `mewcode/evolution/auto_review.py`：跳过 `source=skill-usage` 的内部 evidence，避免系统自反馈循环。
- `tests/test_evolution.py`：覆盖 evidence 自动摄入和重复 review 不重复摄入。

审批边界：

- 系统只接受 metadata 明确标注的 skill，不从自由文本猜测 skill 名称。
- evidence 必须指向已有项目级正式 skill，否则不会摄入。
- 同一 evidence 只摄入一次。
- 内部 `skill-usage` evidence 不会再次变成 usage，避免自我放大。
- 摄入后仍必须经过 eval case、deterministic eval、execution eval 和用户审批，不能自动 promote。

TDD 与验证：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_ingests_evidence_as_skill_usage tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_reingest_same_evidence_usage -q
2 failed  # 实现前红灯：没有 evidence -> usage 摄入逻辑

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_ingests_evidence_as_skill_usage tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_reingest_same_evidence_usage -q
1 failed, 1 passed  # 暴露 source=skill-usage 内部 evidence 被二次摄入

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_ingests_evidence_as_skill_usage tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_reingest_same_evidence_usage -q
2 passed

python3 -m py_compile mewcode/evolution/auto_review.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py -q
72 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
255 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次 evidence 自动归因无直接关系。

## 工具失败自动记录 Evidence

本次把 evidence 来源前移到 Agent 工具执行层。工具执行失败时，如果当前只有一个 active skill，Agent 会自动记录 `failure` evidence，并携带明确的 `skill_name` 和 `tool_name`；后续 auto review 会把该 evidence 摄入为 skill usage。

新增实现：

- `mewcode/agent.py`：新增 `_record_tool_failure_evidence()`。
- `mewcode/agent.py`：普通工具执行和并发工具执行都会在失败结果后尝试记录 evidence。
- `tests/test_agent.py`：覆盖单 active skill 时记录 evidence。
- `tests/test_agent.py`：覆盖多个 active skill 时不记录 evidence，避免误归因。

审批边界：

- 工具失败只记录 evidence，不直接创建 candidate。
- 多 active skill 场景不自动归因。
- evidence 后续仍要经过 usage 阈值、patch proposal、eval、execution eval 和用户审批。

TDD 与验证：

```text
PYTHONPATH=. pytest tests/test_agent.py::test_tool_failure_records_evidence_for_single_active_skill tests/test_agent.py::test_tool_failure_does_not_record_evidence_for_ambiguous_skills -q
1 failed, 1 passed  # 实现前红灯：单 active skill 工具失败没有 evidence

PYTHONPATH=. pytest tests/test_agent.py::test_tool_failure_records_evidence_for_single_active_skill tests/test_agent.py::test_tool_failure_does_not_record_evidence_for_ambiguous_skills -q
2 passed

python3 -m py_compile mewcode/agent.py mewcode/evolution/auto_review.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_agent.py -q -k 'not test_multi_step_autonomous'
FAILED tests/test_agent.py::test_message_splicing

PYTHONPATH=. pytest tests/test_evolution.py -q
72 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
255 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

`test_message_splicing` 和 `test_multi_step_autonomous` 都是既有 agent 测试口径差异，和本次 evidence 记录无直接关系。

## 用户纠正自动记录 Evidence

本次把用户自然语言纠正接入 evidence 来源。Agent 会在运行开始时扫描用户消息；如果只有一个 active skill，且用户消息含明确纠正标记，会记录 `user_feedback` evidence。auto review 后续会把该 evidence 摄入为 skill usage。

新增实现：

- `mewcode/agent.py`：新增 `_record_user_feedback_evidence()`，在 `run()` 开头扫描用户纠正文案。
- `mewcode/agent.py`：新增 `_looks_like_user_correction()` 和 `_feedback_message_hash()`，用于识别和去重。
- `mewcode/agent.py`：新增 `_feedback_evidence_exists()`，避免跨 Agent 实例重复记录同一纠正。
- `tests/test_agent.py`：覆盖单 active skill 用户纠正记录 evidence。
- `tests/test_agent.py`：覆盖多个 active skill 时不自动归因。

审批边界：

- 用户纠正只记录 evidence，不直接创建 candidate。
- 多 active skill 场景不自动归因，避免错误推动某个 skill 进化。
- evidence 后续仍要经过 usage 阈值、patch proposal、eval、execution eval 和用户审批。

TDD 与验证：

```text
PYTHONPATH=. pytest tests/test_agent.py::test_user_correction_records_feedback_evidence_for_single_active_skill tests/test_agent.py::test_user_correction_does_not_record_feedback_for_ambiguous_skills -q
1 failed, 1 passed  # 实现前红灯：单 active skill 用户纠正没有 evidence

PYTHONPATH=. pytest tests/test_agent.py::test_user_correction_records_feedback_evidence_for_single_active_skill tests/test_agent.py::test_user_correction_does_not_record_feedback_for_ambiguous_skills -q
2 passed

python3 -m py_compile mewcode/agent.py mewcode/evolution/auto_review.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_agent.py::test_tool_failure_records_evidence_for_single_active_skill tests/test_agent.py::test_tool_failure_does_not_record_evidence_for_ambiguous_skills tests/test_agent.py::test_user_correction_records_feedback_evidence_for_single_active_skill tests/test_agent.py::test_user_correction_does_not_record_feedback_for_ambiguous_skills -q
4 passed

PYTHONPATH=. pytest tests/test_evolution.py -q
72 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_dialog.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
255 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次用户纠正 evidence 记录无直接关系。

## 剩余工作

- 为审批页展示 evidence 来源：tool-result、conversation、manual、skill-usage。
- 完善用户可见审批入口：补拒绝路径 UI 测试、manual/deferred 差异化展示和批量审批队列视图。
- 实现 `manual`、`deferred` 与 `trusted-auto` 的策略差异：manual/deferred 保留用户最终审批，trusted-auto 只允许本轮自动生成且通过全部评测门禁的 candidate 自动 promote。
- 补充多轮任务回放评测，确保 candidate skill 在若干轮任务正确执行后才进入审批。

## Trusted-Auto 策略模式

本次新增 `self_evolution.skill_approval_mode: trusted-auto`。该模式不是完全放开自进化，而是把“用户最终审批”替换为“策略化审批”：只有 auto review 本轮从 usage evidence 生成、自动 materialize eval case、通过 deterministic eval、通过 execution eval 的 candidate skill，才会由 `self-evolution-policy` 自动 resolve approval request 并 promote。

关键边界：

- `manual`：生成 pending approval request，用户逐个审批。
- `deferred`：生成 pending approval request，可延后集中审批。
- `trusted-auto`：generated candidate 满足全部门禁后自动 approve/promote；已有 ready candidate 仍只进入 pending request。
- 所有模式都会保留 approval request、candidate manifest 和 fork reviewer report，便于审计和回滚。

验证记录：

```text
PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_use_trusted_auto_approval tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_promotes_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_keeps_existing_ready_candidate_pending -q
3 passed
```

## Trusted-Auto 自动回滚隔离

`trusted-auto` 现在新增自动回滚隔离：如果由 trusted-auto promote 的 skill 在 approval resolved 之后又出现新的 `failure` 或 `user_feedback` usage，下一次 auto review 会自动调用 `quarantine_skill(source="trusted-auto-rollback")`，把正式项目 skill 移出 `.mewcode/skills`。

关键边界：

- 只处理 `approval_mode=trusted-auto` 且已 approved 的 request。
- 只统计 `created_at > resolved_at` 的负面 usage，避免 promote 前的旧失败立即触发回滚。
- 回滚是 quarantine，不是版本级恢复；skill 会进入 `.mewcode/evolution/quarantine/`。
- 当前策略偏安全：一次 promote 后新负面 usage 即隔离；后续可改成可配置阈值。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_quarantines_after_new_negative_usage -q
1 passed
```

## Trusted-Auto Rollback Threshold

新增配置：

```yaml
self_evolution:
  enabled: true
  skill_approval_mode: trusted-auto
  trusted_auto_rollback_threshold: 2
```

默认 `trusted_auto_rollback_threshold: 1`，表示 trusted-auto promote 后出现一条新的 `failure` 或 `user_feedback` usage 即 quarantine。设置为 `2` 后，需要两条 promote 后新负面 usage 才会触发自动隔离。该阈值不统计 promote 前用于生成 candidate 的历史失败。

验证记录：

```text
PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_set_trusted_auto_rollback_threshold tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_uses_rollback_threshold -q
2 passed
```

## Trusted-Auto Rollback Events

新增配置：

```yaml
self_evolution:
  enabled: true
  skill_approval_mode: trusted-auto
  trusted_auto_rollback_events:
    - user_feedback
```

默认 `trusted_auto_rollback_events: [failure, user_feedback]`。如果配置为只包含 `user_feedback`，则 trusted-auto promote 后的普通 `failure` 不会触发自动 quarantine；只有明确用户纠正类 usage 才会回滚隔离。该配置和 `trusted_auto_rollback_threshold` 组合使用，threshold 只统计命中的 event 类型。

验证记录：

```text
PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_set_trusted_auto_rollback_events tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_filters_rollback_events -q
2 passed
```

## Trusted-Auto Policy 审计展示

fork reviewer 的审计产物现在会记录并展示本次 `trusted-auto` 生效的策略参数，避免用户只看到“自动通过/自动回滚”结果，却不知道自动决策依据。

新增审计字段：

```json
{
  "trusted_auto_policy": {
    "auto_promote_scope": "same_pass_generated_candidates_only",
    "rollback_threshold": 2,
    "rollback_events": ["user_feedback"]
  }
}
```

展示位置：

- `.mewcode/evolution/review_runs/<run_id>/input.json`：保存本次 review 启动时的配置快照。
- `.mewcode/evolution/review_runs/<run_id>/policy.json`：保存 fork reviewer 能力边界和 trusted-auto policy。
- `.mewcode/evolution/review_runs/<run_id>/report.md`：新增 `## Trusted-Auto Policy`，展示 auto promote scope、rollback threshold 和 rollback events。

关键边界：

- `auto_promote_scope` 固定为 `same_pass_generated_candidates_only`，表示只有本轮自动生成并通过全部 eval/execution eval 的 candidate 才能自动 promote。
- 既有 ready candidate 即使处于 `trusted-auto` 模式，也仍进入 pending approval request，不会因为策略展示而被自动应用。
- 该改动只增强可审计性，不放宽 gate，也不改变 rollback 判定。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_trusted_auto_policy_in_run_artifacts -q
1 failed  # 实现前红灯：input.json 缺少 trusted_auto_policy

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_trusted_auto_policy_in_run_artifacts -q
1 passed
```

## Candidate Canary 执行产物

execution eval 现在会把候选 skill 作为 canary 临时注入到每轮 child agent 沙盒，而不是直接写入正式项目 skill 目录。审批请求仍然依赖 execution eval 通过；`trusted-auto` 也仍然只能在 deterministic eval 和 execution eval 全部通过后自动 promote。

每轮产物新增：

```text
.mewcode/evolution/candidates/<proposal_id>/execution_sandbox/
  round_XX_<case>/
    child_agent/
      .mewcode/skills/<skill>/SKILL.md
      input.json
      tool_policy.json
      transcript.md
      final_answer.md
```

关键字段：

- `input.json.injected_skill.mode = candidate_canary`
- `input.json.injected_skill.path = <child_agent canary SKILL.md>`
- `result.json.fork_agent.canary_skill.path = <child_agent canary SKILL.md>`
- execution eval Markdown 每轮展示 `Canary Skill`

关键边界：

- canary 注入只发生在 execution eval sandbox 内，不会提前写入 `.mewcode/skills`。
- approval request 和 trusted-auto promote 仍读取 execution eval 状态，不会因为存在 canary 文件而绕过审批。
- canary 的作用是证明候选 skill 在隔离 child agent 中能完成多轮任务；正式启用仍由 approval/promote 流程决定。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_injects_candidate_skill_into_child_agent_sandbox -q
1 failed  # 实现前红灯：child_agent/.mewcode/skills/<skill>/SKILL.md 不存在

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_injects_candidate_skill_into_child_agent_sandbox -q
1 passed
```

## Approval Request Canary 摘要

approval request 的 Markdown 现在会在 `Candidate Diff` 之前展示 canary 执行摘要，方便审批人先判断候选 skill 是否已经完成隔离多轮任务验证。

新增区块：

```text
## Canary Execution Summary

- Runner: `fork_agent_sandbox_deterministic`
- Rounds: `3/3` passed
- Mode: `candidate_canary`
- Canary skills injected: `3`
- First canary skill: `<child_agent/.mewcode/skills/<skill>/SKILL.md>`
```

关键边界：

- 摘要来自已生成的 execution eval JSON，不会重新运行 eval。
- 摘要缺失不会绕过原有 gate；`submit_skill_approval_request()` 仍要求 execution eval passed。
- 审批详情仍保留完整 `Execution Eval Report`，canary 摘要只是把最关键执行证据前置。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_canary_execution_summary -q
1 failed  # 实现前红灯：审批 Markdown 缺少 Canary Execution Summary

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_canary_execution_summary -q
1 passed
```

## Canary Failure 审批阻断

候选 skill 的 canary execution eval 如果失败，系统现在会把阻断原因写入 candidate manifest，而不是只返回一个通用错误。

新增 manifest 字段：

```json
{
  "approval_status": "blocked",
  "approval_blocked_reason": "canary execution eval failed: 0/1 rounds passed (runner=fork_agent_sandbox_deterministic)",
  "approval_blocked_at": 178...
}
```

行为边界：

- `submit_skill_approval_request()` 不会为失败 canary 生成 approval request。
- `manual`、`deferred` 和 `trusted-auto` 都共享同一个 execution eval gate。
- 后续如果候选 skill 修复并重新通过 execution eval，生成 pending request 时会清空 blocked reason。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_blocks_failed_canary_execution_eval -q
1 failed  # 实现前红灯：只抛通用 execution eval 错误，manifest 没有 blocked reason

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_blocks_failed_canary_execution_eval -q
1 passed
```

## Fork Reviewer Generated Canary 摘要

fork reviewer report 现在会集中展示 auto review 本轮生成 candidate 后的 execution eval canary 摘要。审批人或调试者不需要分别打开每个 `eval_report.json`，即可先看到 generated candidate 是否完成多轮 canary 执行。

新增区块示例：

```text
## Generated Execution Evals

- proposal=`prop_xxx` skill=`review-loop` ok=`True` runner=`fork_agent_sandbox_deterministic` rounds=`3/3` canary_mode=`candidate_canary` canary_injections=`3`
```

关键字段：

- `runner`：execution eval runner，例如 `fork_agent_sandbox_deterministic`。
- `rounds`：通过轮次摘要，例如 `3/3`。
- `canary_mode`：当前固定为 `candidate_canary`。
- `canary_injections`：候选 skill 被注入 child agent 沙盒的轮次数。

行为边界：

- fork reviewer 只读取已生成的 execution eval JSON，不重新执行 candidate。
- 该摘要只增强审计可见性，不改变 approval request、trusted-auto promote 或 rollback gate。
- 如果 execution eval report 缺失或损坏，摘要字段为空，不会伪造通过结果。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_report_includes_generated_canary_summary -q
1 failed  # 实现前红灯：fork reviewer report 缺少 Generated Execution Evals

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_report_includes_generated_canary_summary -q
1 passed
```

## Blocked Generated Candidates

fork reviewer report 现在会把 generated candidate 的 canary 失败单独展示为 `Blocked Generated Candidates`。这类候选不会进入 approval request，也不会被 `trusted-auto` 自动应用。

新增区块示例：

```text
## Blocked Generated Candidates

- proposal=`prop_xxx` skill=`failing-generated-loop` blocked=`True` runner=`fork_agent_sandbox_deterministic` rounds=`0/3` reason=generated candidate canary failed: 0/3 rounds passed (...)
```

行为边界：

- blocked 只针对 generated execution eval 中 `ok=False` 的候选。
- 系统会同步写入 candidate manifest：`approval_status=blocked` 和 `approval_blocked_reason`。
- blocked candidate 不会进入 manual/deferred 审批队列，也不会触发 trusted-auto promote。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_blocks_failed_generated_candidate_in_report -q
1 failed  # 实现前红灯：缺少 _block_failed_generated_candidates

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_blocks_failed_generated_candidate_in_report -q
1 passed
```

## Blocked Candidate Notification

`format_review_notification()` 现在除了 pending approval request，也会提示 blocked generated candidates。这样当 auto review 没有可审批项，但有候选 skill 因 canary 失败被阻断时，用户仍能在界面消息中看到原因。

通知示例：

```text
Self-evolution blocked generated candidate(s):
- prop_blocked / failing-generated-loop reason=generated candidate canary failed: 0/3 rounds passed
```

行为边界：

- 通知只展示摘要，不会生成 approval request。
- 如果同一轮同时存在 pending request 和 blocked candidate，通知会分两个段落展示。
- 没有 request 且没有 blocked candidate 时，仍返回空字符串。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_blocked_generated_candidates -q
1 failed  # 实现前红灯：blocked generated candidates 时 notification 为空

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_blocked_generated_candidates -q
1 passed
```

## Self-Evolution Inbox 分类

`EvolutionEngine.list_self_evolution_inbox()` 现在提供一个只读分类视图，供 TUI 和后续 review surface 使用。

返回结构：

```json
{
  "pending_requests": [{"request_id": "approval_xxx", "proposal_id": "prop_xxx"}],
  "blocked_candidates": [{"proposal_id": "prop_blocked", "approval_status": "blocked"}],
  "generated_candidates": [{"proposal_id": "prop_generated", "approval_status": ""}],
  "counts": {
    "pending_requests": 1,
    "blocked_candidates": 1,
    "generated_candidates": 1
  }
}
```

TUI fallback 行为：

- 有 pending request：继续打开第一个 approval widget。
- 无 pending 但有 blocked candidate：展示 blocked generated candidate 摘要。
- 只有 generated candidate：暂不弹审批，也不自动应用。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_list_self_evolution_inbox_groups_pending_blocked_and_generated -q
1 failed  # 实现前红灯：EvolutionEngine 缺少 list_self_evolution_inbox()

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_list_self_evolution_inbox_groups_pending_blocked_and_generated -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate -q
1 failed  # 实现前红灯：TUI fallback 未读取 blocked inbox

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate -q
1 passed
```

## Generated Candidate TUI 提示

TUI fallback 现在会在没有 pending approval request 时读取 `generated_candidates`，并展示只读摘要。该摘要用于提示“已有候选 skill 进入生成态，但尚未完成 eval/approval gate”，不会创建 approval request，也不会自动 approve/promote。

展示字段：

- candidate 数量。
- proposal id。
- skill 名称。
- deterministic eval 状态。
- execution eval 状态。

行为边界：

- 有 pending request：继续优先打开 approval widget。
- 有 blocked candidate：继续展示阻断原因。
- 只有 generated candidate：展示 generated 摘要，不弹审批、不应用。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
1 failed  # 实现前红灯：TUI fallback 忽略 generated_candidates

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
1 passed
```

## TUI Fallback 使用 Markdown Inbox

TUI self-evolution fallback 现在使用 `render_self_evolution_inbox()` 的 Markdown 输出。没有 pending approval request 时，界面不再只显示 blocked/generated 短摘要，而是展示完整 inbox 列表。

显示内容：

- `# Self-Evolution Inbox` 标题。
- `Pending Approval Requests` 分组。
- `Blocked Generated Candidates` 分组。
- `Generated Candidates` 分组。
- review run 和 report path。

行为边界：

- 有 pending request：仍优先打开 approval widget。
- 无 pending request：显示 Markdown inbox。
- Markdown inbox 只读，不改变审批和应用逻辑。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
2 failed  # 实现前红灯：TUI fallback 仍是短摘要

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
2 passed
```

## Markdown Self-Evolution Inbox

`EvolutionEngine.render_self_evolution_inbox()` 现在可以把 self-evolution inbox 渲染为 Markdown。它集中展示 pending approval request、blocked generated candidate 和 generated candidate 三类内容，作为后续 TUI/API 列表视图的只读输出基础。

展示内容：

- pending approval request：request id、proposal id、skill、审批模式、状态和 eval report。
- blocked generated candidate：proposal id、skill、阻断原因、review run 和 report path。
- generated candidate：proposal id、skill、eval/execution 状态、review run 和 report path。

行为边界：

- 只读渲染，不创建或修改任何 request/candidate/skill。
- 空分组显示 `None`。
- 该能力只是列表输出基础，不代表新增自动审批入口。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_self_evolution_inbox_summarizes_all_candidate_groups -q
1 failed  # 实现前红灯：缺少 Markdown inbox 渲染函数

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_self_evolution_inbox_summarizes_all_candidate_groups -q
1 passed
```

## Review Report 路径展示

TUI 的 blocked/generated candidate 摘要现在会同时显示来源 report path。此前只显示 `review=<run_id>`，用户还需要再查 review run 才能找到报告文件。现在摘要会补充 `report=<path>`，直接指向 fork reviewer report。

展示形式：

```text
- prop_xxx / generated-review-loop eval=pending execution=pending review=review_xxx report=.mewcode/evolution/review_runs/review_xxx/report.md
```

行为边界：

- 有 report path：显示 `report=<path>`。
- 无 report path：保持原摘要。
- 该字段只用于定位报告，不读取、不修改、不自动打开报告。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
2 failed  # 实现前红灯：TUI 摘要没有 report path

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
2 passed
```

## Blocked Candidate 来源 Review Run

TUI blocked generated candidate 摘要现在也会显示来源 review run id。这个字段用于回答“这个候选 skill 是哪次自动复盘阻断的”，方便从 blocked 提示反查 fork reviewer 报告和 canary 失败证据。

展示形式：

```text
- prop_xxx / blocked-review-loop reason=... review=review_xxx
```

行为边界：

- 有来源 run：显示 `review=<run_id>`。
- 无来源 run：保持原 blocked 摘要。
- 该字段只用于审计追踪，不影响阻断、审批和自动应用。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate -q
1 failed  # 实现前红灯：blocked candidate 摘要没有 review run id

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate -q
1 passed
```

## Trusted-Auto Rollback Cursor

trusted-auto rollback 现在优先使用 approval request 中的 `usage_baseline_count` 判断“审批后新增负反馈”。该值在 approval resolve 成功时写入，表示当时 usage log 的记录数量。后续 rollback 只检查该 cursor 之后追加的 usage 记录，避免系统时间回拨、时间戳粒度不足或测试环境时间乱序导致 approval 前负反馈被误算。

兼容策略：

- 新 approval request：使用 `usage_baseline_count` 按 JSONL 追加顺序截断。
- 旧 approval request：字段缺失时继续使用 `usage.created_at > approval.resolved_at`。
- rollback threshold 和 rollback events 语义不变，只改变“审批后 usage”的判定方式。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_rollback_uses_usage_cursor -q
1 failed  # 实现前红灯：时间戳乱序导致误 quarantine

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_rollback_uses_usage_cursor -q
1 passed
```

## Trusted-Auto Rollback Guard 展示

approval request 详情现在会在 trusted-auto 自动批准后展示 rollback guard。这个段落不是新的审批条件，只是把系统已经记录的 `usage_baseline_count` 展示给用户，方便判断后续 quarantine 是否只基于审批后的新失败。

展示内容：

- `Usage baseline count`：自动批准时 usage log 的记录数。
- `Post-approval usage source`：后续 usage 以 JSONL 追加游标为准。
- `Timestamp fallback`：有 cursor 的 request 不再使用时间戳兜底。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_trusted_auto_rollback_guard -q
1 failed  # 实现前红灯：审批详情没有 rollback guard

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_trusted_auto_rollback_guard -q
1 passed
```

## Generated Candidate 来源 Review Run

TUI generated candidate 摘要现在会显示来源 review run id。这个字段用于回答“这个候选 skill 是哪次自动复盘生成的”，方便后续打开 fork reviewer 报告查证生成原因、eval 状态和阻断/审批路径。

展示形式：

```text
- prop_xxx / generated-review-loop eval=pending execution=pending review=review_xxx
```

行为边界：

- 有来源 run：显示 `review=<run_id>`。
- 无来源 run：保持原摘要，不伪造来源。
- 该字段只用于审计追踪，不影响审批和自动应用。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
1 failed  # 实现前红灯：generated candidate 摘要没有 review run id

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
1 passed
```

## 清理旧 TUI 短摘要 Formatter

TUI self-evolution fallback 现在只使用 `EvolutionEngine.render_self_evolution_inbox()` 的 Markdown 输出。旧的 `MewCodeApp._format_self_evolution_inbox_message()` 和 `_format_self_evolution_source_part()` 已删除，避免 blocked/generated 候选出现两套不同展示逻辑。

行为边界：

- pending request 仍优先打开 approval widget。
- 没有 pending request 时仍展示 `# Self-Evolution Inbox`。
- blocked/generated 候选的 review run 和 report path 仍由 engine 层 Markdown 渲染。
- 本次不改变审批、promote、rollback、quarantine 或 trusted-auto 条件。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_drops_legacy_short_summary_formatter -q
1 failed  # 实现前红灯：旧 formatter 仍存在

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_drops_legacy_short_summary_formatter -q
1 passed
```

## Review Run 报告只读读取接口

`EvolutionEngine.read_self_evolution_review_report(review_run_id)` 现在可以按 review run id 读取 fork reviewer 的 Markdown report。它是后续 TUI inbox 详情入口的基础能力，不会创建 approval request，也不会 approve、promote 或 quarantine。

行为边界：

- 正常 report 路径：读取 `.mewcode/evolution/review_runs/<review_id>/report.md`。
- 未知 review run：返回失败消息。
- report 缺失：返回失败消息。
- artifact path 指向 review_runs 外部：拒绝读取。

安全策略：

- report path 必须是相对路径。
- 解析后的路径必须留在 `.mewcode/evolution/review_runs/` 下。
- 文件名必须是 `report.md`。
- 拒绝读取项目其它文件，避免污染的 artifact path 泄露内容。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_read_self_evolution_review_report_returns_markdown tests/test_evolution.py::TestEvolutionEngine::test_read_self_evolution_review_report_rejects_escaped_report_path -q
2 failed  # 实现前红灯：engine 缺少 review report 读取接口

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_read_self_evolution_review_report_returns_markdown tests/test_evolution.py::TestEvolutionEngine::test_read_self_evolution_review_report_rejects_escaped_report_path -q
2 passed
```

## TUI Inbox 内联 Review Report 详情

TUI self-evolution fallback 现在会在单个可追踪 review run 的场景下内联展示 report 内容。这个能力只用于让用户直接看到 fork reviewer 的测试与复盘证据，不会创建 approval request，也不会 approve、promote、rollback 或 quarantine。

展示规则：

- inbox 中只有一个 review run id：追加 `## Review Report Details` 并展示 report Markdown。
- inbox 中没有 review run id：只展示 inbox。
- inbox 中有多个不同 review run id：只展示 inbox，不自动展开多个报告。
- report 读取失败：展示安全错误，不泄露文件内容。

安全策略：

- TUI 不直接拼路径读文件，统一调用 `EvolutionEngine.read_self_evolution_review_report()`。
- report path 仍必须留在 `.mewcode/evolution/review_runs/` 下。
- 污染的 artifact path，例如 `README.md`，只会展示拒绝读取的错误。
- 本次不新增用户命令，也不改变用户审批模式。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_inlines_single_review_report tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_rejects_escaped_path -q
2 failed  # 实现前红灯：TUI 没有内联 Review Report Details

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_inlines_single_review_report tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_rejects_escaped_path -q
2 passed
```

## 多 Review Run 报告省略提示

TUI self-evolution fallback 现在在多个 review run 同时出现时，会展示 `## Review Report Details`，但不会自动读取和展开多份 report。界面只列出相关 review run id，并说明报告内容因 `multiple review runs` 已省略。

展示规则：

- 一个 review run：读取并展示该 run 的 report Markdown。
- 多个 review run：只列出 id，不读取 report 内容。
- 没有 review run：不展示 report detail 区块。

安全策略：

- 避免多个候选时一次性刷出多份评测报告。
- 避免 UI 消息过长影响用户判断。
- 保持审批、promote、rollback、quarantine 和 trusted-auto 条件不变。
- 不新增用户命令。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_omits_multiple_review_reports -q
1 failed  # 实现前红灯：多 review run 时没有省略提示

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_omits_multiple_review_reports -q
1 passed
```

## 缺失 Review Report 的 UI 脱敏提示

TUI self-evolution fallback 现在会把缺失 report 的底层错误脱敏后展示。底层 `EvolutionEngine.read_self_evolution_review_report()` 仍保留完整诊断；TUI 只展示用户需要的 review run id，避免把本机临时目录路径暴露到界面。

展示规则：

- report 存在：展示 report Markdown。
- report 缺失：展示 `report missing for <review_run_id>`。
- report path 逃逸：继续展示安全拒绝原因。
- 多个 review run：仍只列 id，不读取 report 内容。

安全策略：

- 脱敏只发生在 TUI 展示层。
- 不修改 report 读取的路径校验。
- 不改变 approval request、promote、rollback、quarantine 或 trusted-auto 行为。
- 不新增用户命令。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_sanitizes_missing_report -q
1 failed  # 实现前红灯：UI 泄露缺失 report 的绝对路径

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_sanitizes_missing_report -q
1 passed
```

## 未知 Review Run 的 UI 脱敏提示

TUI self-evolution report detail 现在会把未知 review run 的底层错误脱敏成稳定的用户提示。这个能力用于未来可选择 inbox/detail 入口，防止过期或损坏的 `review_run_id` 直接暴露底层 store 查询文案。

展示规则：

- review run 存在且 report 存在：展示 report Markdown。
- review run 存在但 report 缺失：展示 `report missing for <review_run_id>`。
- review run 不存在：展示 `review run unavailable: <review_run_id>`。
- 多个 review run：仍只列 id，不读取 report 内容。

安全策略：

- 脱敏只发生在 TUI 展示层。
- 不修改 engine 的查询和路径校验。
- 不改变 approval request、promote、rollback、quarantine 或 trusted-auto 行为。
- 不新增用户命令。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_sanitizes_unknown_run -q
1 failed  # 实现前红灯：UI 直接展示底层 unknown run 文案

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_sanitizes_unknown_run -q
1 passed
```

## Review Report 错误脱敏 Helper

TUI self-evolution report detail 的错误脱敏规则现在集中在 `_sanitize_self_evolution_review_report_error()`。此前缺失 report、未知 run、普通安全错误的处理分散在 Markdown 拼接逻辑里；现在 helper 负责把底层错误转成用户可读文案，展示函数只负责渲染。

覆盖规则：

- `review report not found for ...` -> `report missing for <review_run_id>`。
- `review run ... not found` -> `review run unavailable: <review_run_id>`。
- 其它错误保持原文，例如路径逃逸的安全拒绝原因。

安全策略：

- 只重构 TUI 展示层。
- 不修改 engine 的查询、读取或路径校验。
- 不改变 approval request、promote、rollback、quarantine 或 trusted-auto 行为。
- 不新增用户命令。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_error_sanitizer -q
1 failed  # 实现前红灯：helper 不存在

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_error_sanitizer -q
1 passed
```

## Self-Evolution Inbox 可选择 Widget 基础

`InlineSelfEvolutionInboxWidget` 现在提供 self-evolution inbox 的可选择 TUI 基础组件。它可以显示 inbox Markdown，并提供 `View report details` 与 `Dismiss inbox` 两个动作。当前 app fallback 仍沿用系统消息展示；该 widget 是下一步替换系统消息路径的基础。

行为边界：

- widget 只接收已准备好的 Markdown 字符串。
- widget 不读取 report 文件。
- widget 不创建 approval request。
- widget 不 approve、promote、rollback 或 quarantine。

安全策略：

- report detail 仍由 engine/app 现有安全读取与脱敏路径准备。
- widget 的 `VIEW_REPORT` 只返回传入的 report Markdown。
- widget 的 `DISMISS` 只关闭/忽略 inbox，后续接入 app 时不应改变候选状态。

验证记录：

```text
PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_self_evolution_inbox_widget_shows_inbox_and_actions tests/test_self_evolution_dialog.py::test_self_evolution_inbox_widget_emits_view_report_and_dismiss -q
1 error  # 实现前红灯：widget 尚不存在

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_self_evolution_inbox_widget_shows_inbox_and_actions tests/test_self_evolution_dialog.py::test_self_evolution_inbox_widget_emits_view_report_and_dismiss -q
2 passed
```

## TUI Fallback 接入可选择 Inbox Widget

self-evolution TUI fallback 现在使用 `InlineSelfEvolutionInboxWidget` 展示 inbox。没有 pending approval request 时，用户先看到 inbox 列表；report detail 不再自动展开，而是通过 `View report details` 按需查看。

行为边界：

- pending approval request 仍优先打开 approval widget。
- 没有 pending request 时打开 inbox widget。
- `View report details` 只显示传入的 report Markdown。
- `Dismiss inbox` 只关闭/忽略当前展示，不修改候选状态。

安全策略：

- 不创建 approval request。
- 不 approve、reject、promote、rollback 或 quarantine。
- 不新增用户命令。
- report 仍由 engine/app 读取与脱敏后再传入 widget。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_inlines_single_review_report -q
2 failed  # 实现前红灯：app 仍使用系统消息展示 inbox

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_inlines_single_review_report -q
2 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_response_views_report_or_dismisses -q
1 failed  # 实现前红灯：缺少 VIEW_REPORT/DISMISS 响应处理器

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_response_views_report_or_dismisses -q
1 passed
```

## Self-Evolution Inbox 去重展示

self-evolution TUI fallback 现在会避免重复展示同一个待处理 inbox。如果同一份 `inbox_markdown` 和 `report_detail_markdown` 已经挂载且用户还没有响应，后续 review tick 不会再次插入相同 widget。用户选择 `View report details` 或 `Dismiss inbox` 后，pending key 会被清理，后续可以再次展示。

行为边界：

- 只去重同内容的 pending inbox。
- 新内容仍可展示。
- 用户响应后允许再次展示。
- `Dismiss inbox` 不改变候选状态。

安全策略：

- 不 approve、reject、promote、rollback 或 quarantine。
- 不新增用户命令。
- 不改变 engine 存储和 review run 记录。
- 只影响 TUI 展示层，避免重复刷屏。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_deduplicates_pending_display -q
1 failed  # 实现前红灯：同一 inbox 会重复挂载

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_deduplicates_pending_display -q
1 passed
```

## Self-Evolution Inbox 挂载失败恢复

inbox 去重依赖 pending key。为了避免一次 TUI 挂载异常把同一个 inbox 永久卡住，`_show_self_evolution_inbox()` 现在通过 guarded coroutine 挂载 widget；如果挂载失败，会在 key 仍匹配时清理 pending 状态，后续 review tick 可以重新展示。

行为边界：

- 挂载成功时 pending key 继续保留，直到用户响应。
- 挂载失败时只清理当前匹配的 pending key。
- 后续相同 inbox 可以重新尝试展示。

安全策略：

- 不改变 approval、promote、rollback、quarantine 或 trusted-auto 逻辑。
- 不修改 candidate 或 review run 存储。
- 只恢复 TUI 展示层状态。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_clears_pending_when_mount_fails -q
1 failed  # 实现前红灯：挂载失败后 pending key 没清理

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_clears_pending_when_mount_fails -q
1 passed
```

## Self-Evolution Inbox 输入框状态测试

本次补充 TUI 挂载层回归测试，验证 `InlineSelfEvolutionInboxWidget` 被挂载时会禁用 `#chat-input`，用户响应后会移除 inline widget、恢复输入框并重新 focus。

行为边界：

- inbox 展示期间输入框禁用。
- 用户 `View report details` 或 `Dismiss inbox` 后输入框恢复。
- 测试使用 fake chat/input/inline widget，不启动完整 TUI。

安全策略：

- 不改变 approval、promote、rollback、quarantine 或 trusted-auto 逻辑。
- 不修改 candidate 或 review run 存储。
- 不新增用户命令。
- 本次只补测试和文档，不修改生产代码。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_mount_disables_and_restores_input -q
1 passed
```

## Self-Evolution TUI 测试 App 构造 Helper

本次清理 `tests/test_evolution.py` 中 self-evolution TUI fallback 测试的重复 app 构造代码，新增 `_make_test_mewcode_app(**kwargs)` 统一创建测试用 `MewCodeApp`。需要开启 manual approval 的测试继续通过 `self_evolution_config` 参数传入。

行为边界：

- 只改测试结构。
- 运行时 self-evolution 行为不变。
- 直接测试 `MewCodeApp` 静态 helper 的用例仍保留直接导入。

安全策略：

- 不改变 approval、promote、rollback、quarantine 或 trusted-auto 逻辑。
- 不修改 candidate 或 review run 存储。
- 不新增用户命令。
- 不修改生产代码。

验证记录：

```text
PYTHONPATH=. pytest tests/test_self_evolution_dialog.py tests/test_evolution.py -q
110 passed  # 重构前基线

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_approval_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_mount_disables_and_restores_input tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills -q
4 passed
```

## Self-Evolution Inbox 单 Pending 限制

self-evolution TUI fallback 现在同一时间只允许一个 inbox widget 处于 pending 状态。此前只去重相同内容；现在只要 `_pending_self_evolution_inbox_key` 非空，新 inbox 无论内容是否相同都不会继续挂载，直到用户响应或挂载失败恢复 pending 状态。

行为边界：

- 一个 pending inbox 未处理前，不再挂载第二个 inbox。
- 用户响应后允许展示后续 inbox。
- 挂载失败仍会清理 pending 状态，允许重试。

安全策略：

- 不改变 approval、promote、rollback、quarantine 或 trusted-auto 逻辑。
- 不修改 candidate 或 review run 存储。
- 不新增用户命令。
- 只串行化 TUI inbox 展示。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_allows_only_one_pending_widget -q
1 failed  # 实现前红灯：不同内容 inbox 会继续挂载

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_allows_only_one_pending_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_deduplicates_pending_display tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_clears_pending_when_mount_fails -q
3 passed
```

## Self-Evolution Inbox 无报告详情动作隐藏

本次补充 inbox widget 的 no-report-detail 回归测试。`report_detail_markdown` 为空或只有空白字符时，widget 不显示 `View report details`，只保留 `Dismiss inbox`，选择后不会返回 report Markdown。

行为边界：

- 空白 report detail 不产生查看报告动作。
- `Dismiss inbox` 仍可正常选择。
- 选择后事件为 `DISMISS`，`report_markdown` 为空。

安全策略：

- 不改变 approval、promote、rollback、quarantine 或 trusted-auto 逻辑。
- 不修改 candidate 或 review run 存储。
- 不新增用户命令。
- 不修改生产代码，只补测试覆盖。

验证记录：

```text
PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_self_evolution_inbox_widget_hides_report_action_without_detail -q
1 passed
```

## Self-Evolution Approval 优先清理 Pending Inbox

approval request 的优先级高于 inbox。现在 `_show_self_evolution_approval()` 会在挂载 approval widget 前调用 `_clear_pending_self_evolution_inbox()`，移除旧 inbox widget 并清空 `_pending_self_evolution_inbox_key`，避免同一时间出现两个 self-evolution 交互入口。

行为边界：

- approval 展示前会清理 pending inbox。
- inbox 响应处理也复用同一个清理 helper。
- approval 挂载和 request 状态仍按原逻辑执行。

安全策略：

- 不改变 approval、promote、rollback、quarantine 或 trusted-auto 逻辑。
- 不修改 candidate 或 review run 存储。
- 不新增用户命令。
- 只调整 TUI 展示优先级和 pending 清理。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox -q
1 failed  # 实现前红灯：approval 出现时旧 inbox 未移除

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_response_views_report_or_dismisses tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_allows_only_one_pending_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills -q
4 passed
```

## Self-Evolution 重复 Approval 仍清理 Pending Inbox

本次补充 duplicate approval path 的回归测试。同一个 approval request 已经 pending 时，`_show_self_evolution_approval()` 不会重复挂载 approval widget，但仍必须清理残留 pending inbox。当前实现已满足：`_clear_pending_self_evolution_inbox()` 在 duplicate request 检查之前执行。

行为边界：

- 同一个 approval request 已 pending 时不重复挂载 approval widget。
- duplicate path 仍会移除旧 inbox 并清空 pending inbox key。
- approval request 状态不变。

安全策略：

- 不改变 approval、promote、rollback、quarantine 或 trusted-auto 逻辑。
- 不修改 candidate 或 review run 存储。
- 不新增用户命令。
- 不修改生产代码，只补测试覆盖。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_duplicate_self_evolution_approval_still_clears_pending_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox -q
2 passed
```

## Self-Evolution TUI Fake Fixture 抽取

本次清理 self-evolution TUI 状态测试样板，新增三个测试 helper：`_capture_self_evolution_inbox_mount(app)`、`_capture_self_evolution_approval_mount(app)` 和 `_install_fake_pending_inbox_query(app)`。

行为边界：

- 只改测试结构。
- inbox/approval mount 捕获语义不变。
- pending inbox remove 语义不变。

安全策略：

- 不修改生产代码。
- 不改变 approval、promote、rollback、quarantine 或 trusted-auto 逻辑。
- 不修改 candidate 或 review run 存储。
- 不新增用户命令。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_deduplicates_pending_display tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_allows_only_one_pending_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_duplicate_self_evolution_approval_still_clears_pending_inbox -q
4 passed
```

## Self-Evolution Fork Reviewer Busy Gate

本次给 self-evolution review pass 增加 running reviewer 并发保护。若 `.mewcode/evolution/review_runs.jsonl` 中已有 `mode=fork_reviewer` 且 `status=running` 的 run，新的 `review_ready_skill_candidates()` 直接返回 `status=busy`，并携带 `active_review_run_id` 与 `active_review_report`，不再创建第二个 review run。

审批影响：

- 不会因为多入口触发而重复生成同一批候选 skill。
- 不会因为并发 review 而重复提交 approval request。
- `manual`、`deferred`、`trusted-auto` 的审批语义不变；只是进入审批前多了一道“同一时间只允许一个 fork reviewer”保护。

安全策略：

- `busy` 是 transient review result，不代表候选 skill 被批准或拒绝。
- 运行中的 fork reviewer 仍不能 approve 或 promote。
- 不新增用户命令；用户只会看到当前 review 正在运行的提示。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_skips_when_fork_reviewer_is_running -q
1 failed  # 实现前红灯：已有 running run 时仍返回 idle 并启动新 run

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_skips_when_fork_reviewer_is_running tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_fork_reviewer_run -q
2 passed
```

## Self-Evolution Busy 提示可见化

本次把 `busy` 状态接到 CLI/TUI 通知层。`format_review_notification()` 现在会为 `busy` 结果生成明确文案，包含 active review run id 和 report 路径；TUI 在渲染 inbox 前优先处理 `busy`，避免展示空 inbox。

审批影响：

- 用户能知道“这次没有新审批”是因为已有 fork reviewer 正在运行，而不是系统没反应。
- pending approval request 仍保持更高优先级；已有审批不会被 busy 文案覆盖。
- 空 inbox 不再遮挡 busy 状态。

安全策略：

- 不改变 approval request、approve、reject、promote、rollback 或 quarantine 行为。
- 不改变候选 skill 的评测门槛。
- 只改变 busy 状态的用户可见反馈。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_busy_review_run -q
1 failed  # 实现前红灯：busy formatter 返回空字符串

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_busy_message -q
1 failed  # 实现前红灯：TUI 挂载空 inbox，没有显示 busy message

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_busy_message tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_busy_review_run tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_skips_when_fork_reviewer_is_running -q
4 passed
```

## Self-Evolution Stale Reviewer 恢复

本次补 active fork reviewer 的异常恢复。若旧 review run 一直停留在 `running` 且超过默认 1 小时，新的 review pass 会先把旧 run 标记为 `failed`，错误原因写入 `stale fork reviewer lock expired...`，然后正常启动新的 fork reviewer。

审批影响：

- stale 恢复不会批准候选 skill。
- stale 恢复不会推广 `.mewcode/skills/**/SKILL.md`。
- stale 恢复只解除旧 `running` run 对新 review pass 的阻塞。

安全策略：

- 新鲜 `running` run 仍然返回 `busy`，不允许并发 review。
- 过期 run 的失败状态会留在 review run 日志中，便于审计。
- 不改变 `manual`、`deferred`、`trusted-auto` 三种审批模式的语义。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_recovers_stale_running_fork_reviewer -q
1 failed  # 实现前红灯：旧 running run 仍返回 busy，阻止新 review pass

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_recovers_stale_running_fork_reviewer tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_skips_when_fork_reviewer_is_running -q
2 passed
```

## Self-Evolution Stale Recovery 通知

本次把 stale recovery 的结果暴露给上层。`review_ready_skill_candidates()` 现在会返回 `expired_review_run_ids`，`format_review_notification()` 会输出 `Self-evolution recovered stale review run(s): ...`，TUI 会在空 inbox 前优先展示该消息。

审批影响：

- stale recovery 通知只说明旧 review run 已被标记为失败。
- 不会把旧 run 中未完成的候选 skill 直接转成 approval request。
- 不会改变已有 pending approval request 的处理优先级。

安全策略：

- 保留旧 run 的审计记录和失败原因。
- 不改变候选 skill 的 eval/canary/approval gate。
- 不新增用户命令或绕过用户审批。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_recovers_stale_running_fork_reviewer tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_recovered_stale_run -q
2 failed  # 实现前红灯：result 无 expired_review_run_ids，formatter 返回空字符串

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_recovered_stale_message -q
1 failed  # 实现前红灯：TUI 挂载空 inbox，没有显示 stale recovery message

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_recovered_stale_message tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_busy_message tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate -q
3 passed
```

## Self-Evolution 候选 Skill 任务匹配

本次新增只读匹配能力：`EvolutionEngine.match_skill_candidates_for_task(task)` 会扫描仍处于 `proposed` 状态的候选 skill，根据当前任务文本与候选 skill 的 name、description、body 做确定性词面匹配，返回 score、matched_terms、proposal_id、skill_name 和候选评测状态。

审批影响：

- 匹配结果不能直接激活候选 skill。
- 未通过 eval/execution eval/approval 的候选仍不能被 promote。
- matched_terms 用于解释相关性，帮助用户审批时理解“为什么系统认为这个候选相关”。

安全策略：

- 不使用不可复现的模型语义判断。
- 不修改候选 skill、approval request 或 project skill。
- 只读取 proposed skill candidates，不复活 rejected/applied proposal。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_match_skill_candidates_for_task_ranks_relevant_candidate -q
1 failed  # 实现前红灯：EvolutionEngine 没有 match_skill_candidates_for_task API

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_match_skill_candidates_for_task_ranks_relevant_candidate -q
1 passed
```

## Self-Evolution 空 Inbox 抑制

本次修复 TUI 空状态展示。`_run_self_evolution_review()` 现在会在渲染 inbox 前检查 `pending_requests`、`blocked_candidates`、`generated_candidates` 三类计数；全部为 0 时不展示 Self-Evolution Inbox。

审批影响：

- 有 pending approval request 时仍优先打开审批入口。
- 有 blocked/generated candidate 时仍展示 inbox。
- 无待处理项时不再制造一个全是 `None` 的空审批面板。

安全策略：

- 不改变 approval request 的创建、批准、拒绝或推广。
- 不改变 candidate manifest 或 review run 存储。
- 只调整 TUI 空状态显示。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_does_not_show_empty_inbox -q
1 failed  # 实现前红灯：idle 无候选时仍挂载空 inbox

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_does_not_show_empty_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_existing_pending_request -q
4 passed
```

## Self-Evolution 候选匹配提示接入 TUI

本次把候选 skill 匹配报告接入 TUI。普通用户消息进入 Agent 前，若 self-evolution enabled，TUI 会调用 `render_skill_candidate_task_matches()`；有匹配项时展示 Markdown 系统消息，说明匹配到哪些候选 skill、匹配词、分数和 gate 状态。

审批影响：

- 匹配提示不是审批，不会改变 approval request 状态。
- 匹配提示不是启用，不会调用 `LoadSkill`。
- 未通过 eval/execution eval/approval 的候选只能被展示为候选，不能生效。

安全策略：

- TUI 报告明确包含 `Runtime: not auto-activated`。
- 匹配失败只记录 debug log，不影响用户消息继续发送给 Agent。
- 报告只读，不写 candidate manifest、project skill 或 approval log。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status -q
1 failed  # 实现前红灯：缺少 render_skill_candidate_task_matches API

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_shows_self_evolution_candidate_matches -q
1 failed  # 实现前红灯：TUI 不显示候选 skill 匹配报告

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_shows_self_evolution_candidate_matches tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status -q
2 passed
```

## Self-Evolution 匹配报告 Next Action

本次给候选 skill 匹配报告增加 `Next action`。报告会根据候选的 eval、execution eval、approval 状态提示下一步：补 eval、跑 execution eval、处理 pending approval，或查看 blocked 报告。

审批影响：

- `Next action` 不会执行审批动作。
- `Next action` 不会改变 candidate manifest 或 approval request。
- 它只帮助用户判断候选 skill 离可用还差哪一道 gate。

安全策略：

- 未通过 gate 的候选仍显示 `Runtime: not auto-activated`。
- pending approval 仍需要审批处理。
- blocked candidate 不会被重新启用，只提示先检查阻断报告。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status -q
1 failed  # 实现前红灯：报告没有 Next action

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_shows_self_evolution_candidate_matches -q
2 passed
```

## Self-Evolution 匹配报告展示 Approval Request

本次在候选 skill 匹配报告中加入 `Approval request`。当候选已经进入 pending approval，报告会显示对应的 `approval_xxx`，并提示 `Next action: review pending approval request before using this skill`。

审批影响：

- 用户可以从匹配报告直接定位 pending approval request。
- 报告不会自动打开或批准 request。
- 候选仍必须经过用户或配置策略允许的审批流程。

安全策略：

- 只读取 candidate manifest 里已有的 `approval_request_id`。
- 不修改 request 状态。
- 不绕过 `manual`、`deferred`、`trusted-auto` 的原有规则。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_pending_approval_request -q
1 failed  # 实现前红灯：匹配报告没有 approval request id

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_pending_approval_request tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status -q
2 passed
```

## Self-Evolution 候选匹配低信号词降噪

本次降低候选 skill 匹配误报。中文 bigram 会产生大量泛化 token，例如 `测试`、`结果`、`文档`，这些词不足以说明当前任务真的适合某个候选 skill。现在匹配逻辑会过滤低信号中文 token，避免普通任务触发无关候选提示。

审批影响：

- 降噪只影响候选匹配提示是否展示。
- 不影响 pending approval request 的状态。
- 不改变 approve、reject、promote 或 trusted-auto rollback。

安全策略：

- 低信号匹配不会被展示为“推荐候选”。
- 高信号匹配仍显示 gate 状态和 `Runtime: not auto-activated`。
- 不修改 candidate manifest 或 project skill。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_match_skill_candidates_for_task_ignores_generic_chinese_overlap -q
1 failed  # 实现前红灯：泛化中文 token 触发了误匹配

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_match_skill_candidates_for_task_ignores_generic_chinese_overlap tests/test_evolution.py::TestEvolutionEngine::test_match_skill_candidates_for_task_ranks_relevant_candidate tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status -q
3 passed
```

## Self-Evolution TUI 匹配提示 Top 1

本次把普通对话里的候选 skill 匹配提示限制为最高置信 1 个。engine 的匹配 API 仍支持多候选 limit，但 TUI 默认只展示 Top 1，避免多个未审批候选在主对话里制造噪音。

审批影响：

- Top 1 只是展示限制，不代表系统自动选择或启用该候选。
- 被隐藏的候选不会被拒绝或修改。
- pending approval、blocked candidate、generated candidate 的 inbox 逻辑不变。

安全策略：

- 仍显示 `Runtime: not auto-activated`。
- 不调用 `LoadSkill`。
- 不改变 approval request 状态。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_shows_only_top_self_evolution_candidate_match -q
1 failed  # 实现前红灯：TUI 显示了 2 个候选

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_shows_only_top_self_evolution_candidate_match tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_shows_self_evolution_candidate_matches -q
2 passed
```

## Self-Evolution 匹配解释词降噪

本次清理匹配报告中的中文边界噪音词。`Matched terms` 不再展示 `化复`、`盘文` 这类用户无法理解的 bigram，而保留 `复盘` 等有解释力的词。

审批影响：

- 只影响匹配解释和低信号 token 过滤。
- 不影响 approval request 创建或处理。
- 不影响候选 skill 是否能被 promote。

安全策略：

- 报告仍显示 gate 状态和 `Runtime: not auto-activated`。
- 不修改 candidate manifest。
- 不自动启用任何候选。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status -q
1 failed  # 实现前红灯：Matched terms 包含 `盘文`

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status tests/test_evolution.py::TestEvolutionEngine::test_match_skill_candidates_for_task_ignores_generic_chinese_overlap tests/test_evolution.py::TestEvolutionEngine::test_match_skill_candidates_for_task_ranks_relevant_candidate -q
3 passed
```

## Self-Evolution 隐藏候选计数

本次在候选 skill 匹配报告中加入隐藏候选计数。TUI 仍只展示 Top 1，但报告会显示 `Shown matches`、`Total matches`、`Hidden matches`，让用户知道是否还有其他达到阈值的候选被折叠。

审批影响：

- 隐藏候选不会被自动审批。
- 隐藏候选不会被自动启用。
- 计数只用于审计提示，不改变候选状态。

安全策略：

- 主对话只展示 Top 1，降低干扰。
- 报告保留隐藏数量，避免完全不可见。
- 所有候选仍受 eval、execution eval 和 approval gate 约束。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_summarizes_hidden_matches -q
1 failed  # 实现前红灯：报告没有隐藏候选摘要

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_summarizes_hidden_matches tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_shows_only_top_self_evolution_candidate_match tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status -q
3 passed
```

## Self-Evolution 匹配完整审计报告

本次新增完整匹配审计报告 API：`render_skill_candidate_task_match_audit(task)`。它展示所有达到阈值的候选 skill，而不是只展示主对话 Top 1。

审批影响：

- audit report 不会审批候选。
- audit report 不会启用候选。
- audit report 只帮助用户查看被 Top 1 折叠掉的候选详情。

安全策略：

- 每个候选仍显示 eval、execution eval、approval 和 `Runtime: not auto-activated`。
- 不修改 candidate manifest 或 approval request。
- 不改变主对话 Top 1 展示策略。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_match_audit_shows_all_matches -q
1 failed  # 实现前红灯：缺少 render_skill_candidate_task_match_audit API

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_match_audit_shows_all_matches tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_summarizes_hidden_matches -q
2 passed
```

## Self-Evolution 匹配审计接入 TUI

本次新增 `InlineSelfEvolutionMatchWidget`，把候选 skill 匹配报告从普通系统消息升级为 TUI match card。主卡片只展示 Top 1 候选；如果存在隐藏候选，用户可选择 `View all matches` 查看完整匹配审计报告。

审批影响：

- match card 不是审批卡，不会 approve 或 reject。
- `View all matches` 只显示完整 audit，不启用候选。
- pending approval request 仍由原 approval widget 处理。

安全策略：

- 不新增 slash command。
- 不调用 `LoadSkill`。
- 不修改 candidate manifest、approval request 或 project skill。
- audit report 继续显示 `Runtime: not auto-activated`。

验证记录：

```text
PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_self_evolution_match_widget_shows_match_and_audit_action -q
1 error  # 实现前红灯：InlineSelfEvolutionMatchWidget 不存在

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_mounts_self_evolution_match_audit_widget -q
1 failed  # 实现前红灯：TUI 仍用系统消息展示匹配报告，没有挂载 match widget

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_self_evolution_match_widget_shows_match_and_audit_action tests/test_self_evolution_dialog.py::test_self_evolution_match_widget_emits_view_audit_and_dismiss tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_mounts_self_evolution_match_audit_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_views_audit -q
4 passed

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py tests/test_evolution.py -q
133 passed
```

## Self-Evolution 匹配卡片打开待审批请求

本次把候选 skill 匹配提示与审批请求衔接起来：如果 Top 1 匹配候选已经有 pending approval request，match card 会显示 `Open pending approval`，用户选择后直接打开对应审批卡片。

审批影响：

- `Open pending approval` 只是打开原审批详情，不代表 approve。
- 审批动作仍由 approval widget、approval mode 和原 gate 处理。
- 没有 pending approval request 的候选不会出现该操作。

安全策略：

- 不自动 promote project skill。
- 不自动启用候选 skill。
- 不绕过 eval、execution eval、canary、approval gate 或 quarantine 状态。
- match card pending key 纳入 approval request id，避免多个审批请求被错误去重。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_opens_pending_approval -q
1 passed

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_self_evolution_match_widget_shows_pending_approval_action tests/test_self_evolution_dialog.py::test_self_evolution_match_widget_emits_view_audit_and_dismiss tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_match_card_can_open_pending_approval tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_opens_pending_approval -q
4 passed

python3 -m py_compile mewcode/app.py mewcode/self_evolution_dialog.py
passed
```

## Match Card 到 Approval Card 的输入状态边界

本次修正 `Open pending approval` 的 TUI 状态切换：打开待审批请求时不再恢复 chat input，而是直接进入原 approval card 流程。

审批影响：

- 审批模式没有变化，仍支持 `manual`、`deferred`、`trusted-auto`。
- 用户仍必须在 approval card 或对应 approval gate 中完成决策。
- 该修复只避免审批卡片挂载前输入框被短暂启用。

安全策略：

- `OPEN_APPROVAL` 分支不调用 approve/reject/promote。
- 输入框保持禁用，降低待审批状态下继续发起新任务导致 UI 状态交叉的风险。
- `VIEW_AUDIT` 和 `DISMISS` 仍恢复输入框，保持原交互。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_opens_pending_approval -q
1 failed  # 修复前会错误恢复 chat input

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_opens_pending_approval -q
1 passed

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_self_evolution_match_widget_shows_pending_approval_action tests/test_self_evolution_dialog.py::test_self_evolution_match_widget_emits_view_audit_and_dismiss tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_views_audit -q
3 passed
```

## Approval Request 失效时的恢复策略

本次为 `Open pending approval` 增加失败恢复：当 approval request id 已失效或无法渲染时，TUI 会恢复 chat input，并显示失败原因。

审批影响：

- 不改变 `manual`、`deferred`、`trusted-auto` 的审批语义。
- 失效 request 不会被自动重建或自动批准。
- 成功打开审批时仍保持输入框禁用，等待用户或审批策略处理。

安全策略：

- `_show_self_evolution_approval()` 返回 `True/False`，调用方可以区分“审批卡片已打开”和“打开失败”。
- 打开失败只恢复 UI，不修改 manifest、approval store 或 project skill。
- 错误会通过系统消息暴露，避免审批请求丢失时静默失败。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_restores_input_when_approval_missing -q
1 failed  # 修复前输入框无法恢复

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_restores_input_when_approval_missing tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_opens_pending_approval tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_views_audit tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_response_views_report_or_dismisses -q
4 passed

python3 -m py_compile mewcode/app.py
passed
```

## Approval 响应缺少 Active Agent 提示

本次让 `on_inline_skill_approval_widget_responded()` 在没有 active agent 时显示 `Self-evolution approval failed: no active agent.`，避免 approve/reject 响应静默失败。

审批影响：

- 不改变 request 状态。
- 不改变 approval mode。
- 不自动 approve 或 reject。

安全策略：

- 只增加响应阶段的前置条件失败提示。
- 不修改 approval store、candidate manifest、eval report 或 project skill。
- 正常 approve/reject 路径保持原 gate。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_without_agent_is_user_visible -q
1 failed  # 修复前 approval response 无 active agent 静默失败

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_without_agent_is_user_visible tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills -q
2 passed

python3 -m py_compile mewcode/app.py
passed
```

## Approval 缺少 Active Agent 提示

本次让 `_show_self_evolution_approval()` 在没有 active agent 时显示 `Self-evolution approval failed: no active agent.`，避免 approval 打开入口静默失败。

审批影响：

- 不改变 request 状态。
- 不改变 approval mode。
- 不自动处理 approval request。

安全策略：

- 只增加前置条件失败提示。
- 不修改 approval store、candidate manifest、eval report 或 project skill。
- 正常打开、重复 pending 和渲染失败路径保持原有 gate。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_without_agent_is_user_visible -q
1 failed  # 修复前无 active agent 静默失败

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_without_agent_is_user_visible tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_duplicate_self_evolution_approval_still_clears_pending_inbox -q
3 passed

python3 -m py_compile mewcode/app.py
passed
```

## Match Card 挂载失败用户提示

本次让 `_mount_self_evolution_match_guarded()` 在 match card 挂载失败时显示系统消息 `Self-evolution match hint failed to open.`，并继续清理 pending key。

审批影响：

- 不改变 approval mode。
- 不处理或修改任何 approval request。
- 不自动启用候选 skill。

安全策略：

- 只影响 TUI 错误提示和 debug log。
- 不修改 approval store、candidate manifest、eval report 或 project skill。
- pending key 清理保留，允许后续任务匹配重新展示 match card。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_clears_pending_when_mount_fails -q
1 failed  # 修复前 match card 挂载失败静默

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_clears_pending_when_mount_fails tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_match_card_can_open_pending_approval tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_views_audit tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_opens_pending_approval -q
4 passed

python3 -m py_compile mewcode/app.py
passed
```

## Inbox 挂载失败用户提示

本次让 `_mount_self_evolution_inbox_guarded()` 在 inbox widget 挂载失败时显示系统消息 `Self-evolution inbox failed to open.`，并继续清理 pending key。

审批影响：

- 不改变 approval mode。
- 不处理或修改任何 approval request。
- 不影响 candidate gate、eval 或 promote 条件。

安全策略：

- 只影响 TUI 错误提示和 debug log。
- 不修改 approval store、candidate manifest、eval report 或 project skill。
- pending key 清理保留，允许后续重新展示 inbox。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_clears_pending_when_mount_fails -q
1 failed  # 修复前 inbox 挂载失败静默

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_clears_pending_when_mount_fails tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_deduplicates_pending_display tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_allows_only_one_pending_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_mount_disables_and_restores_input -q
4 passed

python3 -m py_compile mewcode/app.py
passed
```

## Approval 渲染失败消息脱敏

本次让 `_show_self_evolution_approval()` 的失败消息复用 `_sanitize_self_evolution_review_error()`。当 approval request 渲染失败原因包含绝对路径时，系统消息会替换为 `<path>`。

审批影响：

- 不改变 request 状态。
- 不改变 approval mode。
- 不自动处理失败 request。

安全策略：

- 只清洗用户可见错误文本。
- 不修改 approval store、candidate manifest、eval report 或 project skill。
- 和 review 执行异常使用同一条脱敏规则，减少遗漏。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_failure_sanitizes_absolute_paths -q
1 failed  # 修复前 approval failure 消息泄露 tmp_path

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_failure_sanitizes_absolute_paths tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_match_response_restores_input_when_approval_missing -q
2 passed

python3 -m py_compile mewcode/app.py
passed
```

## Review 异常消息脱敏

本次新增 `_sanitize_self_evolution_review_error()`，用于清洗 `_run_self_evolution_review()` 的用户可见异常消息。绝对路径会被替换为 `<path>`，避免把本地 workspace 或 `.mewcode/evolution` 目录结构直接展示给用户。

审批影响：

- 不改变审批模式或审批状态。
- 不自动重试失败 review。
- 不影响 debug log 中的原始异常记录。

安全策略：

- 只清洗系统消息文本。
- 不修改 approval store、candidate manifest、eval report 或 project skill。
- 普通非路径错误保持原样，避免丢失必要排查信息。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_failure_is_user_visible tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_failure_sanitizes_absolute_paths tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_error_sanitizer -q
2 failed, 1 passed  # 修复前路径泄露且 helper 不存在

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_failure_is_user_visible tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_failure_sanitizes_absolute_paths tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_error_sanitizer tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_busy_message -q
4 passed

python3 -m py_compile mewcode/app.py
passed
```

## Review Ready 通知展示 Request ID

本次让 `format_review_notification()` 的 ready request 行展示 approval request id，格式变为 `approval_id / proposal_id / skill_name ...`。

审批影响：

- 不改变审批模式或审批状态。
- 不自动处理 request。
- 只让用户更容易定位要处理的 approval request。

安全策略：

- 只增加 request id 文本。
- 不展示 eval report 内容，只保留原有 report path。
- 不修改 candidate manifest、approval store 或 project skill。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_ready_candidates_once tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_falls_back_when_new_approval_open_fails -q
2 failed  # 修复前通知缺少 approval request id

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_ready_candidates_once tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_falls_back_when_new_approval_open_fails tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_approval_widget -q
3 passed

python3 -m py_compile mewcode/evolution/auto_review.py
passed
```

## Review 执行异常用户可见

本次让 `_run_self_evolution_review()` 在 `review_ready_skill_candidates()` 抛异常时显示系统消息，而不是只写 debug log。

审批影响：

- 不改变审批模式或审批状态。
- 不自动重试失败 review。
- 不自动生成、审批或应用候选 skill。

安全策略：

- 只增加错误提示。
- 不修改 approval store、candidate manifest、eval report 或 project skill。
- 后续可继续补充错误消息的路径/敏感信息清洗。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_failure_is_user_visible -q
1 failed  # 修复前 review 崩溃对用户不可见

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_failure_is_user_visible tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_busy_message tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_recovered_stale_message -q
3 passed

python3 -m py_compile mewcode/app.py
passed
```

## Review 流审批打开失败的降级展示

本次让 `_run_self_evolution_review()` 尊重 `_show_self_evolution_approval()` 的 bool 返回值：新审批请求打不开时显示 ready notification；已有 pending request 打不开时继续展示 self-evolution inbox。

审批影响：

- 不改变 approval mode。
- 不自动处理打不开的审批请求。
- 只确保用户仍能看到 pending request 或 inbox 信息。

安全策略：

- fallback 只影响 TUI 展示路径。
- 不修改 approval store、candidate manifest、eval report 或 project skill。
- 失败分支不会绕过 eval、execution eval 或 approval gate。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_falls_back_when_new_approval_open_fails -q
1 failed  # 修复前 ready notification 被吞掉

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_falls_back_to_inbox_when_pending_open_fails -q
1 failed  # 修复前 pending inbox 被提前 return 吞掉

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_approval_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_falls_back_when_new_approval_open_fails tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_existing_pending_request tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_falls_back_to_inbox_when_pending_open_fails tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
6 passed

python3 -m py_compile mewcode/app.py
passed
```

## Approval Card 打开失败用户提示

本次为 approval card 挂载失败增加系统消息，并补上 `mewcode/app.py` 的模块级 logger，避免恢复路径里的 `log.debug(...)` 触发 `NameError`。

审批影响：

- 不改变审批模式或审批结果。
- 不自动处理失败的 approval request。
- 用户看到失败提示后，可以重新打开审批入口。

安全策略：

- UI 打开失败只清理 pending UI 状态。
- 不写 candidate manifest、approval store、eval report 或 project skill。
- 系统消息只暴露 request id，不包含额外敏感内容。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_when_mount_fails -q
1 failed  # 修复前没有用户可见消息，并暴露 log 未定义

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_when_mount_fails tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_duplicate_self_evolution_approval_still_clears_pending_inbox -q
3 passed

python3 -m py_compile mewcode/app.py
passed
```

## 自进化卡片选项渲染重构

本次把 self-evolution 卡片的选项渲染和光标边界逻辑抽成 `_render_choice_lines()` 与 `_move_choice_cursor()`，供 approval、inbox 和 match card 复用。

审批影响：

- 审批模式和审批 gate 没有变化。
- `Approve`、`Reject`、`Open pending approval`、`View all matches`、`Dismiss` 的行为保持不变。
- 该改动只减少 TUI 重复代码，不改变 candidate 状态流转。

安全策略：

- 统一光标 clamp，避免不同卡片在边界行为上出现分叉。
- 统一选项渲染，避免后续新增自进化卡片时遗漏选中态或弱化态展示。
- 不触碰 eval、execution eval、approval store、candidate manifest 或 project skill 文件。

验证记录：

```text
PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_render_choice_lines_marks_selected_option tests/test_self_evolution_dialog.py::test_move_choice_cursor_clamps_to_option_bounds -q
1 error  # 修复前 helper 不存在

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_render_choice_lines_marks_selected_option tests/test_self_evolution_dialog.py::test_move_choice_cursor_clamps_to_option_bounds -q
2 passed

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py -q
10 passed

python3 -m py_compile mewcode/self_evolution_dialog.py
passed
```

## Approval Card 挂载失败恢复

本次为 `_show_self_evolution_approval()` 增加 guarded mount。审批卡片异步挂载失败时，会清理 `_pending_skill_approval_request_id`，避免同一 request 后续无法再次打开。

审批影响：

- 不改变 approval mode。
- 不改变 approve/reject/promote 的条件。
- 挂载失败不会自动处理审批请求，只允许用户后续重新打开。

安全策略：

- pending 清理只发生在 UI mount 失败时。
- approval request、candidate manifest、eval report 和 project skill 不被修改。
- 异常通过 debug log 记录，避免未处理 task exception 干扰主流程。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_when_mount_fails -q
1 failed  # 修复前 pending id 残留

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_when_mount_fails tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_duplicate_self_evolution_approval_still_clears_pending_inbox -q
3 passed

python3 -m py_compile mewcode/app.py
passed
```

## Approval 响应失败消息脱敏

本次为 approval response 的 resolve 失败分支接入路径脱敏。此前打开 approval card 失败会脱敏路径，但用户点击 `Approve` / `Reject` 后如果 engine resolve 失败，错误消息可能原样包含本地工作目录或 `.mewcode/evolution` store 文件路径。

审批影响：

- 不改变 `manual`、`deferred`、`trusted-auto` 三种审批模式。
- 不改变 approve/reject 的状态流转。
- 不改变 skill promote 条件。
- 只影响失败消息展示内容。

安全策略：

- 继续向用户展示失败原因，但隐藏绝对路径。
- 防止 UI 错误提示泄露本机目录结构、临时目录和内部 store 文件位置。
- 不写 candidate manifest、approval store、eval report 或 project skill。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_failure_sanitizes_absolute_paths -q
1 failed  # 修复前 approval response 失败消息原样泄露绝对路径

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_failure_sanitizes_absolute_paths tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_without_agent_is_user_visible -q
3 passed
```

## Approval 响应缺少 Agent 的 Pending 清理

本次补齐 approval response no-agent 分支的 pending 状态清理。该分支已经会给用户提示，但如果 `_pending_skill_approval_request_id` 不清空，后续打开同一个 approval request 会被重复挂载保护误判为已打开。

审批影响：

- 不改变审批模式。
- 不改变 approve/reject 结果。
- 不自动提交或撤回 approval request。
- 只恢复 TUI 内部 pending 状态。

安全策略：

- active agent 缺失时不执行 resolve。
- 不写 approval store、candidate manifest、eval report 或 project skill。
- 清理 pending id 的目的只是让用户后续可以重新打开审批卡片。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_without_agent_is_user_visible -q
1 failed  # 修复前 no-agent 分支留下 stale pending approval id

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_without_agent_is_user_visible tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_failure_sanitizes_absolute_paths tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills -q
3 passed
```

## Approval 打开缺少 Agent 的 Pending 清理

本次补齐 `_show_self_evolution_approval()` no-agent 分支的 pending 状态清理。该入口负责从 review、inbox 或 match 卡片打开审批详情；如果 active agent 缺失且 pending id 残留，后续同一 request 可能无法重新打开。

审批影响：

- 不改变审批模式。
- 不读取、不修改 approval request。
- 不改变 candidate promote 条件。
- 只清理 TUI 内部 pending id。

安全策略：

- active agent 缺失时不创建 engine，不执行 render。
- 不写 approval store、candidate manifest、eval report 或 project skill。
- 只让用户在恢复上下文后可以重新打开审批卡片。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_without_agent_is_user_visible -q
1 failed  # 修复前打开 approval no-agent 分支留下 stale pending approval id

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_without_agent_is_user_visible tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_without_agent_is_user_visible tests/test_evolution.py::TestEvolutionEngine::test_tui_duplicate_self_evolution_approval_still_clears_pending_inbox -q
3 passed
```

## Fork Reviewer 报告展示 Eval Case 覆盖

本次增强 self-evolution approval 的审计材料。fork reviewer 报告新增 `Generated Eval Cases` 小节，列出自动生成候选 skill 时 materialized 的 eval case 数量和 case id。

审批影响：

- 不改变审批模式。
- 不改变 eval、execution eval 或 promote gate。
- 不改变 approval request 状态流转。
- 只让审批人看到更完整的测试覆盖证据。

安全策略：

- 候选 skill 仍必须先通过 deterministic eval 和 execution eval。
- 生成的 eval case 只是报告展示，不会降低通过门槛。
- 用户或 trusted-auto policy 审批前，可以从报告看到 `eval_cases` 数量和执行轮次。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_report_includes_generated_canary_summary -q
1 failed  # 修复前 report 缺少 Generated Eval Cases 和 eval_cases 数量

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_report_includes_generated_canary_summary tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_blocks_failed_generated_candidate_in_report -q
2 passed
```

## Approval 详情展示 Eval Case 覆盖

本次把 eval case 覆盖摘要展示到 approval request 详情页。用户打开 self-evolution approval card 时，可以直接看到候选 skill 的 eval case 数量、execution runner 分布和 case id。

审批影响：

- 不改变审批模式。
- 不改变候选进入审批的条件。
- 不改变 approve/reject/promote 行为。
- 只增强审批材料的可读性和可审计性。

安全策略：

- 详情页复用已有 `_load_eval_cases()`，不会绕过 eval case 校验。
- 候选 skill 仍必须通过 deterministic eval 和 execution eval。
- 审批人能在提交 approve/reject 前看到测试覆盖摘要。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_canary_execution_summary -q
1 failed  # 修复前 approval 详情没有 Eval Cases Summary

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_canary_execution_summary tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_review_materials tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_fork_reviewer_evidence -q
3 passed
```

## Self-Evolution Inbox 展示 Eval 覆盖

本次把 eval coverage 摘要展示到 self-evolution inbox 的 blocked/generated 候选行。用户在进入审批详情前，就能看到候选 skill 的 eval case 数量和 execution eval 轮次。

审批影响：

- 不改变 `manual`、`deferred`、`trusted-auto` 审批模式。
- 不改变候选进入审批、阻断或 promote 的条件。
- 不改变 approval request 状态流转。
- 只增强 inbox 总览的信息透明度。

安全策略：

- 只读取 candidate manifest 中已有的 `eval_case_results` 和 `execution_eval_rounds`。
- 不写 approval store、candidate manifest、eval report 或 project skill。
- 候选 skill 仍必须通过 eval 和 execution eval gate 后才能进入审批。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_list_self_evolution_inbox_groups_pending_blocked_and_generated tests/test_evolution.py::TestEvolutionEngine::test_render_self_evolution_inbox_summarizes_all_candidate_groups -q
2 failed  # 修复前 inbox item 和 markdown 都没有 eval coverage 摘要

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_list_self_evolution_inbox_groups_pending_blocked_and_generated tests/test_evolution.py::TestEvolutionEngine::test_render_self_evolution_inbox_summarizes_all_candidate_groups -q
2 passed
```

## Candidate Match Card 展示 Eval 覆盖

本次把 eval coverage 摘要展示到 candidate match card 和完整 match audit。用户看到候选 skill 与当前任务匹配时，可以同时看到候选的测试覆盖，而不是只看到匹配分数。

审批影响：

- 不改变审批模式。
- 不改变候选匹配评分、排序或过滤逻辑。
- 不改变 approval request 状态流转。
- 不自动启用候选 skill。

安全策略：

- match card 仍是只读提示。
- 候选 skill 仍必须通过 eval、execution eval 和 approval gate。
- 覆盖摘要只读取 candidate manifest 中已有的 eval 结果。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_pending_approval_request -q
1 failed  # 修复前 match card 没有 eval coverage 摘要

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_pending_approval_request tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_summarizes_hidden_matches tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_mounts_self_evolution_match_audit_widget -q
3 passed
```

## Self-Evolution Inbox 展示 Next Action

本次让 blocked/generated candidate 行直接展示下一步动作。它把 approval/eval/execution 状态转换成用户可执行的短文案，减少用户从状态字段反推流程的成本。

审批影响：

- 不改变审批模式。
- 不改变 candidate gate 或 promote gate。
- 不自动执行 eval、approval 或 reject。
- 只增强 inbox 的只读解释层。

安全策略：

- next action 由 manifest 中已有状态派生。
- 不写 approval store、candidate manifest、eval report 或 project skill。
- blocked 候选仍需要人工查看 blocked report 后再决定是否修订。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_self_evolution_inbox_summarizes_all_candidate_groups -q
1 failed  # 修复前 inbox 候选行没有 next_action

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_self_evolution_inbox_summarizes_all_candidate_groups tests/test_evolution.py::TestEvolutionEngine::test_list_self_evolution_inbox_groups_pending_blocked_and_generated -q
2 passed
```

## Fork Reviewer 拆分启动/完成边界

本次新增 `start_fork_reviewer_run()` 与 `complete_fork_reviewer_run()`。它把 fork reviewer 生命周期拆成 running run 创建和 run id 恢复完成两个阶段，为后续真正后台 review 或子 Agent 执行提供边界。

审批影响：

- start 阶段不提交 approval request。
- complete 阶段仍沿用原 review/eval/execution eval/approval gate。
- 不改变 `manual`、`deferred`、`trusted-auto` 审批模式。
- 不改变 trusted-auto 的 same-pass auto-promote 范围。

安全策略：

- running run 仍触发 busy gate，避免并发 review 重复处理同一批候选。
- review run policy 继续记录 `can_approve=False`、`can_promote=False`、`project_write=disabled`。
- complete 阶段优先使用 persisted run 的 approval mode 和 trusted-auto policy，降低配置漂移风险。
- 本次仍不是完整 LLM 子 Agent，只是把同步流程拆出可恢复 API。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_fork_reviewer_run_can_start_and_complete_separately -q
1 failed  # 修复前缺少 start/complete API

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_fork_reviewer_run_can_start_and_complete_separately tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_fork_reviewer_run tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_skips_when_fork_reviewer_is_running tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_recovers_stale_running_fork_reviewer -q
4 passed
```

## Fork Reviewer Task Artifact

本次为 fork reviewer run 增加 `task.md`。它把 review 子任务、输入计数、权限策略和输出要求写成 markdown，方便后续真实 fork 子 Agent 或用户审计读取。

审批影响：

- 不改变审批模式。
- 不改变 start/complete API 的状态流转。
- 不改变 approval request 生成条件。
- 不自动 approve/promote。

安全策略：

- `task.md` 明确展示 `can_approve: false` 和 `can_promote: false`。
- task artifact 与 policy artifact 一起生成，便于审计子 Agent 权限边界。
- 子 Agent 任务说明要求通过 runner 写 output/report，不允许直接编辑 project skill。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_fork_reviewer_run_can_start_and_complete_separately -q
1 failed  # 修复前 fork reviewer run 没有 task artifact

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_fork_reviewer_run_can_start_and_complete_separately tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_fork_reviewer_run tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_trusted_auto_policy_in_run_artifacts -q
3 passed
```

## Started Fork Reviewer 用户通知

本次为 `started` review result 增加用户可见通知。它让后台化 fork reviewer 在启动后能展示 run id、task artifact 和 report artifact，避免用户看到静默状态。

审批影响：

- 不改变审批模式。
- 不改变 start/complete 状态流转。
- 不提交 approval request。
- 不自动 approve/promote。

安全策略：

- 通知只展示项目内相对 artifact 路径。
- 不暴露绝对路径。
- 不改变 review policy 或 candidate gate。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_started_review_run -q
1 failed  # 修复前 started result 没有用户可见消息

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_started_review_run tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_busy_review_run tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_recovered_stale_run tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_ready_candidates_once -q
4 passed
```

## Fork Reviewer Complete 恢复失败通知

本次为 complete 阶段的 `missing` 和 `not-running` 结果增加通知。后台化 review 需要所有生命周期分支都可见，否则恢复失败会被误解为没有触发。

审批影响：

- 不改变审批模式。
- 不改变 approval request 生成条件。
- 不自动重启 review run。
- 不自动 approve/promote。

安全策略：

- run 缺失或非 running 时只提示，不继续执行 review。
- 通知只展示 run id、状态和项目内相对 report 路径。
- 不写 candidate manifest、approval store 或 project skill。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_completion_resume_failure -q
1 failed  # 修复前 missing/not-running 结果没有用户可见消息

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_completion_resume_failure tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_started_review_run tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_busy_review_run -q
3 passed
```

## TUI 后台 Review 与审批闭环

本次把 start/complete 生命周期接入 TUI。Agent 一轮任务结束后，TUI 先创建 `running` review 并立即返回；候选生成和多轮执行评测在后台线程完成。完成后若存在 pending request，TUI 自动打开审批卡；否则展示 blocked/generated inbox 或生命周期错误。

审批影响：

- `manual` 模式下，3/3 execution eval 通过只会创建 pending request，不会更新正式 skill。
- 用户能在审批详情中看到 eval case 数量、执行轮次和 reviewer report，再决定批准或拒绝。
- 只有 `resolve_skill_approval_request(..., approved=True)` 成功后才 promote candidate。
- `deferred` 和 `trusted-auto` 继续使用既有策略，本次没有放宽任何 gate。

安全策略：

- start 阶段只创建 run 和 artifacts，不生成审批、不写正式 skill。
- complete 阶段使用启动时持久化的 review policy；reviewer policy 仍为 `can_approve=false`、`can_promote=false`。
- `missing`、`not-running` 和执行异常会显示给用户，不静默重试或绕过审批。
- 应用退出时取消 TUI 持有的后台任务；磁盘上的 running run 仍可由 stale recovery/complete API 审计和恢复。

端到端证据：

```text
旧 skill + 两条负面使用证据
  -> 后台 fork review started
  -> patch candidate
  -> 3 eval cases
  -> execution eval 3/3 passed
  -> pending approval card（正式 skill 未变化）
  -> 用户批准
  -> 正式 SKILL.md 更新，proposal=applied，request=approved
```

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -k 'self_evolution_review_starts_then_completes_in_background or self_evolution_background_failure_is_user_visible or background_review_completes_full_skill_loop' -q
3 passed

PYTHONPATH=. pytest tests/test_evolution.py -k 'tui_self_evolution_review or tui_self_evolution_background' -q
22 passed

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py tests/test_evolution.py -q
156 passed
```

当前边界：后台生命周期已经完成，但 reviewer 本身仍是确定性 runner，不是拥有独立模型上下文的 Hermes 式 LLM fork Agent。后续接入真实子 Agent 时，审批 gate 和 3 轮执行证据应保持为不可绕过的外层约束。

## 独立模型 Fork Reviewer 审批证据

本次在确定性 complete 和审批卡之间接入专用 `ForkReviewerAgent`。它拥有独立模型上下文，但没有工具，只能对当前 run 的 task/output/report 给出结构化第二意见。

审批影响：

- fork Agent 的 `ready-for-user-review` 不是批准，只表示建议进入现有用户审批流程。
- `needs-revision` 或 `block` 会作为风险意见展示；当前不会由模型直接 reject、approve 或 promote。
- `manual` 模式仍由用户最终决定；`deferred` 和 `trusted-auto` 的原 gate 不变。
- 模型调用失败时使用 deterministic fallback，pending request 不会丢失。

权限与 schema：

- LLM 调用固定 `tools=[]`；tool call 直接判定 reviewer 失败。
- 输出只允许 `schema_version`、`recommendation`、`summary`、`risks`、`evidence`、`recommended_actions`。
- recommendation 只允许 `ready-for-user-review`、`needs-revision`、`block`。
- reviewer system policy 固定 `can_approve=false`、`can_promote=false`、`project_write=disabled`。
- 45 秒超时后回退，避免模型调用无限阻塞后台 review。
- schema version 使用精确整数类型校验，`true` 不会被当作版本 `1`。
- artifact 读取和 agent review 写入都会解析真实路径，拒绝 project root 外路径和 symlink 越界。

审计产物：

- `.mewcode/evolution/review_runs/<run-id>/agent_review.json`
- `.mewcode/evolution/review_runs/<run-id>/agent_review.md`
- 原 `report.md` 追加 `Fork Agent Independent Review`，因此审批详情能同时展示确定性测试证据和模型意见。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -k 'fork_reviewer_agent or persist_fork_reviewer_opinion or persists_fork_agent_opinion_before_approval or fork_agent_failure_falls_back' -q
6 passed

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py tests/test_evolution.py -q
164 passed
```

全仓 `pytest -q` 的已知遗留失败仍是 `test_multi_step_autonomous`（旧 WriteFile 安全假设）和 `test_message_splicing`（旧消息数量断言），均不在本轮审批/fork reviewer 修改范围内。

当前边界：模型 fork Agent 已经真实执行，但仅承担独立复核。候选 skill 正文仍由确定性 usage patch 产生；模型候选生成必须在下一阶段接入 candidate schema、sandbox、三轮 eval 和审批 gate 后才能启用。

模式差异：`manual` 模式会在审批卡打开前展示模型第二意见；`trusted-auto` 继续保持已有 same-pass auto-promote 语义，模型意见此时是补充审计而不是前置批准条件。若后续希望模型 verdict 成为 trusted-auto gate，必须单独设计多评审一致性和误判回滚策略，不能直接把单次模型输出当作授权。

## Fork Skill Proposer 权限边界

日期：2026-08-06

本次新增独立 `ForkSkillProposerAgent`，让模型能够生成受限 Candidate Skill JSON。该 Agent 与主对话隔离，固定不携带工具，也没有文件写入、审批或 promote 能力。

审批链路没有放宽：Proposer 输出只是未落地的候选数据。后续接入主流程时，必须先由 Engine 写入 `.mewcode/evolution/candidates/<proposal-id>/`，依次通过 schema/static policy、eval case、至少三轮 execution eval 和独立 Reviewer；`manual` 模式最终仍由用户批准后才能写入 `.mewcode/skills/`。

当前实现已经验证：隔离单消息上下文、`tools=[]`、严格字段集合、非法名称拒绝、危险下载执行命令拒绝和 45 秒默认超时。目标测试 `tests/test_fork_skill_proposer_agent.py` 为 `4 passed`，编译和 `git diff --check` 通过。当前尚未修改 approval mode 语义，也尚未把模型输出接入自动 candidate 生成。
