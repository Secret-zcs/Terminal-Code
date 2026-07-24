# Verified Skill Evolution 自进化复盘

> 日期：2026-07-18
> 最近更新：2026-07-21
> 目标：把 Hermes 风格“模型生成 skill 后可用”的自进化路径，升级为“候选 skill 先验证、评审、再启用”的安全闭环。

## 1. 结论

本次实现的是第一阶段 Verified Skill Evolution：模型仍然可以从 `/learn` 或 `/evolve propose-skill*` 生成 skill，但生成结果只会进入候选区，不会直接进入正式 skill loader。

新的落地路径是：

```text
learn / propose-skill
  -> 写入 candidate skill
  -> validate 静态校验
  -> add-eval-case 记录任务评估用例
  -> eval 运行候选评估
  -> run-eval 执行至少三轮任务评估
  -> show-eval 展示用户可见评估报告
  -> approve 人工确认
  -> promote 启用正式 skill
  -> checkpoint + reload
```

这比原来的“approve 后 apply 直接写 `.mewcode/skills`”更保守，也更适合代码智能体。因为错误 skill 会长期影响后续工具选择、代码修改顺序和验证策略，风险比普通 memory 更高。

## 2. 与 Hermes 原版策略的差异

| 维度 | Hermes 倾向 | 当前项目策略 |
|---|---|---|
| skill 生成 | background review 可自动 patch/create | 只生成 candidate，不直接启用 |
| 用户确认 | 偏轻量，强调持续学习 | show-eval 后再 approve/promote |
| 验证重点 | 经验沉淀与可复用性 | 静态安全、候选隔离、eval、execution eval、checkpoint |
| 启用路径 | 写入 skill 资产后可加载 | candidate 通过 eval + execution eval + promote 后才进入 `.mewcode/skills` |
| 风险控制 | 依赖受限工具和后台隔离 | 额外增加候选区、静态策略、显式启用 |

当前方案不是全面替代 Hermes，而是在代码智能体场景下提高安全性：模型负责提出候选，系统负责校验边界，用户负责授权启用。

## 3. 新状态机

Memory 仍保持原闭环：

```text
observe -> propose -> validate -> approve -> apply
```

Skill 改为候选闭环：

```text
learn / propose-skill
  -> candidate
  -> validate
  -> eval-case
  -> eval
  -> run-eval
  -> show-eval
  -> approve
  -> promote
```

其中：

- `candidate`：写入 `.mewcode/evolution/candidates/<proposal_id>/SKILL.md`。
- `eval-case`：写入 `.mewcode/evolution/evals/<skill-name>/cases.jsonl`，记录任务输入、必须覆盖的关键步骤和禁止出现的错误策略。
- `eval`：对 candidate 做确定性评估，确认候选 skill 可解析、通过 validate，并满足至少一个任务评估用例。
- `run-eval`：要求至少 3 个 eval case，并生成 JSON/Markdown 多轮评估报告。
- `show-eval`：把 Markdown 报告展示给用户，让用户在 approve/promote 前看到测试效果。
- `approve`：人工确认 proposal 可进入启用阶段。
- `promote`：将 candidate 写入正式 `.mewcode/skills/<name>/SKILL.md`。
- `applied`：proposal 状态仍沿用既有 `applied`，表示 candidate 已正式启用。

## 4. Candidate 存储结构

每个 skill proposal 会生成：

```text
.mewcode/evolution/candidates/<proposal_id>/SKILL.md
.mewcode/evolution/candidates/<proposal_id>/manifest.json
.mewcode/evolution/candidates/<proposal_id>/eval_report.json
.mewcode/evolution/candidates/<proposal_id>/eval_report.md
.mewcode/evolution/evals/<skill-name>/cases.jsonl
```

`manifest.json` 记录：

```json
{
  "proposal_id": "prop_xxx",
  "skill_name": "debug-regression-loop",
  "action": "create",
  "status": "candidate",
  "evidence_ids": ["ev_xxx"],
  "formal_target": ".mewcode/skills/debug-regression-loop/SKILL.md",
  "candidate_skill": ".mewcode/evolution/candidates/prop_xxx/SKILL.md",
  "created_at": 1780000000.0,
  "promoted_at": 0.0,
  "eval_status": "pending",
  "eval_checks": [],
  "eval_errors": [],
  "eval_case_results": [],
  "evaluated_at": 0.0,
  "execution_eval_status": "pending",
  "execution_eval_report": "",
  "execution_eval_markdown": "",
  "execution_eval_rounds": [],
  "execution_evaluated_at": 0.0
}
```

