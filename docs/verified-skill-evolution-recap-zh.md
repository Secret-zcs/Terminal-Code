# Verified Skill Evolution 自进化复盘

> 日期：2026-07-18
> 最近更新：2026-07-20
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
  -> approve 人工确认
  -> promote 启用正式 skill
  -> checkpoint + reload
```

这比原来的“approve 后 apply 直接写 `.mewcode/skills`”更保守，也更适合代码智能体。因为错误 skill 会长期影响后续工具选择、代码修改顺序和验证策略，风险比普通 memory 更高。

## 2. 与 Hermes 原版策略的差异

| 维度 | Hermes 倾向 | 当前项目策略 |
|---|---|---|
| skill 生成 | background review 可自动 patch/create | 只生成 candidate，不直接启用 |
| 用户确认 | 偏轻量，强调持续学习 | approve 后还需要 promote |
| 验证重点 | 经验沉淀与可复用性 | 静态安全、候选隔离、eval、checkpoint |
| 启用路径 | 写入 skill 资产后可加载 | candidate 通过 eval + promote 后才进入 `.mewcode/skills` |
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
  -> approve
  -> promote
```

其中：

- `candidate`：写入 `.mewcode/evolution/candidates/<proposal_id>/SKILL.md`。
- `eval-case`：写入 `.mewcode/evolution/evals/<skill-name>/cases.jsonl`，记录任务输入、必须覆盖的关键步骤和禁止出现的错误策略。
- `eval`：对 candidate 做确定性评估，确认候选 skill 可解析、通过 validate，并满足至少一个任务评估用例。
- `approve`：人工确认 proposal 可进入启用阶段。
- `promote`：将 candidate 写入正式 `.mewcode/skills/<name>/SKILL.md`。
- `applied`：proposal 状态仍沿用既有 `applied`，表示 candidate 已正式启用。

## 4. Candidate 存储结构

每个 skill proposal 会生成：

```text
.mewcode/evolution/candidates/<proposal_id>/SKILL.md
.mewcode/evolution/candidates/<proposal_id>/manifest.json
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
  "evaluated_at": 0.0
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

- 修改 `mewcode/evolution/engine.py`：新增 candidate 路径、candidate skill/manifest 写入、`evaluate()`、`add_eval_case()`、`promote()`、skill direct apply 拒绝、eval case 执行和静态危险命令校验。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve add-eval-case`、`/evolve eval` 和 `/evolve promote`，并让 `/evolve apply` 不再对 skill 做正式启用。
- 修改 `tests/test_evolution.py`：新增 candidate 写入、eval manifest、eval case 缺失阻断、eval case 成功/失败、direct apply 拒绝、promote eval 门禁、promote 启用、patch promote、危险命令阻断和命令层 eval/promote/reload 测试。
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

## 9. 后续方向

当前已实现第一阶段 candidate/promote、eval gate 和 eval case gate。下一阶段建议增加：

- `/evolve quarantine <skill-name>`：启用后如果用户纠正或任务失败，将正式 skill 移入隔离区。
- usage log：记录 skill 触发、结果、用户反馈，用于后续自动降级或复盘。
- 更强的任务回放：由受限 fork agent 在沙盒中执行 case，而不只是检查候选 SOP 的关键步骤覆盖。
- background review：只能生成 candidate，禁止自动 promote。

## 10. 设计取舍

该方案牺牲了 Hermes 原版的学习速度，但降低了代码智能体长期行为污染风险。核心原则是：

```text
模型生成的是候选，不是事实；
候选通过验证和评审后，才可以变成正式能力。
```
