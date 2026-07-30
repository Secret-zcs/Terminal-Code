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

## 剩余工作

- 实现自动候选 skill 抽取：由系统读取对话轨迹、工具结果、用户纠正和失败记录生成 proposal。
- 实现审批申请列表与用户可见入口：基于 `render_skill_approval_request()` 展示详情，并调用 `resolve_skill_approval_request()` 处理 approve/reject。
- 实现 `manual` 与 `deferred` 的审批队列差异，但两者都必须保留用户最终审批。
- 补充多轮任务回放评测，确保 candidate skill 在若干轮任务正确执行后才进入审批。