promote 成功后，manifest 的 `status` 会变为 `enabled`，并写入 `promoted_at`。

## 5. 命令语义

### `/learn`

`/learn` 仍然是显式学习入口：

```text
/learn <skill-name> :: <description> :: <skill body>
```

执行逻辑：

```text
记录 learn evidence
  -> 同名项目 skill 存在：创建 patch proposal + candidate
  -> 同名项目 skill 不存在：创建 create proposal + candidate
```

### `/evolve apply`

现在只用于 memory proposal。

如果对 skill proposal 执行 apply，会返回提示：

```text
skill proposals must be promoted with /evolve promote after review
```

### `/evolve preview`

新增只读预览命令：

```text
/evolve preview <proposal_id>
```

memory proposal 会展示目标 `.mewcode/memories.md` 和将追加的 bullet。skill proposal 会展示 candidate `SKILL.md`、formal target，并输出 formal 与 candidate 的 unified diff。preview 不写正式 memory/skill，也不改变 proposal 状态；如果 candidate 文件被清理，preview 只从 proposal payload 内存渲染，不会重建 candidate 目录。

### `/evolve promote`

新增 skill 启用命令：

```text
/evolve promote <proposal_id>
```

promote 要求：

- proposal 必须存在；
- target 必须是 `skill`；
- status 必须是 `approved`；
- validate 必须通过；
- candidate eval 必须通过；
- candidate execution eval 必须通过；
- candidate `SKILL.md` 必须能被 `parse_skill_file()` 解析。

### `/evolve eval`

新增 candidate 评估命令：

```text
/evolve eval <proposal_id>
```

当前 eval 是 deterministic gate，不调用模型：

- proposal 必须存在；
- target 必须是 `skill`；
- proposal validate 必须通过；
- candidate `SKILL.md` 必须能被 `parse_skill_file()` 解析。
- 至少存在一个 eval case；
- 每个 eval case 的 `must_contain` 必须出现在候选 SOP 中；
- 每个 eval case 的 `must_not_contain` 不得出现在候选 SOP 中。

### `/evolve add-eval-case`

新增任务评估用例命令：

```text
/evolve add-eval-case <proposal_id> :: <task> :: <must_contain_csv> [:: <must_not_contain_csv>]
```

示例：

```text
/evolve add-eval-case prop_xxx :: 修复复杂回归 bug 时应该遵循什么流程？ :: 复现失败,回归测试 :: 跳过测试
```

该命令会写入：

```text
.mewcode/evolution/evals/<skill-name>/cases.jsonl
```

case schema：

```json
{
  "id": "case_xxx",
  "proposal_id": "prop_xxx",
  "skill_name": "debug-regression-loop",
  "task": "修复复杂回归 bug 时应该遵循什么流程？",
  "must_contain": ["复现失败", "回归测试"],
  "must_not_contain": ["跳过测试"],
  "created_at": 1780000000.0
}
```

通过后写入 manifest：

```json
{
  "eval_status": "passed",
  "eval_checks": ["parse_skill_file", "eval_case:case_xxx"],
  "eval_errors": [],
  "eval_case_results": [
    {
      "id": "case_xxx",
      "status": "passed",
      "errors": []
    }
  ],
  "evaluated_at": 1780000000.0
}
```

promote 会检查 `eval_status == "passed"`，否则拒绝启用。

### `/evolve run-eval`

新增执行评估命令：

```text
/evolve run-eval <proposal_id>
```

当前 execution eval 仍是 deterministic/mock runner，不调用模型、不修改真实项目。它会：

- 要求 proposal 是 skill proposal；
- 要求普通 `/evolve eval` 已通过；
- 要求该 skill 至少有 3 个 eval case；
- 对每个 case 加载 candidate SOP，检查 `must_contain` 和 `must_not_contain`；
- 为每轮写入 `round`、`case_id`、`task`、`status`、`errors` 和 `execution_summary`；
- 写入 `eval_report.json` 和 `eval_report.md`；
- 在 `execution_sandbox/round_*` 下写入 `task.md`、候选 `SKILL.md` 快照、`rendered_prompt.md` 和 `result.json`；
- 更新 manifest 的 `execution_eval_status` 和 `execution_eval_rounds`。

