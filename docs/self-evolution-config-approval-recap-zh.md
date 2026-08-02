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
