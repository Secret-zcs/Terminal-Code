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

## 剩余工作

- 扩展自动候选 skill 抽取：从当前显式 usage log 扩展到对话轨迹、工具结果、用户纠正和失败记录。
- eval case 自动写入后，继续跑 deterministic eval 和 execution eval，再进入 approval request。
- 完善用户可见审批入口：补拒绝路径 UI 测试、manual/deferred 差异化展示和批量审批队列视图。
- 实现 `manual` 与 `deferred` 的审批队列差异，但两者都必须保留用户最终审批。
- 补充多轮任务回放评测，确保 candidate skill 在若干轮任务正确执行后才进入审批。