少于三轮时会拒绝：

```text
execution eval requires at least 3 eval cases
```

### `/evolve show-eval`

新增用户可见报告命令：

```text
/evolve show-eval <proposal_id>
```

该命令读取 `.mewcode/evolution/candidates/<proposal_id>/eval_report.md` 并展示给用户。这样 approve/promote 前，用户能看到候选 skill 在多轮任务 case 中的通过情况，而不是只看到一个 `passed` 状态。

promote 现在同时检查：

```text
eval_status == "passed"
execution_eval_status == "passed"
```

否则拒绝启用。

promote 成功后会：

- 写入正式 `.mewcode/skills/<name>/SKILL.md`；
- 将 proposal 状态改为 `applied`；
- 更新 candidate manifest 为 `enabled`；
- 尝试 reload skill loader；
- 在命令层 promote 前创建 checkpoint。

## 6. 静态安全校验

本次新增基础静态策略：如果 skill body 包含明显危险命令片段，会阻止通过 validate。

当前阻断示例：

```text
rm -rf /
sudo rm -rf
chmod 777 /
curl | sh
curl -s | sh
wget -qO-
```

同时对过宽规则词给 warning，例如：

```text
永远
所有任务
必须
禁止
```

这些 warning 不阻断，但要求人工 review 时关注 scope 是否过度泛化。

## 7. 修改清单

- 修改 `mewcode/evolution/engine.py`：新增 candidate 路径、candidate skill/manifest 写入、`evaluate()`、`add_eval_case()`、`run_execution_eval()`、`promote()`、skill direct apply 拒绝、eval case 执行、execution eval 报告和静态危险命令校验。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve preview`、`/evolve add-eval-case`、`/evolve eval`、`/evolve run-eval`、`/evolve show-eval` 和 `/evolve promote`，并让 `/evolve apply` 不再对 skill 做正式启用。
- 修改 `tests/test_evolution.py`：新增 candidate 写入、eval manifest、eval case 缺失阻断、eval case 成功/失败、execution eval 报告、direct apply 拒绝、promote eval/execution eval 门禁、promote 启用、patch promote、危险命令阻断和命令层 eval/run-eval/show-eval/promote/reload 测试。
- 修改 `README.md`：更新自进化命令和 memory/skill 分流语义。
- 新增本文档：记录 Verified Skill Evolution 的设计和验证。

## 8. 测试记录

TDD 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
8 failed, 11 passed
```

失败原因符合预期：

- `EvolutionEngine` 尚无 `candidate_skill_path()` / `candidate_manifest_path()`。
- `EvolutionEngine` 尚无 `promote()`。
- skill proposal 仍可通过 `apply()` 直接写入正式 skill。
- 尚未阻断危险命令 candidate。
- `/evolve promote` 尚未接入命令层。

绿灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
19 passed
```

扩展回归记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
193 passed
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

全量首个失败点仍为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突，和本次 candidate/promote 自进化修改无直接依赖。

### 2026-07-20 Eval Gate 追加记录

TDD 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
5 failed, 16 passed
```

失败原因符合预期：

- candidate manifest 缺少 `eval_status`。
- `EvolutionEngine` 尚无 `evaluate()`。
- `promote()` 尚未要求 eval 通过。
- `/evolve eval` 尚未接入命令层。

绿灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
21 passed
```

扩展回归记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
195 passed
```

格式检查记录：

```text
git diff --check
```

命令无输出，表示未发现 diff whitespace 问题。

全量测试记录：

```text
PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_message_splicing
```

全量首个失败点停在既有 agent 消息拼接测试，断言期望消息数为 5、实际为 4，和本次 candidate eval gate 修改无直接依赖。

### 2026-07-20 Eval Case Gate 追加记录

本次把 eval 从“候选 skill 可解析”升级为“候选 skill 必须通过至少一个任务评估用例”。这对应用户提出的安全要求：模型生成的 skill 不应因为格式正确就被启用，而应先证明它覆盖了目标任务中的关键步骤。

TDD 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
5 failed, 18 passed
```

失败原因符合预期：

- 无 eval case 时 `evaluate()` 仍然返回 passed。
- `EvolutionEngine` 尚无 `add_eval_case()`。
- manifest 尚无 `eval_case_results`。
- `/evolve add-eval-case` 尚未接入命令层。

追加安全红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_add_eval_case_rejects_invalid_skill_name -q
1 failed
```

失败原因符合预期：无效 skill name 仍可写入 eval case 路径。

绿灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
24 passed
```

扩展回归记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
198 passed
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

全量首个失败点仍为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突，和本次 eval case gate 修改无直接依赖。

修改内容：

- 修改 `mewcode/evolution/engine.py`：新增 `.mewcode/evolution/evals/<skill-name>/cases.jsonl`、`add_eval_case()`、case 读取校验、case 执行和 `eval_case_results` manifest 留档。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve add-eval-case <proposal_id> :: <task> :: <must_contain_csv> [:: <must_not_contain_csv>]`。
- 修改 `tests/test_evolution.py`：新增无 case 阻断、case 通过、case 失败、命令层 add-eval-case 到 promote 的完整流程测试。
- 修改 `README.md`、`docs/hermes-skill-evolution-implementation.md`、`docs/hermes-evolution-rewind-review.md` 和本文档：同步记录新的 case gate。

### 2026-07-21 Execution Eval Gate 追加记录

本次把 candidate skill 启用门禁从“至少一个 eval case 覆盖关键步骤”升级为“至少三轮任务评估并生成用户可见报告”。这对应用户提出的要求：候选 skill 不应只因为文本覆盖就启用，而应在提交应用申请前展示测试效果。

TDD 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
1 failed, 27 passed
```

失败原因符合预期：`/evolve run-eval` 和 `/evolve show-eval` 尚未接入命令层，导致命令层完整 promote 流程失败。

追加 `/learn` 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_learn_command_points_to_eval_promote_flow -q
1 failed
```

失败原因符合预期：`/learn` 创建提示仍只包含 `eval/approve/promote`，没有提示 `run-eval/show-eval`。

修改内容：

- 修改 `mewcode/evolution/engine.py`：新增 `run_execution_eval()`、`read_execution_eval_report()`、`execution_eval_report_path()` 和 `execution_eval_markdown_path()`。
- 修改 `mewcode/evolution/engine.py`：新增 `eval_report.json` / `eval_report.md` 写入，并在 manifest 记录 `execution_eval_status`、报告路径、轮次结果和评估时间。
- 修改 `mewcode/evolution/engine.py`：`promote()` 新增 `execution_eval_status == "passed"` 门禁。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve run-eval <proposal_id>` 和 `/evolve show-eval <proposal_id>`。
- 修改 `mewcode/commands/handlers/learn.py`：创建提示和 help 同步要求 `run-eval/show-eval`。
- 修改 `tests/test_evolution.py`：新增少于三轮阻断、报告写入、sandbox artifacts 落地、新增 eval case 失效旧报告、promote 未 execution eval 阻断、execution eval 后 promote 成功、命令层报告展示测试。
- 修改 `README.md`、`docs/self-evolution-development-progress-recap-zh.md` 和本文档：同步记录 execution eval gate。

绿灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
37 passed
```

扩展回归记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
212 passed
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

## 9. 后续方向

当前已实现第一阶段 candidate/promote、eval gate、eval case gate、execution eval report gate，以及基础 usage log / quarantine。下一阶段建议增加：

- 自动 usage 归因：记录 skill 触发后的任务结果、用户反馈和失败原因，用于后续自动降级或复盘。
- quarantine 建议器：当同一 skill 多次失败或被用户纠正时，提示 `/evolve quarantine <skill-name>` 或生成 patch proposal。
- 更强的任务回放：由受限 fork agent 在沙盒中真实执行 case，而不只是检查候选 SOP 的关键步骤覆盖。
- background review：只能生成 candidate，禁止自动 promote。

## 10. 设计取舍

该方案牺牲了 Hermes 原版的学习速度，但降低了代码智能体长期行为污染风险。核心原则是：

```text
模型生成的是候选，不是事实；
候选通过验证和评审后，才可以变成正式能力。
```
