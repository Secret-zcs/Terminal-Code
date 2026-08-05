# 自进化开发进度总复盘

> 日期：2026-07-29
> 基线提交：`f01966c 为候选 skill 增加 eval case 门禁`
> 最新阶段：候选 skill 执行评估报告门禁、JSON 高级 eval case 命令、scripted LLM 驱动的真实 Agent loop 评测、确定性 child-agent fork 轨迹、只读 preview、usage log、隔离建议、usage-driven patch candidate、只读 eval case 建议与质量摘要
> 范围：`mewcode/evolution/`、`/evolve`、`/learn`、candidate skill、eval gate、checkpoint/rewind 保护和测试留档

## 1. 当前结论

当前项目的自进化已经进入 **手动触发、候选隔离、验证后启用** 的阶段。

它已经不是最初的“只写 memory”版本，也不是“模型生成 skill 后直接启用”的高风险版本。现在的核心路径是：

```text
memory: observe -> propose -> validate -> approve -> apply
skill:  learn/propose -> candidate -> validate -> add-eval-case -> eval -> run-eval -> show-eval -> approve -> promote
```

这意味着：

- memory 可以在用户 approve 后写入 `.mewcode/memories.md`。
- skill 不会直接进入正式 skill loader，而是先进入 `.mewcode/evolution/candidates/<proposal_id>/`。
- candidate skill 必须通过 deterministic eval，并且至少完成三轮带 child-agent 轨迹的 execution eval，才能被 promote；eval case 可选择继续使用 deterministic replay，也可显式使用 `agent_loop_scripted` 让 scripted LLM 走真实 `Agent.run()`。
- execution eval 会生成用户可见的 JSON/Markdown 报告和每轮 `child_agent/` 轨迹，用户可用 `/evolve show-eval` 先看测试效果再 approve/promote。
- 用户可用 `/evolve preview <proposal_id>` 在 approve/apply/promote 前查看 memory 追加内容或 skill unified diff。
- `LoadSkill` 成功激活 skill 会写入 `.mewcode/evolution/skill_usage.jsonl`，未知 skill 加载失败会自动写入 `failure` usage，用于后续追踪 skill 影响。
- 用户可用 `/evolve record-usage <skill-name> :: <event> [:: summary]` 手动记录失败或用户纠正。
- 用户可用 `/evolve suggest-quarantine [skill-name]` 基于负面 usage 事件查看隔离建议。
- 用户可用 `/evolve propose-patch-from-usage <skill-name>` 将负面 usage 转为 skill patch candidate。
- 用户可用 `/evolve suggest-eval-cases <proposal_id> [count]` 查看候选 skill 的 eval case 建议命令、质量摘要、coverage gap、warnings 和 recommendation；默认三条，必要时可提高 count 覆盖更多真实 usage feedback，但不会自动写入 eval case。
- 用户可用 `/evolve quarantine <skill-name> [:: reason]` 将不可靠的项目级正式 skill 移入隔离区。
- promote 前会尝试 checkpoint，promote 后会尝试 reload skill loader。
- 运行时自进化明确只允许 `memory | skill`，不允许 `code | tool | prompt` 自动落地。

整体进度可以概括为：**安全版 Hermes skill evolution 的主干闭环已完成，并新增了多轮评估报告门禁、scripted LLM 驱动的真实 Agent loop、确定性 child-agent fork 轨迹、只读预览、usage log、手动 quarantine、隔离建议、usage-driven patch candidate、只读 eval case 建议、质量摘要、coverage gap 和可调建议数量；Hermes 原版的后台自动 review、真实 LLM 子 Agent 沙盒任务执行、自动 usage 归因/自动降级还未完成。**

## 2. 版本演进时间线

### 阶段 0：基础导入

提交：`3295895 Initial MewCode project import`

此时项目已有 Claude Code 风格的终端 Agent 主体、工具系统、命令系统、skills、checkpoint/rewind 等基础能力，但没有独立的 Hermes 风格自进化闭环。

### 阶段 1：Memory + Skill 目标收敛

提交：`98477c2 修改自进化机制，实现第一步-skill提案（进候选skill）`

关键目标：

- 引入 `mewcode/evolution/` 子系统。
- 将自进化 target 收敛为 `memory | skill`。
- 拒绝 `code | tool | prompt` 运行时自修改。
- 增加 `/evolve` 命令，用于 observe/propose/approve/apply。
- 增加 `/learn` 显式学习入口，用于把复用流程沉淀为 skill proposal。

这一阶段确定了核心原则：**自进化写外部可审计资产，不改 Agent 核心执行面。**

### 阶段 2：Candidate Skill + Promote

提交：`98477c2` 中已包含该阶段主干

关键目标：

- skill proposal 创建后只写入 candidate：

```text
.mewcode/evolution/candidates/<proposal_id>/SKILL.md
.mewcode/evolution/candidates/<proposal_id>/manifest.json
```

- `/evolve apply` 不再启用 skill，只处理 memory。
- `/evolve promote <proposal_id>` 才能把 candidate 写入正式 `.mewcode/skills/<name>/SKILL.md`。
- promote 必须先 approve。
- promote 前尝试 checkpoint，promote 后尝试 reload skill loader。
- 增加危险命令静态策略，例如阻断 `rm -rf /`、`sudo rm -rf`、`chmod 777 /`、`curl | sh`。

这一阶段解决了“模型生成 skill 直接污染长期行为库”的问题。

### 阶段 3：Candidate Eval Gate

提交：`1d5318c 为自进化候选 skill 增加 eval 门禁`

关键目标：

- 新增 `EvolutionEngine.evaluate(proposal_id)`。
- 新增 `/evolve eval <proposal_id>`。
- candidate manifest 增加：

```json
{
  "eval_status": "pending|passed|failed",
  "eval_checks": [],
  "eval_errors": [],
  "evaluated_at": 0.0
}
```

- `promote()` 要求 `eval_status == "passed"`。

这一阶段先建立了 eval 的状态门禁，但第一版 eval 主要验证 validate 与 `parse_skill_file()`。

### 阶段 4：Eval Case Gate

提交：`f01966c 为候选 skill 增加 eval case 门禁`

关键目标：

- 新增 eval case 文件：

```text
.mewcode/evolution/evals/<skill-name>/cases.jsonl
```

- 新增 `/evolve add-eval-case <proposal_id> :: <task> :: <must_contain_csv> [:: <must_not_contain_csv>]`。
- `evaluate()` 现在要求至少一个 eval case。
- eval case 会检查候选 SOP 是否包含 `must_contain`，且不包含 `must_not_contain`。
- manifest 增加 `eval_case_results`，记录每个 case 的通过/失败明细。
- 阻断无效 skill name 写 eval case 路径，避免路径逃逸。

这一阶段把 eval 从“格式正确”推进到“必须覆盖目标任务关键步骤”。

### 阶段 5：流程提示修正

本次复盘时发现 `/learn` 的用户提示仍然写着旧流程：`approve` 后 `apply`。这已经不符合当前 skill 自进化路径。

已修正：

- `/learn` docstring 改为：学习结果不会直接写 skill，必须 add eval case、eval、approve、promote。
- `/learn` 创建成功提示改为指向：

```text
/evolve add-eval-case <proposal_id>
/evolve eval <proposal_id>
/evolve approve <proposal_id>
/evolve promote <proposal_id>
```

- `/learn help` 同步修正为 eval/promote 语义。
- 新增测试 `test_learn_command_points_to_eval_promote_flow`，防止未来退回旧文案。

红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_learn_command_points_to_eval_promote_flow -q
1 failed
```

绿灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_learn_command_points_to_eval_promote_flow -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py -q
25 passed
```

### 阶段 6：Skill Execution Eval Gate

日期：2026-07-21

关键目标：

- 新增 `EvolutionEngine.run_execution_eval(proposal_id)`，要求候选 skill 至少有 3 个任务 eval case。
- 新增 `EvolutionEngine.read_execution_eval_report(proposal_id)`。
- 新增 candidate 报告文件：

```text
.mewcode/evolution/candidates/<proposal_id>/eval_report.json
.mewcode/evolution/candidates/<proposal_id>/eval_report.md
```

- candidate manifest 增加：

```json
{
  "execution_eval_status": "pending|passed|failed",
  "execution_eval_report": "",
  "execution_eval_markdown": "",
  "execution_eval_rounds": [],
  "execution_evaluated_at": 0.0
}
```

- 新增 `/evolve run-eval <proposal_id>`，生成多轮任务评估报告。
- 新增 `/evolve show-eval <proposal_id>`，把 Markdown 报告直接展示给用户。
- `promote()` 现在要求 `eval_status == "passed"` 且 `execution_eval_status == "passed"`。
- `/learn` 的创建提示和 help 同步要求 `add-eval-case -> eval -> run-eval -> show-eval -> approve -> promote`。

TDD 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
1 failed, 27 passed
```

失败原因符合预期：命令层尚未接入 `run-eval` / `show-eval`，导致 promote 前 execution eval 未通过。

追加 `/learn` 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_learn_command_points_to_eval_promote_flow -q
1 failed
```

失败原因符合预期：`/learn` 仍未提示 `run-eval` 和 `show-eval`。

绿灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
46 passed
```

扩展回归记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
221 passed
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

这一阶段回答了“候选 skill 是否至少正确执行几轮任务、提交应用前用户能不能看到测试效果”的问题。当前实现仍是确定性执行评估：它加载候选 SOP，对每个任务 case 检查必须覆盖/禁止出现的关键策略，并写出每轮结果；它不是完整的模型沙盒 runner。

## 3. 当前实现模块

### `mewcode/evolution/models.py`

职责：定义自进化数据模型。

当前模型：

- `EvolutionEvidence`：记录观察、成功经验、失败经验、用户反馈、测试结果、rewind 等证据。
- `EvolutionProposal`：记录将要落地的变更。
- `EvolutionValidation`：记录 validate 的 errors/warnings。

当前边界：

```python
ProposalTarget = Literal["memory", "skill"]
ProposalStatus = Literal["proposed", "approved", "rejected", "applied"]
ProposalRisk = Literal["low", "medium", "high"]
```

也就是说，运行时自进化没有 `code/tool/prompt` 目标。

### `mewcode/evolution/store.py`

职责：持久化 evidence/proposal。

当前写入：

```text
.mewcode/evolution/evidence.jsonl
.mewcode/evolution/proposals.jsonl
```

特点：

- 使用 JSONL，便于追加和审计。
- `recent_evidence_ids()` 会给 proposal 自动关联最近 evidence。
- 读 JSONL 时会跳过损坏行，避免单条坏数据导致系统不可用。

### `mewcode/evolution/engine.py`

职责：自进化核心状态机。

当前主要能力：

- `record_evidence()`：记录 evidence。
- `propose()`：创建 memory proposal。
- `propose_skill()`：创建 create skill proposal，并写 candidate。
- `propose_skill_patch()`：创建 patch skill proposal，并写 candidate。
- `validate()`：校验 memory/skill proposal。
- `approve()` / `reject()`：人工状态转换。
- `apply()`：只允许 approved memory proposal 写入 `.mewcode/memories.md`。
- `add_eval_case()`：为 candidate skill 增加任务评估用例。
- `evaluate()`：执行 deterministic eval。
- `run_execution_eval()`：执行至少三轮 candidate skill 任务评估并生成报告。
- `read_execution_eval_report()`：读取用户可见 Markdown 报告。
- `promote()`：将通过 eval、execution eval 且 approved 的 candidate skill 写入正式 skill。

### `mewcode/commands/handlers/evolve.py`

职责：`/evolve` 命令入口。

当前命令：

```text
/evolve observe <summary>
/evolve propose <title> :: <memory change>
/evolve propose-skill <name> :: <description> :: <skill body>
/evolve propose-skill-patch <name> :: <description> :: <skill body>
/evolve list
/evolve show <proposal_id>
/evolve preview <proposal_id>
/evolve approve <proposal_id>
/evolve reject <proposal_id>
/evolve apply <proposal_id>
/evolve add-eval-case <proposal_id> :: <task> :: <must_contain_csv> [:: <must_not_contain_csv>]
/evolve eval <proposal_id>
/evolve run-eval <proposal_id>
/evolve show-eval <proposal_id>
/evolve promote <proposal_id>
```

重要行为：

- apply 前，如果 target 是 memory，会尝试 checkpoint。
- promote 前，会尝试 checkpoint。
- promote 成功后，如果存在 `skill_loader`，会尝试 reload。
- skill proposal 的 apply 会失败并提示使用 promote。

### `mewcode/commands/handlers/learn.py`

职责：Hermes 风格显式学习入口。

当前行为：

```text
/learn <skill-name> :: <description> :: <skill body>
```

- 如果项目级同名 skill 已存在，创建 patch proposal。
- 如果不存在，创建 create proposal。
- 会先记录 `source="learn-command"` 的 evidence。
- 不直接启用 skill。
- 当前提示已修正为 eval/run-eval/show-eval/promote 流程。

## 4. 当前数据落点

| 数据 | 路径 | 说明 |
|---|---|---|
| Evidence | `.mewcode/evolution/evidence.jsonl` | 观察、反馈、测试等证据 |
| Proposal | `.mewcode/evolution/proposals.jsonl` | 待应用或已应用的自进化提案 |
| Memory | `.mewcode/memories.md` | approved memory proposal 的落地文件 |
| Candidate skill | `.mewcode/evolution/candidates/<proposal_id>/SKILL.md` | 待评审 skill |
| Candidate manifest | `.mewcode/evolution/candidates/<proposal_id>/manifest.json` | candidate 状态、eval 结果、目标路径 |
| Execution eval report | `.mewcode/evolution/candidates/<proposal_id>/eval_report.json` / `eval_report.md` | 多轮执行评估结果和用户可见报告 |
| Eval case | `.mewcode/evolution/evals/<skill-name>/cases.jsonl` | 任务评估用例 |
| Skill usage log | `.mewcode/evolution/skill_usage.jsonl` | 正式 skill 被加载和隔离的追踪记录 |
| Quarantined skill | `.mewcode/evolution/quarantine/<skill-name>/` | 被手动隔离的项目级正式 skill |
| Formal skill | `.mewcode/skills/<name>/SKILL.md` | promote 后的正式项目 skill |

## 5. 当前安全边界

### 已实现

- 运行时 target 白名单：只允许 `memory | skill`。
- skill create 不覆盖已有项目 skill。
- skill patch 只能 patch 已存在项目级 skill，不 patch 用户全局 skill 或内置 skill。
- skill proposal 先写 candidate，不直接进入正式 skill loader。
- promote 必须先 approve。
- promote 必须先 eval passed。
- eval 必须至少有一个 eval case。
- promote 必须先 execution eval passed。
- execution eval 至少要求 3 个 eval case，并生成用户可见报告。
- execution eval 在 candidate 目录内生成 deterministic child-agent sandbox artifacts，包含任务、候选 skill 快照、渲染 SOP、结构化结果、子 Agent 输入、工具策略、transcript 和 final answer。
- `/evolve preview <proposal_id>` 已支持 memory 追加预览和 skill unified diff，且不会写正式 memory/skill；candidate 缺失时也只从 proposal payload 内存渲染。
- `LoadSkill` 成功加载 skill 后会记录 `load` usage event；未知 skill 加载失败会记录 `failure` usage event。
- `/evolve quarantine <skill-name> [:: reason]` 只隔离项目级正式 skill，不隔离内置 skill 或用户全局 skill。
- eval case 路径校验 skill name，避免路径逃逸。
- 危险命令片段会被 validate 阻断。
- 宽泛词如“永远/所有任务/必须/禁止”会产生 warning，提示人工 review scope。
- apply/promote 前会尝试 checkpoint。

### 仍未实现

- 没有后台 background review 自动从对话中蒸馏 skill。
- 没有后台 fork reviewer 隔离运行自进化审查。
- execution eval 目前是确定性 child-agent replay，不是真实 LLM 子 Agent 沙盒任务执行。
- usage log 目前记录 skill load、未知 skill 加载失败和 quarantine，并支持手动记录 failure/user_feedback；尚未自动记录完整任务成功、失败和用户纠正。
- quarantine 目前是手动命令，已能根据 usage failure 阈值给出建议，但不会自动执行隔离。
- 没有自动从失败任务或 rewind 事件反推 skill 需要 patch。
- 没有受限 LLM 子 Agent 真实执行 eval case；当前 child-agent runner 仍是 deterministic replay。

## 6. 与 Hermes 原版的差距

| 能力 | 当前项目 | Hermes 原版倾向 |
|---|---|---|
| 触发方式 | 手动 `/learn`、`/evolve propose-skill*` | 回合结束 background review 自动触发 |
| 生成位置 | 先写 candidate | 可由后台 review patch/create skill |
| 启用方式 | eval case + eval + run-eval + show-eval + approve + promote | 更偏持续学习和自动沉淀 |
| 验证方式 | parse + deterministic eval case + 3 轮 sandbox artifact 报告 | skill verifier、reload、任务回放 |
| 隔离方式 | 主命令流内受控执行 | fork review agent 隔离 |
| 风险控制 | 候选区、manifest、checkpoint、手动 promote | 工具白名单、curator review、skill 管理 |
| 反馈闭环 | evidence/proposal 记录 | 更完整的会话复盘、skill usage 和后续 patch |

当前项目比 Hermes 原版更保守，主要是为了代码智能体场景：错误 skill 会长期影响后续代码修改和验证策略，因此必须先作为候选资产被验证和评审。

## 7. 当前测试覆盖

核心测试集中在 `tests/test_evolution.py`。

已覆盖能力：

- evidence/proposal 创建。
- memory approve/apply。
- 拒绝 `code/tool/prompt` 目标。
- skill proposal 写 candidate。
- candidate manifest 初始化。
- eval 无 case 阻断。
- eval case 通过/失败。
- execution eval 少于三轮阻断。
- execution eval 报告写入 JSON/Markdown。
- execution eval sandbox artifacts 落地。
- 新增 eval case 会失效既有 eval/execution eval，防止旧报告被复用。
- promote 必须 execution eval passed。
- 无效 skill name 不允许写 eval case。
- skill direct apply 拒绝。
- promote 必须 approve。
- promote 必须 eval passed。
- create skill 不覆盖已有 skill。
- patch skill 更新已有项目 skill。
- 危险命令静态策略阻断。
- skill usage log 写入与读取。
- `/evolve` 命令 observe/propose/list/preview/apply/eval/run-eval/show-eval/promote/record-usage/suggest-quarantine/suggest-eval-cases/propose-patch-from-usage/quarantine。
- `/evolve preview` 的只读语义：memory preview 不创建 memory 文件，skill candidate 缺失时不重建 candidate 目录。
- `/evolve record-usage` 手动记录正式 skill 失败、用户纠正等 usage 事件。
- `/evolve suggest-quarantine` 基于负面 usage 事件给出只读隔离建议，不自动隔离。
- `/evolve propose-patch-from-usage` 基于负面 usage 生成 patch proposal 和 candidate，不自动启用。
- `/evolve suggest-eval-cases <proposal_id> [count]` 基于 proposal evidence 生成 eval case 建议命令、质量摘要、coverage gap、warnings 和 recommendation；默认三条，可提高 count 补齐更多 usage feedback，不自动写入 cases.jsonl。
- `/evolve quarantine` 移动正式项目 skill 到隔离区并 reload loader。
- `/learn` create/patch 优先级。
- `/learn` evidence 关联。
- `/learn` 提示指向 eval/run-eval/show-eval/promote 新流程。

本次最新验证：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
47 passed
```

## 8. 当前已知全量测试问题

当前全量测试不是完全绿灯，首个失败点为既有 agent 测试：

```text
PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

失败原因：旧测试期望 `WriteFile` 可以先写文件再 `ReadFile` 验证；当前工具安全策略要求写文件前必须先 `ReadFile`。这个问题和自进化机制没有直接依赖，但会影响 full suite 的最终通过状态。

## 9. 后续路线建议

### P0：补齐评审可见性（已完成）

- 已增加 `/evolve preview <proposal_id>`。
- memory preview 显示即将追加的 bullet 和目标 `.mewcode/memories.md`。
- skill preview 显示 candidate 路径、formal target 和 unified diff。
- 当前 preview 是只读操作，不写正式 memory/skill；如果 candidate 文件被清理，也不会为了预览而重建 candidate 目录。

理由：用户可以在 approve/apply/promote 前看到实际影响面，避免只凭 proposal JSON 做判断。

### P1：引入 usage log、quarantine、建议器与 patch candidate（基础版已完成）

- 已增加 `.mewcode/evolution/skill_usage.jsonl`。
- 已记录 `LoadSkill` 成功加载事件、未知 skill 加载失败事件和 `/evolve quarantine` 隔离事件。
- 已增加 `/evolve record-usage <skill-name> :: <event> [:: summary]`，支持手动记录 `failure`、`user_feedback` 等事件。
- 已增加 `/evolve suggest-quarantine [skill-name]`，当负面 usage 事件达到阈值时输出建议命令。
- 已增加 `/evolve propose-patch-from-usage <skill-name>`，把负面 usage 汇总进 patch candidate。
- 已增加 `/evolve suggest-eval-cases <proposal_id> [count]`，把 patch candidate 的 evidence 转为可审阅的 eval case 建议命令，并标出 high/medium/low 质量、coverage、rationale、summary 和 recommendation；默认三条，可提高 count 覆盖更多真实 usage feedback。
- 已增加 `/evolve quarantine <skill-name> [:: reason]`，把项目级正式 skill 移入 `.mewcode/evolution/quarantine/<skill-name>/`。
- 已在 command 层隔离后 reload skill loader，避免后续任务继续使用该正式 skill。
- 尚未自动记录任务成功/失败、用户纠正，也不会自动执行 quarantine、自动写入 eval case 或自动 promote patch proposal。

理由：skill 一旦启用会长期影响行为，必须有降级和追责机制。

### P2：更真实的 eval runner

- 将 eval case 从关键字检查升级为沙盒任务回放。
- 用受限 fork agent 执行 case。
- 限制工具白名单，避免 eval 过程修改真实项目。
- 记录输入、输出、工具调用、通过规则。

理由：当前 deterministic eval 只能证明 SOP 覆盖关键文本，不能证明 skill 真能完成任务。

### P3：后台 review 但只生成 candidate

- 回合结束后从对话中抽取可能的学习点。
- 后台 fork reviewer 只能写 evidence/proposal/candidate，禁止 promote。
- 自动 review 结果必须等待用户显式 eval/approve/promote。

理由：这会更接近 Hermes，但仍保留当前项目的安全边界。

### P4：从 rewind/failure 反推学习

- 当用户执行 rewind、任务失败、测试红灯、用户纠正时，自动记录 evidence。
- 结合 existing skill 命中，优先生成 patch proposal。
- 避免创建重复小 skill，控制 skill 膨胀。

理由：失败经验比成功路径更适合作为自进化触发点。

## 10. 总结

当前自进化机制已经完成了安全主干：

```text
evidence -> proposal -> candidate -> eval case -> eval -> run-eval -> show-eval -> approve -> promote
```

它已经能支持用户显式把复杂问题的解决流程沉淀为 project skill，并通过 candidate、eval、execution eval report、manifest、checkpoint 和 promote 控制风险。

但它还不是完整 Hermes：缺少后台 review、真实模型沙盒任务回放、自动 usage feedback 归因和自动 patch 评审。下一阶段不建议直接追求“自动生成并启用 skill”，而应优先补齐 **任务成功/失败自动归因、patch candidate 评估建议、受限 fork-agent eval runner**，让 skill 在隔离环境中真实完成一些任务后再进入长期能力库。

## 11. 最新推进记录：Eval Case Suggestion Count

本次推进补齐了 `/evolve suggest-eval-cases <proposal_id> [count]`。它解决的问题是：默认三条建议适合作为最小执行评估门槛，但当 usage-driven patch candidate 绑定了四条及以上真实用户反馈时，默认输出会留下 coverage gap。现在用户可以提高 `count`，先看到更多建议及其质量摘要，再决定是否显式写入 eval case。

修改内容：

- 修改 `mewcode/commands/handlers/evolve.py`：帮助文档和 usage 支持 `[count]`。
- 修改 `mewcode/commands/handlers/evolve.py`：解析第二个参数为正整数，并传入 `review_eval_case_suggestions()`。
- 修改 `tests/test_evolution.py`：新增四条 usage feedback 的命令层用例，验证 `count=4` 时全部反馈被 high-quality suggestion 覆盖，且仍不写入 `cases.jsonl`。
- 修改 `README.md`、`docs/hermes-evolution-rewind-review.md`、`docs/hermes-skill-evolution-implementation.md` 和 `docs/verified-skill-evolution-recap-zh.md`：同步留档。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_suggest_eval_cases_command_accepts_count_to_cover_feedback -q
1 failed  # 实现前红灯："<proposal_id> 4" 被误当成完整 proposal id

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_suggest_eval_cases_command_accepts_count_to_cover_feedback -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_review_eval_case_suggestions_reports_uncovered_usage_feedback tests/test_evolution.py::TestEvolveCommand::test_suggest_eval_cases_command_shows_uncovered_feedback tests/test_evolution.py::TestEvolveCommand::test_suggest_eval_cases_command_accepts_count_to_cover_feedback -q
3 passed

PYTHONPATH=. pytest tests/test_evolution.py -q
47 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
222 passed
```

编译和格式检查记录：`python3 -m py_compile mewcode/evolution/engine.py mewcode/commands/handlers/evolve.py` 与 `git diff --check` 均无输出。

全量测试记录：`PYTHONPATH=. pytest -q -x` 仍停在 `tests/test_agent.py::test_multi_step_autonomous`，失败原因为旧测试要求先 `WriteFile` 再 `ReadFile`，当前安全策略要求写前先读；和本次 count 参数修改无直接依赖。

边界说明：`count` 不改变自进化安全主线。系统仍只生成只读建议，不自动写入 eval case，不自动 approve，也不自动 promote；用户必须审阅测试意图后再执行 `add-eval-case -> eval -> run-eval -> show-eval -> approve -> promote`。

追加推进：coverage gap 的 recommendation 已从泛化建议升级为明确动作：提高 `count` 或手写 eval case。新增断言先红后绿，确保用户看到未覆盖反馈时能直接知道下一步该如何补齐测试覆盖。验证结果保持 `tests/test_evolution.py` 47 个通过、核心回归 222 个通过，full suite 仍停在既有 `test_multi_step_autonomous`。

## 12. 最新推进记录：LoadSkill 失败自动归因

本次推进补齐了第一个自动 usage 归因点：当 `LoadSkill` 请求一个不存在的 skill 时，系统在返回 unknown skill 错误的同时，会向 `.mewcode/evolution/skill_usage.jsonl` 记录 `event=failure`，metadata 中保存 `summary=unknown skill requested` 和当时可用 skill 列表。

TDD 记录：

```text
PYTHONPATH=. pytest tests/test_skills.py::TestLoadSkillTool::test_load_unknown_project_skill_records_failure_usage -q
1 failed  # 实现前红灯：usage log 为空

PYTHONPATH=. pytest tests/test_skills.py::TestLoadSkillTool::test_load_unknown_project_skill_records_failure_usage -q
1 passed

PYTHONPATH=. pytest tests/test_skills.py::TestLoadSkillTool -q
6 passed

PYTHONPATH=. pytest tests/test_skills.py -q
45 passed

PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
223 passed
```

编译和格式检查记录：`python3 -m py_compile mewcode/tools/load_skill.py mewcode/evolution/engine.py mewcode/commands/handlers/evolve.py` 与 `git diff --check` 均无输出。

全量测试记录：`PYTHONPATH=. pytest -q -x` 仍停在 `tests/test_agent.py::test_multi_step_autonomous`，失败原因为旧测试要求先 `WriteFile` 再 `ReadFile`，当前安全策略要求写前先读；和本次 LoadSkill 失败归因修改无直接依赖。

边界说明：该归因只记录失败事实，不会自动 quarantine、不会自动生成 patch candidate，也不会影响正式 skill loader。不存在的 skill 不会触发 quarantine suggestion，因为 quarantine 仍要求项目级正式 skill 存在。

## 13. 最新推进记录：公开基准种子数据集评测

日期：2026-07-29

本次推进新增了一个离线、确定性的自进化效果评测基线。它使用公开基准族作为任务来源参考，把 SWE-bench、AgentBench、MBPP 和 HumanEval 风格任务抽象成仓库内 JSONL seed cases，然后比较两段 SOP：

- baseline：自进化前的通用编码 SOP，只要求阅读需求、必要时运行测试、遇错继续。
- evolved：自进化后的候选 skill SOP，要求复现失败、最小补丁、回归测试、工具失败有限重试、rewind 安全、函数规格、边界用例、断言和禁止硬编码等关键步骤。

新增文件：

- `benchmarks/self_evolution_seed_cases.jsonl`：6 条公开 benchmark 启发的 seed cases。
- `mewcode/evolution/benchmark.py`：加载 JSONL、计算 required-term recall、生成 Markdown 报告。
- `scripts/run_self_evolution_dataset_eval.py`：命令行评测入口。
- `tests/test_self_evolution_benchmark.py`：评测加载、打分和报告结构测试。
- `docs/self-evolution-dataset-eval-results.json`：结构化评测结果。
- `docs/self-evolution-dataset-eval-results-zh.md`：用户可读评测报告。

当前结果：

```text
Cases: 6
Baseline Required Recall: 0.00%
Evolved Required Recall: 100.00%
Delta Required Recall: 100.00%
Baseline Passed: 0
Evolved Passed: 6
```

TDD 记录：

```text
PYTHONPATH=. pytest tests/test_self_evolution_benchmark.py -q
1 failed, 3 passed  # 实现前红灯：报告缺少 Dataset Selection Rationale / Before/After Interpretation

PYTHONPATH=. pytest tests/test_self_evolution_benchmark.py -q
4 passed
```

编译与生成记录：

```text
python3 -m py_compile mewcode/evolution/benchmark.py scripts/run_self_evolution_dataset_eval.py

PYTHONPATH=. python3 scripts/run_self_evolution_dataset_eval.py \
  --json-output docs/self-evolution-dataset-eval-results.json \
  --md-output docs/self-evolution-dataset-eval-results-zh.md
```

边界说明：

- 该评测测试的是候选 skill SOP 对任务关键步骤的覆盖能力，不是真实模型完成率。
- seed cases 是基于公开 benchmark 任务族抽象出来的本地用例，不复制原始 benchmark 实例。
- 当前公开基准评测不会 fork 一个真实 Agent 去执行仓库补丁、工具调用或函数实现。
- 下一步更强评测应把 deterministic child-agent runner 接入真实受限 LLM 子 Agent：给候选 skill、临时工作区、工具白名单和任务断言，让子 Agent 真实完成多轮任务后再产出 promote 申请。

## 14. 最新推进记录：确定性 Child-Agent Fork 轨迹

日期：2026-07-29

本次推进回答了“是否已经 fork 一个 Agent 执行自进化评测”的阶段性问题：当前已经有每轮 eval case 的 **child-agent fork 轨迹**，但它是 deterministic replay，不是真实 LLM 子 Agent。这样做的理由是先稳定接口和审计产物，再接入真实模型执行，避免直接把不稳定 LLM 回放放进 promote 门禁。

修改内容：

- 修改 `mewcode/evolution/engine.py`：`run_execution_eval()` 的 runner 从 `sandbox_deterministic` 升级为 `fork_agent_sandbox_deterministic`。
- 修改 `mewcode/evolution/engine.py`：每轮 sandbox 新增 `child_agent/input.json`、`tool_policy.json`、`transcript.md` 和 `final_answer.md`。
- 修改 `mewcode/evolution/engine.py`：`result.json` 新增 `fork_agent` 字段，保存 child-agent artifact 路径。
- 修改 `mewcode/evolution/engine.py`：Markdown 报告在每轮展示 `Child Agent` 路径。
- 修改 `tests/test_evolution.py`：新增断言，确认 runner 名称、child-agent 目录、工具策略和 transcript 路径。
- 修改 `README.md`、`docs/skill-execution-eval-gate-recap-zh.md` 和本文档：同步记录能力边界。

每轮新增结构：

```text
.mewcode/evolution/candidates/<proposal_id>/execution_sandbox/
  round_01_<case_id>/
    task.md
    SKILL.md
    rendered_prompt.md
    result.json
    child_agent/
      input.json
      tool_policy.json
      transcript.md
      final_answer.md
```

工具策略固定记录：

```json
{
  "network": "disabled",
  "write_scope": "round_sandbox_only",
  "project_write": "disabled",
  "max_retries": 1
}
```

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_creates_sandbox_artifacts -q
1 failed  # 实现前红灯：runner 仍是 sandbox_deterministic，缺少 child_agent 目录和工具策略

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_creates_sandbox_artifacts -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py -q
47 passed
```

边界说明：

- 已实现：候选 skill 的每轮 execution eval 都有独立 child-agent 输入、工具策略、transcript 和 final answer 产物。
- 已实现：child-agent 产物只写在 candidate sandbox 下，不写真实项目。
- 未实现：没有调用真实 LLM，也没有真实执行 Bash/ReadFile/EditFile 等工具。
- 未实现：还没有基于真实任务断言判断子 Agent 是否完成仓库补丁或函数实现。
- 下一步：把 `child_agent/input.json` 和 `tool_policy.json` 接入受限子 Agent 执行器，记录真实工具调用，并让 `show-eval` 展示真实执行轨迹后再提交 promote 申请。

## 15. 最新推进记录：Scripted Workspace Assertion Runner

日期：2026-07-29

本次继续把 child-agent fork 轨迹从“只写 transcript”推进到“能验证隔离工作区产物”。`eval case` 现在可以携带可选字段：

- `workspace_files`：评测开始前写入 child-agent workspace 的初始文件。
- `scripted_tool_calls`：脚本化子 Agent 要执行的 `ReadFile` / `WriteFile` 调用。
- `expected_files`：执行后必须在 workspace 中出现且内容精确匹配的文件断言。

这一步仍不调用真实 LLM，但已经开始验证“执行产物”，不再只验证 SOP 文本和 transcript 是否存在。

修改内容：

- 修改 `mewcode/evolution/engine.py`：`add_eval_case()` 支持 `workspace_files`、`scripted_tool_calls` 和 `expected_files`。
- 修改 `mewcode/evolution/engine.py`：child-agent runner 新增 `workspace/`，并在其中执行脚本化 `ReadFile` / `WriteFile`。
- 修改 `mewcode/evolution/engine.py`：所有脚本化路径必须是相对路径，且不能逃逸 workspace。
- 修改 `mewcode/evolution/engine.py`：`result.json` 的 `fork_agent` 字段新增 `workspace`、`tool_results` 和 `assertions`。
- 修改 `tests/test_evolution.py`：新增 workspace assertion 测试，验证 runner 能写出 `result.txt` 并通过 `expected_files` 精确匹配。
- 修改 `README.md` 和复盘文档：同步记录当前评测口径和边界。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_executes_scripted_workspace_assertions -q
1 failed  # 实现前红灯：add_eval_case 不支持 workspace_files / scripted_tool_calls / expected_files

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_executes_scripted_workspace_assertions -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py -q
48 passed
```

边界说明：

- 已实现：可以在隔离 workspace 中执行脚本化 `ReadFile` / `WriteFile`，并校验实际文件产物。
- 已实现：路径逃逸会被拒绝，脚本化工具调用只能落在 child-agent workspace 内。
- 未实现：脚本化 tool calls 仍由 eval case 提供，不是 LLM 自主生成。
- 未实现：还没有把真实 Agent loop 接进来，也没有真实模型根据任务自行选择工具。
- 下一步：把 `scripted_tool_calls` 替换为受限 LLM 子 Agent 的真实工具调用轨迹，并用同一套 `expected_files` 断言做判定。

## 16. 最新推进记录：Turn-Based Scripted Agent Replay

日期：2026-07-29

本次把上一阶段的一维 `scripted_tool_calls` 推进为多轮 `scripted_agent_turns`。每个 turn 可以包含 assistant 文本和该轮工具调用，runner 会按 turn 顺序执行工具、记录每轮 `tool_results`，并把这些内容写入 `transcript.md` 和 `result.json`。

新增 eval case 字段：

```json
{
  "scripted_agent_turns": [
    {
      "assistant": "先读取失败输入。",
      "tool_calls": [{"tool": "ReadFile", "path": "bug.txt"}]
    },
    {
      "assistant": "写出修复结果。",
      "tool_calls": [{"tool": "WriteFile", "path": "result.txt", "content": "fixed\n"}]
    }
  ]
}
```

修改内容：

- 修改 `mewcode/evolution/engine.py`：`add_eval_case()` 支持 `scripted_agent_turns`。
- 修改 `mewcode/evolution/engine.py`：child-agent runner 优先按 `scripted_agent_turns` 回放；没有 turns 时继续兼容 `scripted_tool_calls`。
- 修改 `mewcode/evolution/engine.py`：`result.json` 的 `fork_agent.turns` 记录每轮 assistant 文本和工具结果。
- 修改 `mewcode/evolution/engine.py`：`transcript.md` 新增 `## Agent Turns`，逐轮记录 `ToolResult`。
- 修改 `tests/test_evolution.py`：新增多轮 turn 回放测试，验证 turn 结构、工具结果和 transcript。
- 修改 `README.md` 和复盘文档：同步记录能力边界。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_replays_scripted_agent_turns -q
1 failed  # 实现前红灯：add_eval_case 不支持 scripted_agent_turns

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_replays_scripted_agent_turns -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py -q
49 passed
```

边界说明：

- 已实现：可以按多轮 Agent turn 回放工具调用，并保留 assistant 文本、tool results 和 workspace 断言。
- 已实现：该回放已经比单条 `scripted_tool_calls` 更接近真实 Agent loop 的 turn/tool-result 结构。
- 未实现：assistant 文本和工具调用仍来自 eval case，不是 LLM 自主生成。
- 下一步：把 turn 来源替换为受限 LLM 子 Agent 的真实输出，保留同一套 transcript、tool result 和 expected-files 判定接口。

## 17. 最新推进记录：Scripted LLM Agent Loop Runner

日期：2026-07-29

本次把 execution eval 从“手写函数回放 Agent turn”推进到“scripted LLM 驱动真实 `Agent.run()` 主循环”。这一步仍不调用外部模型，但它已经使用项目内真实 Agent loop、真实 `ReadFile` / `WriteFile` 工具、真实 permission checker 和真实 `ToolUseEvent` / `ToolResultEvent` 事件流。

新增 eval case 字段：

```json
{
  "execution_runner": "agent_loop_scripted",
  "scripted_agent_turns": [
    {
      "assistant": "先读取失败输入。",
      "tool_calls": [{"tool": "ReadFile", "path": "bug.txt"}]
    },
    {
      "assistant": "写出修复结果。",
      "tool_calls": [{"tool": "WriteFile", "path": "result.txt", "content": "fixed\n"}]
    },
    {
      "assistant": "修复已完成。"
    }
  ],
  "expected_files": {"result.txt": "fixed\n"}
}
```

修改内容：

- 修改 `mewcode/evolution/engine.py`：新增 `SUPPORTED_EXECUTION_RUNNERS`，`add_eval_case()` 支持 `execution_runner`，并校验 JSONL 中的 runner 值。
- 修改 `mewcode/evolution/engine.py`：新增 `_ScriptedAgentLoopClient`，把 eval case 的 scripted turns 转成 LLM stream 事件，并将相对路径重写到 `child_agent/workspace/` 的绝对路径。
- 修改 `mewcode/evolution/engine.py`：新增 `_run_agent_loop_scripted()`，创建受限 registry、workspace sandbox、permission checker，再调用真实 `Agent.run()`。
- 修改 `mewcode/evolution/engine.py`：`fork_agent.turns` 现在可记录 `events`，包括 `ToolUseEvent` 和 `ToolResultEvent`；`transcript.md` 同步展示事件流。
- 修改 `tests/test_evolution.py`：新增 `test_run_execution_eval_can_drive_agent_loop_with_scripted_llm`，验证 runner、Agent loop 标记、事件流、工具结果和 transcript。
- 修改 `README.md` 和复盘文档：同步记录当前 runner 语义、边界和下一步。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_can_drive_agent_loop_with_scripted_llm -q
1 failed  # 实现前红灯：add_eval_case 不支持 execution_runner

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_can_drive_agent_loop_with_scripted_llm -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py -q
50 passed
```

扩展验证记录：

```text
python3 -m py_compile mewcode/evolution/engine.py
无输出

git diff --check
无输出

PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py tests/test_self_evolution_benchmark.py -q
230 passed

PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突，和本次 Agent-loop scripted runner 修改无直接依赖。

边界说明：

- 已实现：`agent_loop_scripted` case 会走真实 Agent 主循环和真实工具执行，而不是 `_run_scripted_agent_turns()` 手写模拟。
- 已实现：工具路径仍被限制在 `child_agent/workspace/`，并继续使用 `expected_files` 做产物断言。
- 已实现：报告层会区分 `fork_agent_sandbox_deterministic`、`fork_agent_sandbox_scripted_agent_loop` 和 mixed runner。
- 未实现：LLM 决策仍是 scripted，不是外部模型自主规划；当前只允许 `ReadFile` / `WriteFile`，不开放 Bash。
- 下一步：把 `_ScriptedAgentLoopClient` 替换为受限真实 LLM child-agent client，在相同 sandbox、tool policy、transcript 和 expected-files 断言框架下跑多轮任务。

## 18. 最新推进记录：JSON Eval Case Command

日期：2026-07-30

本次把上一阶段 engine 已支持的高级 eval case 能力暴露到 `/evolve` 命令层。此前用户只能通过 Python API 或直接写 JSONL 添加 `workspace_files`、`scripted_agent_turns`、`expected_files` 和 `execution_runner="agent_loop_scripted"`；现在可以用 `/evolve add-eval-case-json <proposal_id> :: <json_object>` 显式录入。

设计理由：

- 基础 `/evolve add-eval-case` 适合纯文本 SOP 覆盖检查。
- `agent_loop_scripted` 需要 workspace、turns、expected files 等结构化字段，继续塞进 `::` 和 CSV 会不可读且容易出错。
- JSON 命令只负责写 eval case，不自动 `eval`、不自动 `run-eval`、不自动 `approve/promote`，因此不改变安全门禁。

修改内容：

- 修改 `mewcode/commands/handlers/evolve.py`：新增 `add-eval-case-json` 子命令、help 文案和 usage。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 JSON payload 解析、字段白名单、必填字段校验和基础类型校验。
- 修改 `tests/test_evolution.py`：新增命令层测试，验证 JSON case 能写入 `agent_loop_scripted` runner，并能跑通 execution eval。
- 修改 `README.md` 和复盘文档：记录 JSON 命令、适用场景和边界。

TDD 记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_add_eval_case_json_command_records_agent_loop_runner -q
1 failed  # 实现前红灯：命令层没有 add-eval-case-json

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_add_eval_case_json_command_records_agent_loop_runner -q
1 failed  # 第二个红灯：async 测试中嵌套 asyncio.run，暴露 Agent-loop runner 不能在已有 event loop 中同步调用

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_add_eval_case_json_command_records_agent_loop_runner -q
1 passed
```

边界说明：

- 已实现：命令层可以添加带 workspace、turns、expected files 和 `agent_loop_scripted` 的高级 eval case。
- 已实现：`run_execution_eval()` 现在可从已有 asyncio event loop 场景调用，内部会在线程中运行子 Agent event loop。
- 未实现：这仍是 scripted LLM，不是外部真实 LLM 自主生成工具调用。
- 下一步：给 JSON case 增加模板/示例输出，降低用户手写复杂 JSON 的成本。

## 19. 最新推进记录：配置驱动自进化与审批模式

日期：2026-07-30

本次根据产品边界修正上一阶段方向：自进化不再通过用户手动命令扩展。用户只负责配置开关和审批模式；候选 skill 的提取、评测用例生成、execution eval 和审批申请应由系统自动完成。

修改内容：

- 修改 `mewcode/validator.py`：新增 `self_evolution` 配置校验，支持 `enabled` 和 `skill_approval_mode`。
- 修改 `mewcode/config.py`：新增 `SelfEvolutionConfig` 并挂到 `AppConfig.self_evolution`。
- 修改 `mewcode/commands/handlers/__init__.py`：普通命令注册表不再注册 `/evolve` 和 `/learn`。
- 修改 `mewcode/commands/handlers/evolve.py`：删除 `add-eval-case-json` 命令层入口。
- 修改 `README.md`：把自进化说明改为配置驱动和用户审批驱动。
- 新增 `docs/self-evolution-config-approval-recap-zh.md`：记录本次设计修正、实现和测试。

配置示例：

```yaml
self_evolution:
  enabled: true
  skill_approval_mode: manual
```

审批模式：

- `manual`：每个通过评测的 candidate skill 单独提交审批。
- `deferred`：系统可先排队审批申请，但仍不能自动 promote。

TDD 记录：

```text
PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution -q
3 failed  # 实现前红灯：配置对象不存在，非法 approval mode 未被拒绝

PYTHONPATH=. pytest tests/test_commands.py::TestRegisterAllCommands -q
2 failed  # 实现前红灯：/evolve 和 /learn 仍注册为普通用户命令

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_add_eval_case_json_command_is_not_user_entrypoint -q
1 failed  # 实现前红灯：add-eval-case-json 仍可作为命令入口

PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution -q
3 passed

PYTHONPATH=. pytest tests/test_commands.py::TestRegisterAllCommands -q
4 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_add_eval_case_json_command_is_not_user_entrypoint -q
1 passed
```

扩展验证记录：

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

全量首个失败点仍是既有安全策略差异：`WriteFile` 写入前必须先 `ReadFile`，旧测试仍期望可直接写入，和本次配置驱动审批修改无直接关系。

边界说明：

- `EvolutionEngine.add_eval_case()` 的高级字段仍保留，供系统自动生成和执行评测使用。
- 用户不再需要手写 skill、eval case 或 JSON case。
- 不存在 `auto` 审批模式；自进化 skill 必须先通过评测并获得用户审批。
- 下一步是把 `self_evolution.enabled` 接入会话/任务结束后的自动 review 触发点，并实现审批申请队列。

## 20. 最新推进记录：自动 Review 触发点与审批申请队列

日期：2026-07-30

本次补齐配置驱动自进化后的第一段自动化闭环：当 `self_evolution.enabled=true` 时，系统会扫描已经通过静态 eval 和 execution eval 的 skill candidate，并把它们提交为 pending approval request。该流程只创建审批申请，不 approve、不 promote、不写正式 skill。

修改内容：

- 修改 `mewcode/evolution/models.py`：新增 `SkillApprovalRequest`。
- 修改 `mewcode/evolution/store.py`：新增 `.mewcode/evolution/approval_requests.jsonl` 持久化。
- 修改 `mewcode/evolution/engine.py`：新增 `submit_skill_approval_request()`，要求 proposal 为 `proposed`、target 为 `skill`、静态 eval 通过、execution eval 通过。
- 新增 `mewcode/evolution/auto_review.py`：新增 `review_ready_skill_candidates()` 和 `format_review_notification()`。
- 修改 `mewcode/app.py`：TUI 每轮 `LoopComplete` 后触发自动 review，有申请时展示提示。
- 修改 `mewcode/__main__.py`：非交互 `mewcode -p` 执行结束和 teammate notification 续跑结束后触发自动 review，有申请时输出到 stderr。
- 修改 `tests/test_evolution.py`：新增审批申请、execution eval 门禁、配置关闭跳过和幂等扫描测试。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录本次变更。

TDD 记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_records_pending_request -q
1 failed  # 实现前红灯：EvolutionEngine 尚无 submit_skill_approval_request

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_requires_execution_eval -q
1 failed  # 实现前红灯：缺少审批申请 API

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_disabled_skips_ready_candidates tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_ready_candidates_once -q
2 failed  # 实现前红灯：缺少 mewcode.evolution.auto_review

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_records_pending_request tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_requires_execution_eval tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_disabled_skips_ready_candidates tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_ready_candidates_once -q
4 passed
```

扩展验证记录：

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

边界说明：

- 已实现：ready candidate 会生成 pending approval request，并把 request id 写回 candidate manifest。
- 已实现：重复扫描不会重复创建 pending request。
- 已实现：配置关闭时自动 review 不写任何文件。
- 未实现：还没有真正的审批 UI，当前只是持久化申请并提示用户。
- 未实现：还没有自动从对话轨迹蒸馏 candidate skill；当前扫描的是已有 candidate。
- 下一步：实现自动候选 skill 抽取和审批视图。

## 21. 最新推进记录：审批申请 Resolve 状态机

日期：2026-07-30

本次补齐 pending approval request 的后半段状态机。上一阶段只能把 ready candidate skill 排入审批队列；现在系统内部已经能处理用户批准或拒绝，并把结果同步写回审批记录和 candidate manifest。

修改内容：

- 修改 `mewcode/evolution/models.py`：`SkillApprovalRequest` 新增 `resolved_at`、`reviewer`、`resolution_reason` 和 `result_path`。
- 修改 `mewcode/evolution/store.py`：新增 `get_skill_approval_request()` 和 `update_skill_approval_request()`，支持按 request id 查询和覆盖更新。
- 修改 `mewcode/evolution/engine.py`：新增 `resolve_skill_approval_request()`。批准时执行 `approve -> promote`，拒绝时执行 `reject`，两条路径都会更新审批状态。
- 修改 `mewcode/evolution/engine.py`：新增 `_mark_candidate_approval_resolved()`，把 `approval_status`、处理人、理由、结果路径写回 candidate manifest。
- 修改 `tests/test_evolution.py`：新增批准后正式 skill 落地、拒绝后正式 skill 不落地的行为测试。
- 修改 `docs/self-evolution-config-approval-recap-zh.md` 和本文档：记录本次 TDD、实现边界和验证结果。

TDD 记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_resolve_skill_approval_request_approved_promotes_candidate -q
1 failed  # 实现前红灯：EvolutionEngine 尚无 resolve_skill_approval_request

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_resolve_skill_approval_request_rejected_rejects_without_promote -q
1 failed  # 实现前红灯：无法拒绝审批并阻止正式 skill 落地

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_resolve_skill_approval_request_approved_promotes_candidate -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_resolve_skill_approval_request_rejected_rejects_without_promote -q
1 passed
```

验证记录：

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

边界说明：

- 已实现：审批记录可以从 `pending` 转为 `approved` 或 `rejected`。
- 已实现：批准后才 promote 到 `.mewcode/skills/<name>/SKILL.md`。
- 已实现：拒绝不会写正式 skill，并会把 proposal 标记为 `rejected`。
- 已实现：candidate manifest 会记录审批结果，便于后续 UI 展示和审计。
- 未实现：用户可见审批视图/API；当前能力仍是 Engine 内部状态机。
- 下一步：实现审批列表与详情展示，展示 candidate diff、execution eval 报告、测试结果和批准/拒绝入口。

## 22. 最新推进记录：只读审批详情 API

日期：2026-07-30

本次继续推进审批可见性，但仍不新增用户命令入口。系统内部现在可以把单个 approval request 渲染为 Markdown 审阅材料，后续 TUI/API 可以直接展示该材料，再调用上一阶段的 `resolve_skill_approval_request()` 处理批准或拒绝。

修改内容：

- 修改 `mewcode/evolution/engine.py`：新增 `render_skill_approval_request(request_id)`。
- 该方法读取 `approval_requests.jsonl` 中的 request，定位 proposal，并复用现有 `_render_skill_preview()` 生成 candidate diff。
- 该方法读取 request 绑定的 execution eval Markdown 报告，并与 request 元数据一起组成审阅文档。
- 修改 `tests/test_evolution.py`：新增 `test_render_skill_approval_request_shows_review_materials`，验证审批详情包含 request id、状态、skill 名称、candidate diff 和 execution eval 报告。
- 修改 `docs/self-evolution-config-approval-recap-zh.md` 和本文档：记录本次 TDD 与验证结果。

TDD 记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_review_materials -q
1 failed  # 实现前红灯：EvolutionEngine 尚无 render_skill_approval_request

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_review_materials -q
1 passed
```

验证记录：

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

边界说明：

- 已实现：审批详情能展示候选 skill diff 和 execution eval 报告。
- 已实现：审批详情只读，不会修改 request/proposal/candidate/formal skill。
- 已实现：没有新增 `/evolve` 或其他用户命令，符合“用户只配置开关与审批模式”的边界。
- 未实现：审批列表和用户可见 approve/reject 入口。
- 下一步：实现审批 inbox 查询能力，让 UI/API 可以按 pending/approved/rejected 列出 request，再进入详情与 resolve。

## 23. 最新推进记录：审批 Inbox 查询 API

日期：2026-07-30

本次补齐审批详情之前的列表入口。系统内部现在可以按状态列出 approval request，默认只返回待处理的 pending request；审计视图可以传入 `status=None` 查看全部已批准、已拒绝和待处理记录。

修改内容：

- 修改 `mewcode/evolution/engine.py`：新增 `list_skill_approval_inbox(status="pending")`。
- 默认行为只返回 `pending`，避免用户审批界面默认混入已处理申请。
- `status=None` 返回全部 request，用于审计或历史视图。
- 返回结果按 `created_at` 排序，保持审批队列展示稳定。
- 修改 `tests/test_evolution.py`：新增 `test_list_skill_approval_inbox_defaults_to_pending_requests`，验证默认 pending、拒绝后从 pending 消失、`status=None` 仍可审计查看。
- 修改 `docs/self-evolution-config-approval-recap-zh.md` 和本文档：记录本次 TDD 与验证结果。

TDD 记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_list_skill_approval_inbox_defaults_to_pending_requests -q
1 failed  # 实现前红灯：EvolutionEngine 尚无 list_skill_approval_inbox

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_list_skill_approval_inbox_defaults_to_pending_requests -q
1 passed
```

验证记录：

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

边界说明：

- 已实现：内部审批 inbox 可以列出 pending 或全部 request。
- 已实现：审批 inbox 只读，不会修改 request/proposal/candidate/formal skill。
- 已实现：没有新增用户命令，不要求用户通过命令提交 skill 或评测。
- 未实现：用户可见审批入口和审批模式差异化展示。
- 下一步：把 inbox、详情和 resolve 串成真正的 UI/API 审批面。

## 24. 最新推进记录：TUI 自进化审批入口

日期：2026-07-31

本次把前几阶段的 inbox、详情和 resolve 串进 TUI。自进化 review 发现 ready candidate 后，不再只是打印提示，而是打开内联审批组件；如果已有 pending request，下次 review 也会重新打开待审批入口，避免审批申请沉默地停留在 JSONL 中。

修改内容：

- 新增 `mewcode/self_evolution_dialog.py`：实现 `InlineSkillApprovalWidget` 和 `SkillApprovalChoice`。
- 修改 `mewcode/app.py`：`_run_self_evolution_review()` 在发现新 request 或已有 pending request 时打开审批组件。
- 修改 `mewcode/app.py`：新增 `_show_self_evolution_approval()` 和 `_mount_self_evolution_approval()`，展示 `render_skill_approval_request()` 生成的审阅材料。
- 修改 `mewcode/app.py`：新增 `on_inline_skill_approval_widget_responded()`，批准时调用 `resolve_skill_approval_request(... approved=True)` 并 reload skill catalog。
- 新增 `tests/test_self_evolution_dialog.py`：覆盖审批组件展示和 approve/reject 事件。
- 修改 `tests/test_evolution.py`：覆盖新申请打开、已有 pending request 打开、批准后 promote 并 reload skill catalog。
- 修改 `docs/self-evolution-config-approval-recap-zh.md` 和本文档：记录本次 TDD 与验证结果。

TDD 记录：

```text
PYTHONPATH=. pytest tests/test_self_evolution_dialog.py -q
1 error  # 实现前红灯：缺少 mewcode.self_evolution_dialog

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_approval_widget -q
1 failed  # 实现前红灯：TUI review 只提示，不打开审批 widget

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills -q
1 failed  # 实现前红灯：MewCodeApp 尚无审批响应处理

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_existing_pending_request -q
1 failed  # 实现前红灯：已有 pending request 被静默跳过

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_approval_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_existing_pending_request tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills -q
5 passed
```

验证记录：

```text
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

边界说明：

- 已实现：TUI 内联审批入口基础版。
- 已实现：用户批准后才 promote candidate skill，并 reload skill catalog。
- 已实现：没有新增 `/evolve` 或其他用户命令，仍符合“用户只配置开关与审批模式”的边界。
- 未实现：审批拒绝路径的 TUI 集成测试、manual/deferred 的差异化展示、批量审批队列视图。
- 下一步：补拒绝路径 UI 测试，并把审批模式差异展示到组件文案中。

## 25. 最新推进记录：自动 Usage Patch Candidate 生成

日期：2026-07-31

本次开始把自进化从“已有 ready candidate 自动提交审批”推进到“系统能从真实使用记录中自动生成候选 skill patch”。当自进化开启后，`auto_review` 会扫描 skill usage log；如果某个项目级正式 skill 累计达到负向 usage 阈值，就基于这些失败或用户纠正记录生成一个隔离的 patch proposal。

修改内容：

- 修改 `mewcode/evolution/auto_review.py`：`review_ready_skill_candidates()` 返回值新增 `generated_candidates`，关闭自进化时也保持稳定结构。
- 修改 `mewcode/evolution/auto_review.py`：新增 usage-driven patch candidate 生成逻辑，复用 `suggest_quarantine()` 和 `propose_skill_patch_from_usage()`。
- 修改 `mewcode/evolution/auto_review.py`：新增开放 patch candidate 去重检查，避免同一个 skill 因同一批 usage 反复生成 proposal。
- 修改 `tests/test_evolution.py`：新增自动生成 usage patch candidate 的行为测试。
- 修改 `tests/test_evolution.py`：新增幂等性测试，确认已有 open patch candidate 时不会重复生成。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录本次边界、验证结果和下一步计划。

当前链路：

```text
skill usage failure/user_feedback
-> auto_review 扫描负向 usage
-> suggest_quarantine 达到阈值
-> propose_skill_patch_from_usage 生成 patch proposal
-> 等待后续自动 eval case / execution eval / approval request
```

关键边界：

- 已实现：自动从负向 usage 生成候选 skill patch proposal。
- 已实现：生成的是 candidate/proposal，不会直接写正式 `.mewcode/skills/<name>/SKILL.md`。
- 已实现：已有同名 open patch proposal 时不会重复生成。
- 已实现：不会跳过 eval、execution eval 或用户审批。
- 未实现：自动为该 patch proposal 生成 eval case、运行 execution eval、提交 approval request。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_creates_usage_patch_candidate -q
1 failed  # 实现前红灯：auto_review 返回值缺少 generated_candidates

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_creates_usage_patch_candidate -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_does_not_duplicate_usage_patch_candidate -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_creates_usage_patch_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_submits_ready_candidates_once -q
2 passed

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

下一步计划：

- 为自动生成的 patch proposal 自动生成候选 eval case，但仍保持只读建议或门禁写入策略。
- 将 patch proposal 进入 execution eval 前的材料展示给用户，避免“候选已生成但不可见”。
- 扩展 usage 来源，从显式 skill usage log 扩展到对话轨迹、工具失败、用户纠正和复盘文档缺失等更完整任务经验。

## 26. 最新推进记录：自动返回候选 Eval 建议摘要

日期：2026-07-31

本次把阶段 25 生成的 usage-driven patch proposal 往评测门禁推进了一步。自动 review 现在不仅返回新生成的 proposal id，还会立刻生成只读 eval case suggestion review，并把质量统计、coverage 统计、warnings、recommendation 和具体 suggestions 返回给调用方。

修改内容：

- 修改 `mewcode/evolution/auto_review.py`：关闭自进化时返回 `generated_candidate_reviews: []`，保持结果结构稳定。
- 修改 `mewcode/evolution/auto_review.py`：`_generate_usage_patch_candidates()` 返回 `(generated_ids, reviews)`，每个新生成的 patch proposal 会调用 `review_eval_case_suggestions()`。
- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_returns_eval_suggestions_for_generated_patch`，验证自动返回 eval 建议摘要。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录本次切片和验证结果。

当前链路：

```text
skill usage failure/user_feedback
-> auto_review 生成 patch proposal
-> review_eval_case_suggestions 生成只读 eval 建议摘要
-> 返回 generated_candidate_reviews 给 UI/后续自动门禁
-> 仍不写 eval case，不跑 execution eval，不提交审批
```

关键边界：

- 已实现：调用方能看到自动生成 candidate 对应的 eval 建议效果。
- 已实现：eval 建议摘要覆盖 usage feedback 的 quality、coverage 和 warnings。
- 已实现：该阶段不落盘写 eval case，避免未审核建议直接进入门禁。
- 未实现：把高质量建议自动转成正式 eval case、运行 execution eval、提交 approval request。

TDD 与验证记录：

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

下一步计划：

- 增加一个受控门禁：只有 high-quality 且覆盖全部 usage feedback 的建议，才允许自动写入 eval case。
- 自动写入 eval case 后触发 deterministic eval，继续保持 execution eval 和用户审批强制门禁。
- 在 TUI 审批或 review 提示中展示 `generated_candidate_reviews`，让用户看到“系统为什么认为这个 skill 应该进化”。

## 27. 最新推进记录：受控物化 Eval Case

日期：2026-07-31

本次把阶段 26 的只读 eval 建议摘要推进为受控写入。自动 review 在生成 usage-driven patch proposal 后，会先评估建议质量；只有建议没有 warnings、没有 uncovered usage feedback、数量达到 `MIN_EXECUTION_EVAL_CASES=3`、且不存在 low-quality suggestion 时，才会把 suggestions 写入正式 eval case 文件。

修改内容：

- 修改 `mewcode/evolution/auto_review.py`：新增 `generated_eval_cases` 返回字段。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_materialize_safe_eval_suggestions()`，负责把安全建议写入 eval cases。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_review_is_safe_to_materialize()`，集中执行 warnings、coverage、数量和质量门禁。
- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_materializes_safe_eval_suggestions`，验证安全建议会写入 3 条 eval case。
- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_does_not_materialize_uncovered_eval_suggestions`，验证覆盖不足时不写入 eval case。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录本次边界和验证结果。

当前链路：

```text
skill usage failure/user_feedback
-> auto_review 生成 patch proposal
-> review_eval_case_suggestions 生成建议摘要
-> gate: no warnings + no uncovered feedback + >=3 cases + no low quality
-> add_eval_case 写入 eval case
-> 仍不跑 execution eval，不提交审批，不 promote
```

关键边界：

- 已实现：自动生成 candidate 后可自动写入合格 eval case。
- 已实现：覆盖不足或 warning 存在时不会写入 eval case。
- 已实现：该阶段仍不会提交 approval request，避免未通过 execution eval 的 skill 出现在审批面。
- 未实现：自动触发 deterministic eval、execution eval 和审批申请。

TDD 与验证记录：

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

下一步计划：

- 在 eval case 自动写入后触发 deterministic eval，并把 eval 结果写入 auto review 返回值。
- deterministic eval 通过后再触发 execution eval，满足“候选 skill 正确执行多轮任务后才提交审批”。
- 扩展 TUI 展示，让用户看到 candidate、eval case、eval report 和 execution eval report 的完整证据链。

## 28. 最新推进记录：自动 Deterministic Eval

日期：2026-07-31

本次把阶段 27 写入的 eval cases 接到 deterministic eval。自动 review 在成功物化 eval case 后，会立即调用 `EvolutionEngine.evaluate()`，并将结果通过 `generated_evaluations` 返回给调用方。该阶段仍不运行 execution eval，也不提交 approval request。

修改内容：

- 修改 `mewcode/evolution/auto_review.py`：关闭自进化时返回 `generated_evaluations: []`。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_evaluate_generated_candidates()`，对刚刚写入 eval cases 的 proposal 执行 deterministic eval。
- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_runs_eval_after_materializing_cases`，验证 eval 自动通过、manifest 写入 `eval_status=passed`，且没有 execution eval report。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录本次边界和验证结果。

当前链路：

```text
skill usage failure/user_feedback
-> auto_review 生成 patch proposal
-> gate 后写入 eval case
-> evaluate() 跑 deterministic eval
-> 写 candidate manifest eval_status
-> 仍不跑 execution eval，不提交审批，不 promote
```

关键边界：

- 已实现：自动生成 candidate 后可自动完成 deterministic eval。
- 已实现：eval 结果会写回 candidate manifest，并通过 `generated_evaluations` 返回。
- 已实现：该阶段不会产生 execution eval report，也不会进入审批队列。
- 未实现：自动运行 execution eval、根据通过结果提交 approval request。

TDD 与验证记录：

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

下一步计划：

- 在 deterministic eval 通过后触发 execution eval，生成用户可见 JSON/Markdown 报告。
- execution eval 通过后再提交 approval request，让用户审批时看到完整测试证据。
- 若 execution eval 失败，保留 candidate 和报告，但不进入审批队列。

## 29. 最新推进记录：自动 Execution Eval

日期：2026-07-31

本次把自动 deterministic eval 继续推进到 execution eval。自动 review 只会对 deterministic eval 通过的 generated candidate 调用 `run_execution_eval()`，生成用户可见 JSON/Markdown execution eval 报告，并通过 `generated_execution_evals` 返回结果。

修改内容：

- 修改 `mewcode/evolution/auto_review.py`：关闭自进化时返回 `generated_execution_evals: []`。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_run_execution_evals_for_generated_candidates()`，只对 `ok=True` 的 deterministic eval 结果运行 execution eval。
- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_runs_execution_eval_after_eval_passes`，验证 execution eval 自动通过、报告写入、manifest 写入 `execution_eval_status=passed`。
- 修改 `tests/test_evolution.py`：更新 deterministic eval 测试，让 execution report 由新测试负责验证。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录本次边界和验证结果。

当前链路：

```text
skill usage failure/user_feedback
-> auto_review 生成 patch proposal
-> gate 后写入 eval case
-> evaluate() 跑 deterministic eval
-> run_execution_eval() 跑多轮 execution eval
-> 写 eval_report.json / eval_report.md
-> 仍不提交审批，不 promote
```

关键边界：

- 已实现：candidate skill 能自动完成多轮 execution eval。
- 已实现：execution eval 报告已可作为后续用户审批证据。
- 已实现：deterministic eval 失败的 candidate 不会进入 execution eval。
- 未实现：execution eval 通过后自动提交 approval request。

TDD 与验证记录：

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

下一步计划：

- execution eval 通过后自动创建 approval request，并把 request id 返回给调用方。
- TUI review 需要能直接展示这类新创建 request 的 candidate diff 和 execution eval 报告。
- approval request 仍必须等待用户 approve/reject，不能自动 promote。

## 30. 最新推进记录：自动提交 Approval Request

日期：2026-07-31

本次完成 usage-driven candidate 的自动闭环：当自动生成的 patch candidate 通过 eval case 物化、deterministic eval 和 execution eval 后，auto review 会立即调用 `submit_skill_approval_request()` 创建 pending approval request，并把 request 放入返回值 `requests`。TUI 现有审批入口可以直接展示该 request。

修改内容：

- 修改 `mewcode/evolution/auto_review.py`：新增 `_submit_generated_approval_requests()`，只对 execution eval 成功的 candidate 创建 approval request。
- 修改 `mewcode/evolution/auto_review.py`：自动生成路径创建的 request 复用 `requests` 返回字段，确保 TUI 现有入口可直接打开审批。
- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_submits_generated_candidate_after_execution_eval`，验证 approval mode 会沿用配置。
- 修改 `tests/test_evolution.py`：更新此前阶段性断言，把“无 request”改为“request 关联到当前 proposal”。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录本次完整闭环和验证结果。

当前链路：

```text
skill usage failure/user_feedback
-> auto_review 生成 patch proposal
-> gate 后写入 eval case
-> deterministic eval
-> execution eval
-> submit_skill_approval_request
-> TUI/pending inbox 等待用户 approve/reject
-> 用户 approve 后才 promote
```

关键边界：

- 已实现：自动生成的 candidate 能在同一轮 review 内进入 pending approval request。
- 已实现：approval request 继承配置的 `skill_approval_mode`。
- 已实现：不会自动 approve、不会自动 promote。
- 已实现：coverage 不足、eval case 未物化、deterministic eval 失败或 execution eval 失败时，不会进入审批队列。

TDD 与验证记录：

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

下一步计划：

- 扩展自动候选来源，从显式 skill usage log 扩展到对话轨迹、工具错误、用户纠正和复盘遗漏。
- 增强 TUI 审批展示，让用户能同时看到 usage feedback、eval cases、deterministic eval 和 execution eval 报告。
- 补齐审批拒绝路径和 `manual/deferred` 文案差异测试，但继续禁止自动 promote。

## 31. 最新推进记录：Evidence 自动归因到 Skill Usage

日期：2026-07-31

本次开始扩展候选 skill 的来源，不再只依赖显式 `skill_usage.jsonl`。自动 review 现在会扫描 evolution evidence；当 evidence 是 `failure` 或 `user_feedback`，并且 metadata 明确标注 `skill_name` 或 `skill`，且该 skill 是项目级正式 skill 时，会自动转成负向 skill usage。后续沿用已经完成的 patch candidate、eval、execution eval 和 approval request 闭环。

修改内容：

- 修改 `mewcode/evolution/auto_review.py`：新增 `ingested_usage` 返回字段。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_ingest_evidence_as_skill_usage()`，把结构化 evidence 转成 usage。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_skill_usage_evidence_ids()`，通过 `metadata.evidence_id` 做幂等去重。
- 修改 `mewcode/evolution/auto_review.py`：跳过 `source=skill-usage` 的内部 evidence，避免系统把自己生成 patch 的证据再次摄入，形成自反馈循环。
- 修改 `tests/test_evolution.py`：新增 evidence 自动摄入测试和重复 review 不重复摄入测试。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录本次来源扩展和验证结果。

当前链路：

```text
structured evidence failure/user_feedback + metadata.skill_name
-> auto_review ingests as skill usage
-> usage threshold
-> patch proposal
-> eval cases
-> deterministic eval
-> execution eval
-> pending approval request
```

关键边界：

- 已实现：对话/工具/用户纠正只要以 evidence 形式记录且明确标注 skill，就能进入自进化闭环。
- 已实现：不从自由文本猜 skill，避免错误归因。
- 已实现：同一个 evidence 只摄入一次。
- 已实现：跳过系统内部 `skill-usage` evidence，避免自反馈放大。
- 未实现：从原始 conversation/tool trace 中自动抽取结构化 evidence。

TDD 与验证记录：

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

下一步计划：

- 实现从 agent/tool 运行结果自动记录结构化 evidence，优先覆盖工具失败和用户纠正。
- 为 evidence ingestion 增加来源统计，让审批页能展示 candidate 来自哪些任务证据。
- 保持自动 promote 禁止不变，所有生成 skill 仍必须经用户审批。

## 32. 最新推进记录：工具失败自动记录 Evidence

日期：2026-07-31

本次把结构化 evidence 的来源继续前移到 Agent 工具执行层。Agent 在工具执行失败时，如果当前只有一个 active skill，会自动记录 `failure` evidence，并在 metadata 中写入 `skill_name`、`tool_name`、`tool_args` 和摘要。下一轮 auto review 会把该 evidence 摄入为 skill usage，再进入阶段 31 已完成的自进化闭环。

修改内容：

- 修改 `mewcode/agent.py`：新增 `_record_tool_failure_evidence()`。
- 修改 `mewcode/agent.py`：在普通工具执行和并发工具执行结果处调用 evidence 记录逻辑。
- 修改 `tests/test_agent.py`：新增唯一 active skill 时工具失败会记录 evidence 的测试。
- 修改 `tests/test_agent.py`：新增多个 active skill 时不记录 evidence 的测试，避免误归因。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录本次来源扩展和验证结果。

当前链路：

```text
single active skill + tool failure
-> Agent records structured failure evidence
-> auto_review ingests evidence as skill usage
-> usage threshold
-> patch candidate / eval / execution eval
-> pending approval request
```

关键边界：

- 已实现：真实工具失败可以自动形成结构化 evolution evidence。
- 已实现：只有一个 active skill 时才归因，多个 active skill 时跳过。
- 已实现：记录 evidence 不会自动生成 skill；仍要等 auto review、评测和用户审批。
- 未实现：用户自然语言纠正自动抽取为 evidence。

TDD 与验证记录：

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

`test_message_splicing` 和 `test_multi_step_autonomous` 都是既有 agent 测试口径差异，前者期望消息数量为 5 但当前序列化结果为 4，后者期望未读先写成功但当前安全策略要求先 `ReadFile`，均和本次工具失败 evidence 记录无直接关系。

下一步计划：

- 自动捕获用户纠正文案，生成 `user_feedback` evidence。
- 为审批页展示 evidence 来源：tool-result、conversation、manual、skill-usage。
- 继续保持用户审批为最终启用门禁。

## 33. 最新推进记录：用户纠正自动记录 Evidence

日期：2026-08-01

本次把用户自然语言纠正接入结构化 evidence 来源。Agent 在运行开始时扫描用户消息；如果当前只有一个 active skill，且消息包含明确纠正标记，例如“用户纠正”“不对”“遗漏”“你刚才”等，会自动记录 `user_feedback` evidence，并写入 `skill_name`、`summary` 和 `message_hash`。后续 auto review 会把该 evidence 摄入为 skill usage，再进入现有自进化闭环。

修改内容：

- 修改 `mewcode/agent.py`：新增 `_record_user_feedback_evidence()`，在 `run()` 开头扫描用户纠正文案。
- 修改 `mewcode/agent.py`：新增 `_looks_like_user_correction()` 和 `_feedback_message_hash()`，用于保守识别和去重。
- 修改 `mewcode/agent.py`：新增 `_feedback_evidence_exists()`，避免新 Agent 实例重复记录相同用户纠正。
- 修改 `tests/test_agent.py`：新增单 active skill 时用户纠正记录 evidence 的测试。
- 修改 `tests/test_agent.py`：新增多 active skill 时不记录 evidence 的测试。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录本次用户反馈来源扩展。

当前链路：

```text
single active skill + user correction message
-> Agent records structured user_feedback evidence
-> auto_review ingests evidence as skill usage
-> usage threshold
-> patch candidate / eval / execution eval
-> pending approval request
```

关键边界：

- 已实现：用户纠正文案可以自动沉淀为 `user_feedback` evidence。
- 已实现：只有一个 active skill 时才自动归因，多个 active skill 时跳过。
- 已实现：使用 message hash 去重，避免同一纠正反复写 evidence。
- 已实现：记录 evidence 不等于启用 skill；仍必须经过评测和用户审批。
- 未实现：从多 active skill 场景中通过显式用户指代做安全归因。

TDD 与验证记录：

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

下一步计划：

- 为审批页展示 evidence 来源：tool-result、conversation、manual、skill-usage。
- 增加用户可见的 evidence summary，让审批时能看到“为什么生成这个 skill patch”。
- 继续保持用户审批为最终启用门禁。

## 34. 最新推进记录：自进化 Fork Reviewer 审计运行

日期：2026-08-01

本次把自进化自动 review 从“主流程里直接执行一个确定性 pipeline”推进为“每次 enabled review 都生成一个可审计的 fork reviewer run”。它不是完整 Hermes 的真实后台 LLM reviewer，但已经把自进化审查隔离成独立运行记录，拥有自己的输入快照、能力策略、输出摘要和 Markdown 报告。

修改内容：

- 修改 `mewcode/evolution/models.py`：新增 `SelfEvolutionReviewRun`，记录 `mode`、`status`、`approval_mode`、`artifacts`、`policy`、`summary`、`error`。
- 修改 `mewcode/evolution/store.py`：新增 `review_runs.jsonl` 的保存、读取和更新方法。
- 修改 `mewcode/evolution/auto_review.py`：enabled review 开始时创建 `fork_reviewer` run，写入 `input.json` 和 `policy.json`。
- 修改 `mewcode/evolution/auto_review.py`：review 完成后写入 `output.json` 和 `report.md`，并把 `review_run` 返回给调用方。
- 修改 `tests/test_evolution.py`：新增 fork reviewer run 持久化、策略和产物测试。
- 修改本文档：记录本次 fork reviewer 审计机制、测试结果和下一步。

当前链路：

```text
self_evolution.enabled = true
-> create fork_reviewer run
-> write input snapshot + policy
-> run existing evidence/candidate/eval/execution-eval/approval pipeline
-> write output summary + report
-> return review_run to caller
```

能力边界：

- 已实现：每次 enabled auto review 都有独立 `review_runs/<run_id>/` 产物。
- 已实现：policy 明确记录 `can_approve=false`、`can_promote=false`、`project_write=disabled`。
- 已实现：run summary 记录 approval request、generated candidate、eval case、execution eval 和 ingested usage。
- 已实现：disabled self-evolution 不创建 review run。
- 未实现：真实后台 LLM reviewer 自主读取 trace 并生成候选 skill。
- 未实现：reviewer 作为独立进程/任务异步运行；当前仍是同步逻辑 fork。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_fork_reviewer_run -q
1 failed  # 实现前红灯：EvolutionStore 尚无 load_self_evolution_review_runs()

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_fork_reviewer_run -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py -q -k "self_evolution_review"
15 passed, 58 deselected
```

下一步计划：

- 把审批页接入 `review_run.report.md`，让用户审批时看到 fork reviewer 证据链。
- 扩展 fork reviewer run 的输入快照，纳入触发 evidence 的摘要和来源。
- 后续再把同步逻辑 fork 替换为受限真实 LLM child reviewer，但仍禁止 approve/promote。

## 35. 最新推进记录：审批页展示 Fork Reviewer 证据链

日期：2026-08-01

本次把上一阶段生成的 `fork_reviewer` 运行报告接入 skill 审批详情。现在通过自动 review 生成的 approval request，在 `render_skill_approval_request()` 中会额外展示 `## Fork Reviewer Evidence`，用户可以在审批前看到 fork reviewer 的运行模式、状态、能力边界和是否允许 promote。

修改内容：

- 修改 `mewcode/evolution/engine.py`：审批详情渲染时查找包含当前 proposal id 的最新 `SelfEvolutionReviewRun`。
- 修改 `mewcode/evolution/engine.py`：新增 `_render_fork_reviewer_evidence()`，优先读取 `review_runs/<run_id>/report.md`，报告缺失时回退到 run metadata。
- 修改 `tests/test_evolution.py`：新增审批详情展示 fork reviewer evidence 的回归测试。
- 修改本文档：记录审批证据链展示和验证结果。

当前链路：

```text
auto review creates approval request
-> fork reviewer run summary contains proposal id
-> approval renderer finds latest matching run
-> approval markdown includes Fork Reviewer Evidence
-> user sees review policy before approve/reject
```

能力边界：

- 已实现：自动 review 生成的审批请求会展示 fork reviewer 报告。
- 已实现：直接手动提交的审批请求没有匹配 run 时，不强行展示空证据区。
- 已实现：报告中明确显示 `Can promote: False`，避免用户误以为后台 reviewer 可自行启用 skill。
- 未实现：审批页还没有展开原始 evidence 摘要和来源明细。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_fork_reviewer_evidence -q
1 failed  # 实现前红灯：审批详情缺少 Fork Reviewer Evidence

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_fork_reviewer_evidence -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py -q -k "render_skill_approval_request or self_evolution_review"
17 passed, 57 deselected
```

下一步计划：

- 在 fork reviewer input/report 中加入触发 evidence 的摘要、source 和 metadata。
- 审批页进一步展示 candidate 生成原因、eval case 列表和 execution eval 每轮摘要。
- 后续再推进真实 LLM child reviewer，但保持只生成候选和审批申请，不允许 promote。

## 36. 最新推进记录：Fork Reviewer 报告展示 Usage Evidence

日期：2026-08-01

本次把 fork reviewer 报告从“只展示运行策略和数量统计”推进为“展示审批请求背后的 evidence”。自动 usage patch candidate 会通过 `proposal.evidence_ids` 关联 `source=skill-usage` 的 evidence；现在 fork reviewer `output.json` 和 `report.md` 会按 proposal 展示 evidence id、kind、source、summary，以及 metadata 中的 usage summaries。

修改内容：

- 修改 `mewcode/evolution/auto_review.py`：`_review_run_summary()` 接收 `EvolutionEngine`，为每个 request proposal 收集 linked evidence。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_proposal_evidence_details()`，从 `proposal.evidence_ids` 读取 evidence 明细。
- 修改 `mewcode/evolution/auto_review.py`：`_render_fork_reviewer_report()` 新增 `## Request Evidence` 区块。
- 修改 `tests/test_evolution.py`：新增 usage evidence 出现在 fork reviewer report 中的回归测试。
- 修改本文档：记录 evidence 展示逻辑和验证结果。

当前链路：

```text
negative skill usage
-> propose_skill_patch_from_usage()
-> record evidence source=skill-usage
-> proposal.evidence_ids links evidence
-> fork reviewer summary resolves evidence
-> report.md shows source and usage summaries
-> approval page includes the same report
```

能力边界：

- 已实现：generated skill patch 审批报告能看到 `source=skill-usage`。
- 已实现：usage summary 会展示具体用户纠正或失败摘要。
- 已实现：报告数据来自持久化 proposal/evidence，不依赖运行期临时变量。
- 未实现：还没有把 tool-result、conversation、manual evidence 分组做成更适合 UI 的摘要卡片。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_report_includes_usage_evidence -q
1 failed  # 实现前红灯：fork reviewer report 缺少 Request Evidence

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_report_includes_usage_evidence -q
1 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_fork_reviewer_evidence tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_report_includes_usage_evidence -q
2 passed
```

下一步计划：

- 把 `Request Evidence` 进一步结构化给 TUI 审批组件，避免用户只看长 Markdown。
- 在 review report 中补充 eval case 与 execution eval 的逐轮摘要。
- 后续再实现真实 LLM child reviewer 的只读/候选生成模式。

## 37. 最新推进记录：Trusted-Auto 策略化自进化模式

日期：2026-08-01

本次回应“是否必须每次都用户审批”的设计问题，把审批机制从固定人工门禁扩展为策略化自治模式。新增 `self_evolution.skill_approval_mode: trusted-auto`，允许系统对“本轮自动 review 新生成、且已经通过 deterministic eval 与 execution eval 的 candidate skill”自动完成 approval request resolve 和 promote。

修改内容：

- 修改 `mewcode/validator.py`：`VALID_SELF_EVOLUTION_APPROVAL_MODES` 新增 `trusted-auto`。
- 修改 `mewcode/config.py`：`SelfEvolutionConfig.requires_user_approval` 对 `trusted-auto` 返回 `False`。
- 修改 `mewcode/evolution/auto_review.py`：自动 review result 新增 `auto_promotions`。
- 修改 `mewcode/evolution/auto_review.py`：`trusted-auto` 只在 generated candidate submission 路径中调用 `resolve_skill_approval_request(... approved=True ...)`。
- 修改 `mewcode/evolution/auto_review.py`：fork reviewer summary/report 展示 auto promotion 数量、proposal、request 和执行结果。
- 修改 `tests/test_mcp.py`：覆盖配置文件接受 `trusted-auto`。
- 修改 `tests/test_evolution.py`：覆盖 generated candidate 自动 promote，以及 existing ready candidate 不被静默 promote。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：记录新的策略边界。

当前链路：

```text
skill_approval_mode = trusted-auto
-> auto review generates candidate from usage evidence
-> safe eval suggestions materialized
-> deterministic eval passed
-> execution eval passed
-> submit approval request for audit
-> self-evolution-policy resolves approved
-> promote candidate skill
-> report auto_promotions
```

安全边界：

- 已实现：只有本轮 auto review 生成并完成全部评测的 candidate 会 trusted-auto promote。
- 已实现：已有 ready candidate 即使配置为 `trusted-auto`，仍只进入 pending request，不会静默启用。
- 已实现：自动启用仍会留下 approval request、reviewer=`self-evolution-policy`、resolution reason 和 candidate manifest。
- 已实现：fork reviewer report 记录 auto promotion 结果。
- 未实现：真实任务 canary 与自动回滚；当前 trusted-auto 仍基于现有 eval/execution eval gate。
- 未实现：用户可配置更细的 risk policy，例如只允许 patch 不允许 create、只允许低风险工具集。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_use_trusted_auto_approval tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_promotes_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_keeps_existing_ready_candidate_pending -q
3 failed  # 实现前红灯：模式未被接受，auto_promotions 不存在，trusted-auto ready candidate 被跳过

PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_use_trusted_auto_approval tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_promotes_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_keeps_existing_ready_candidate_pending -q
3 passed
```

下一步计划：

- 增加 canary skill 使用模式：candidate 只在匹配任务中临时注入，成功若干轮后才允许 trusted-auto。
- 增加自动 rollback/quarantine：trusted-auto skill 后续触发用户纠正或失败 evidence 时自动禁用。
- 将 trusted-auto policy 做成可配置风控项，而不是只有一个总开关。

## 38. 最新推进记录：Trusted-Auto 自动回滚隔离

日期：2026-08-01

本次补齐 `trusted-auto` 的第一层回滚机制：如果某个 skill 是由 `trusted-auto` 自动 promote 的，并且在 approval resolved 之后又出现新的 `failure` 或 `user_feedback` usage，下一次 auto review 会自动把该正式项目 skill 移入 quarantine，避免错误 skill 长期留在 loader 路径里继续影响任务。

修改内容：

- 修改 `mewcode/evolution/auto_review.py`：新增 `auto_quarantines` result 字段。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_auto_quarantine_trusted_auto_skills()`，只检查 `approval_mode=trusted-auto` 且 `status=approved` 的 approval request。
- 修改 `mewcode/evolution/auto_review.py`：回滚只统计 `created_at > request.resolved_at` 的负面 usage，避免把 promote 前用于生成 patch 的旧失败误判为新失败。
- 修改 `mewcode/evolution/auto_review.py`：fork reviewer summary/report 展示 `Auto Quarantines`。
- 修改 `mewcode/evolution/engine.py`：`quarantine_skill()` 新增 `source` 参数，默认仍为 `evolve`，自动回滚使用 `trusted-auto-rollback`。
- 修改 `tests/test_evolution.py`：新增 trusted-auto promote 后出现新负面 usage 自动 quarantine 的回归测试。
- 修改本文档：记录自动回滚隔离的触发条件和验证结果。

当前链路：

```text
trusted-auto promotes skill
-> later failure/user_feedback usage is recorded
-> next auto review checks approved trusted-auto requests
-> only usage after resolved_at counts
-> quarantine_skill(source=trusted-auto-rollback)
-> formal skill leaves .mewcode/skills
-> review report records auto_quarantines
```

安全边界：

- 已实现：只回滚 trusted-auto 自动启用过的正式项目 skill。
- 已实现：只看 promote 之后的新负面 usage，不会因为历史失败立即隔离新 skill。
- 已实现：quarantine 会写 usage log，后续 `suggest_quarantine()` 会因 quarantine 事件重置旧负面链。
- 未实现：自动恢复上一个版本；当前回滚方式是隔离，不是版本级 revert。
- 未实现：需要多次失败才回滚的阈值策略；当前 trusted-auto 后一次新负面 usage 即隔离，偏安全。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_quarantines_after_new_negative_usage -q
1 failed  # 实现前红灯：post-promote failure 仍生成新 candidate，没有 quarantine

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_quarantines_after_new_negative_usage -q
1 passed

PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_use_trusted_auto_approval tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_promotes_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_keeps_existing_ready_candidate_pending tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_quarantines_after_new_negative_usage -q
4 passed
```

下一步计划：

- 增加 canary skill 临时注入机制，让 trusted-auto 在正式 promote 前先经过真实任务试用。
- 增加 rollback policy 配置，例如 `rollback_threshold`、`rollback_events` 和 `patch_after_quarantine`。
- 在 TUI/报告中展示 trusted-auto 被隔离的原因和 quarantine 路径。

## 39. 最新推进记录：Trusted-Auto Rollback Threshold 配置

日期：2026-08-01

本次把 trusted-auto 自动回滚从硬编码“一次新负面 usage 即隔离”扩展为可配置策略。新增 `self_evolution.trusted_auto_rollback_threshold`，默认值为 `1`，必须为正整数。设置为 `2` 时，trusted-auto promote 后需要两条新的 `failure` / `user_feedback` usage 才会触发 quarantine。

修改内容：

- 修改 `mewcode/validator.py`：新增 `trusted_auto_rollback_threshold` 默认值和正整数校验。
- 修改 `mewcode/config.py`：`SelfEvolutionConfig` 新增 `trusted_auto_rollback_threshold` 字段，并从配置文件加载。
- 修改 `mewcode/evolution/auto_review.py`：`review_ready_skill_candidates()` 读取 rollback threshold 并传入 trusted-auto rollback 检查。
- 修改 `mewcode/evolution/auto_review.py`：`_auto_quarantine_trusted_auto_skills()` 按 threshold 判断是否 quarantine。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_trusted_auto_managed_skill_names()`，避免 trusted-auto 管理中的 skill 因 promote 前旧负面 usage 反复生成 patch candidate。
- 修改 `tests/test_mcp.py`：新增配置解析 threshold 的测试。
- 修改 `tests/test_evolution.py`：新增 threshold=2 时第一次新失败不隔离、第二次新失败才隔离的测试。

当前链路：

```text
self_evolution.trusted_auto_rollback_threshold = 2
-> trusted-auto promotes skill
-> first post-promote negative usage
-> no quarantine and no patch churn
-> second post-promote negative usage
-> quarantine triggered
```

安全边界：

- 已实现：默认仍偏安全，threshold=1。
- 已实现：threshold 只统计 promote 后的新负面 usage。
- 已实现：未达到 threshold 时不会继续基于旧负面 usage 生成新的 trusted-auto patch candidate。
- 未实现：按 event 类型分别设置阈值，例如 `failure=2`、`user_feedback=1`。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_set_trusted_auto_rollback_threshold tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_uses_rollback_threshold -q
2 failed  # 实现前红灯：配置字段不存在，SelfEvolutionConfig 不接受 threshold

PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_set_trusted_auto_rollback_threshold tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_uses_rollback_threshold -q
2 passed

PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_use_trusted_auto_approval tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_set_trusted_auto_rollback_threshold tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_promotes_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_keeps_existing_ready_candidate_pending tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_quarantines_after_new_negative_usage tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_uses_rollback_threshold -q
6 passed
```

下一步计划：

- 增加 canary skill 临时注入机制。
- 增加 rollback event 类型配置。
- 在审批/报告 UI 中展示当前 trusted-auto policy 参数。

## 40. 最新推进记录：Trusted-Auto Rollback Events 配置

日期：2026-08-01

本次把 trusted-auto 自动回滚的触发事件从固定 `failure/user_feedback` 扩展为可配置列表。新增 `self_evolution.trusted_auto_rollback_events`，默认仍为 `['failure', 'user_feedback']`；用户可以只保留 `user_feedback`，让普通失败先进入观察状态，只有明确用户纠正才触发 quarantine。

修改内容：

- 修改 `mewcode/validator.py`：新增 `VALID_SELF_EVOLUTION_ROLLBACK_EVENTS`，目前允许 `failure` 和 `user_feedback`。
- 修改 `mewcode/validator.py`：新增 `trusted_auto_rollback_events` 默认值、非空列表校验、事件名校验和去重。
- 修改 `mewcode/config.py`：`SelfEvolutionConfig` 新增 `trusted_auto_rollback_events` 字段，并从配置文件加载。
- 修改 `mewcode/evolution/auto_review.py`：`review_ready_skill_candidates()` 读取 rollback events 并传入 trusted-auto rollback 检查。
- 修改 `mewcode/evolution/auto_review.py`：`_auto_quarantine_trusted_auto_skills()` 只统计配置允许的 promote 后 usage event。
- 修改 `tests/test_mcp.py`：新增配置解析 rollback events 的测试。
- 修改 `tests/test_evolution.py`：新增只允许 `user_feedback` 时 failure 不隔离、user_feedback 才隔离的测试。

当前链路：

```text
trusted_auto_rollback_events = [user_feedback]
-> trusted-auto promotes skill
-> post-promote failure usage
-> no quarantine and no patch churn
-> post-promote user_feedback usage
-> quarantine triggered
```

安全边界：

- 已实现：默认行为不变，仍对 `failure` 和 `user_feedback` 敏感。
- 已实现：可以只让用户纠正触发 rollback，降低普通失败带来的误隔离。
- 已实现：未命中配置事件时不会生成新 patch candidate，避免 trusted-auto 管理中的 skill 自我抖动。
- 未实现：按 event 类型分别设置不同阈值；当前 threshold 对配置后的事件集合统一计数。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_set_trusted_auto_rollback_events tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_filters_rollback_events -q
2 failed  # 实现前红灯：配置字段不存在，SelfEvolutionConfig 不接受 rollback events

PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_set_trusted_auto_rollback_events tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_filters_rollback_events -q
2 passed

PYTHONPATH=. pytest tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_use_trusted_auto_approval tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_set_trusted_auto_rollback_threshold tests/test_mcp.py::TestLoadConfigSelfEvolution::test_self_evolution_can_set_trusted_auto_rollback_events tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_promotes_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_keeps_existing_ready_candidate_pending tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_quarantines_after_new_negative_usage tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_uses_rollback_threshold tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_filters_rollback_events -q
8 passed
```

下一步计划：

- 增加 canary skill 临时注入机制。
- 后续支持按 event 类型设置不同 threshold。

## 41. 最新推进记录：Fork Reviewer 展示 Trusted-Auto 策略参数

日期：2026-08-01

本次把 `trusted-auto` 的关键策略参数写入 fork reviewer 的审计产物。此前 reviewer report 会展示是否允许 approve/promote，但不会展示当前自动 promote 范围、rollback threshold 和 rollback event 过滤条件；现在审批和复盘时可以直接看到本次自动审查具体按什么策略运行。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_records_trusted_auto_policy_in_run_artifacts`，要求 `input.json` 和 `report.md` 都展示 trusted-auto policy。
- 修改 `mewcode/evolution/auto_review.py`：`review_ready_skill_candidates()` 将配置中的 `trusted_auto_rollback_threshold` 和 `trusted_auto_rollback_events` 传入 fork reviewer run。
- 修改 `mewcode/evolution/auto_review.py`：`_start_fork_reviewer_run()` 在 `input.json`、`policy.json` 和 run policy 中记录 `trusted_auto_policy`。
- 修改 `mewcode/evolution/auto_review.py`：`_render_fork_reviewer_report()` 新增 `## Trusted-Auto Policy` 区块，展示 auto promote scope、rollback threshold 和 rollback events。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档本次策略透明度改动和验证结果。

当前链路：

```text
self_evolution.skill_approval_mode = trusted-auto
trusted_auto_rollback_threshold = 2
trusted_auto_rollback_events = [user_feedback]
-> review_ready_skill_candidates()
-> fork reviewer input.json records trusted_auto_policy
-> fork reviewer report.md renders Trusted-Auto Policy
-> approval/recap can audit exact autonomous policy
```

安全边界：

- 已实现：自动 promote 范围固定展示为 `same_pass_generated_candidates_only`，避免误解为所有 candidate 都会自动应用。
- 已实现：rollback threshold 和 rollback events 来自当前配置快照，而不是报告生成时重新读取外部状态。
- 已实现：策略只增强审计可见性，不改变 approve/promote/quarantine 的既有门禁。
- 未实现：把 trusted-auto policy 暴露到交互式审批队列的摘要列表；当前主要在 reviewer report 和 approval detail 中可见。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_trusted_auto_policy_in_run_artifacts -q
1 failed  # 实现前红灯：input.json 缺少 trusted_auto_policy

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_trusted_auto_policy_in_run_artifacts -q
1 passed
```

下一步计划：

- 把 canary 执行结果纳入 approval request，要求候选 skill 正确执行多轮任务后才允许进入应用阶段。
- 后续支持按 event 类型设置不同 threshold。

## 42. 最新推进记录：候选 Skill Canary 临时注入

日期：2026-08-01

本次把 execution eval 从“复制候选 `SKILL.md` 到每轮 round 目录”推进为“在 child agent 沙盒中临时注入候选 skill”。每一轮执行评测都会在 `child_agent/.mewcode/skills/<skill>/SKILL.md` 写入候选 skill，并在 `input.json`、`result.json` 和 Markdown 报告里记录 canary 路径。正式项目 `.mewcode/skills` 仍不会被写入，只有审批通过或 trusted-auto 满足全部 gate 后才会 promote。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_run_execution_eval_injects_candidate_skill_into_child_agent_sandbox`，验证 canary skill 只出现在 child agent 沙盒。
- 修改 `mewcode/evolution/engine.py`：`_write_execution_round_artifacts()` 将候选 skill 路径传入 child agent artifact 写入流程。
- 修改 `mewcode/evolution/engine.py`：`_write_child_agent_artifacts()` 新增 `candidate_canary` 注入，把候选 skill 写到 `child_agent/.mewcode/skills/<name>/SKILL.md`。
- 修改 `mewcode/evolution/engine.py`：child agent `input.json`、`tool_policy.json`、`transcript.md`、`result.json` 和 execution eval Markdown 都记录 canary 注入信息。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 canary 注入语义、边界和验证结果。

当前链路：

```text
candidate SKILL.md
-> run_execution_eval()
-> round sandbox
-> child_agent/.mewcode/skills/<skill>/SKILL.md
-> scripted/deterministic child agent uses candidate_canary
-> report records canary skill path
-> approval/promote gate still unchanged
```

安全边界：

- 已实现：canary 写入范围限制在 execution sandbox 的 child agent 目录。
- 已实现：正式项目 `.mewcode/skills/<skill>/SKILL.md` 在 execution eval 阶段不存在，避免未审批 skill 提前生效。
- 已实现：canary 注入结果进入 `input.json` 和 `result.json`，后续审批页读取 execution eval report 时能追溯测试使用的 skill 文件。
- 未实现：独立的长期 canary 观察窗口；当前 canary 是 execution eval 内的多轮隔离执行。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_injects_candidate_skill_into_child_agent_sandbox -q
1 failed  # 实现前红灯：child_agent/.mewcode/skills/<skill>/SKILL.md 不存在

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_run_execution_eval_injects_candidate_skill_into_child_agent_sandbox -q
1 passed
```

下一步计划：

- 增加 canary 失败时阻止 approval request 的回归测试。
- 后续支持按 event 类型设置不同 threshold。

## 43. 最新推进记录：审批页展示 Canary 执行摘要

日期：2026-08-01

本次把 canary 执行结果从完整 execution eval 报告中提取到 approval request 顶部。审批人现在可以先看到 runner、通过轮次、canary 注入数量和首个 canary skill 路径，再决定是否展开阅读完整评测报告。这解决了“候选 skill 是否真的跑过多轮任务”需要翻长报告才能确认的问题。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_render_skill_approval_request_shows_canary_execution_summary`，要求审批 Markdown 包含 `## Canary Execution Summary`。
- 修改 `mewcode/evolution/engine.py`：`render_skill_approval_request()` 在 Candidate Diff 前插入 canary 摘要。
- 修改 `mewcode/evolution/engine.py`：新增 `_render_canary_execution_summary()`，只读 execution eval JSON，提取 runner、passed/total、`candidate_canary` 模式和注入数量。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档审批展示改动和验证结果。

当前链路：

```text
execution eval JSON
-> render_skill_approval_request()
-> Canary Execution Summary
-> Candidate Diff
-> Fork Reviewer Evidence
-> full Execution Eval Report
```

安全边界：

- 已实现：审批渲染只读已有 report，不重新运行 eval、不写入新状态。
- 已实现：canary 摘要出现在 Candidate Diff 之前，优先暴露候选 skill 的执行证据。
- 已实现：如果 execution eval JSON 缺失或损坏，则不展示摘要，原有完整报告缺失提示逻辑仍保留。
- 未实现：把 canary 摘要同步到 approval inbox 列表视图；当前在单个 approval detail 中展示。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_canary_execution_summary -q
1 failed  # 实现前红灯：审批 Markdown 缺少 Canary Execution Summary

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_canary_execution_summary -q
1 passed
```

下一步计划：

- 把 canary 摘要同步到 fork reviewer report 的 generated execution eval 区块。
- 后续支持按 event 类型设置不同 threshold。

## 44. 最新推进记录：Canary 失败阻断审批并留痕

日期：2026-08-01

本次把“execution eval 未通过不能进入审批”从通用错误升级为可审计的 canary block 状态。候选 skill 如果在 canary execution eval 中失败，`submit_skill_approval_request()` 不会生成 approval request，并会在 candidate manifest 中记录 `approval_status=blocked`、`approval_blocked_reason` 和 `approval_blocked_at`。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_submit_skill_approval_request_blocks_failed_canary_execution_eval`，构造 expected file mismatch 的失败 canary。
- 修改 `mewcode/evolution/engine.py`：`submit_skill_approval_request()` 在 execution eval 未通过时写入 blocked manifest，再抛出包含 canary 摘要的错误。
- 修改 `mewcode/evolution/engine.py`：新增 `_mark_candidate_approval_blocked()`，专门记录审批阻断状态。
- 修改 `mewcode/evolution/engine.py`：新增 `_candidate_approval_block_reason()`，从 execution eval JSON 提取 `0/1 rounds passed` 等失败摘要。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档阻断语义和验证结果。

当前链路：

```text
candidate canary execution eval failed
-> submit_skill_approval_request()
-> manifest.approval_status = blocked
-> manifest.approval_blocked_reason = canary execution eval failed: 0/1 rounds passed
-> no approval request generated
```

安全边界：

- 已实现：失败 canary 不会进入 manual/deferred/trusted-auto 审批队列。
- 已实现：阻断原因写在 candidate manifest，后续 fork reviewer 或审批列表可以读取。
- 已实现：如果后续重新跑 eval 并通过，真正生成 request 时会清空 blocked reason。
- 未实现：fork reviewer report 尚未集中展示 blocked candidates；下一步会把 generated execution eval 的失败摘要汇总进去。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_blocks_failed_canary_execution_eval -q
1 failed  # 实现前红灯：只抛通用 execution eval 错误，manifest 没有 blocked reason

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_submit_skill_approval_request_blocks_failed_canary_execution_eval -q
1 passed
```

下一步计划：

- 在 auto review summary 中显式记录 blocked generated candidates。
- 后续支持按 event 类型设置不同 threshold。

## 45. 最新推进记录：Fork Reviewer 汇总 Generated Canary Eval

日期：2026-08-01

本次把自动生成 candidate 的 execution eval canary 摘要同步进 fork reviewer report。此前 canary 摘要只在 approval request 和 execution eval report 内可见；现在 fork reviewer 的 `report.md` 会新增 `## Generated Execution Evals`，集中展示 proposal、skill、执行是否通过、runner、通过轮次、canary 模式和注入次数。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_report_includes_generated_canary_summary`，要求 fork reviewer report 展示 generated execution eval 的 canary 摘要。
- 修改 `mewcode/evolution/auto_review.py`：`_run_execution_evals_for_generated_candidates()` 在每次 execution eval 后读取 report JSON，并附加 `canary_summary`。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_execution_eval_canary_summary()`，提取 runner、`passed/total`、`candidate_canary` 模式和 canary 注入数量。
- 修改 `mewcode/evolution/auto_review.py`：`_render_fork_reviewer_report()` 新增 `## Generated Execution Evals` 区块。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 fork reviewer report 新增摘要字段和验证结果。

当前链路：

```text
auto review generates candidate
-> deterministic eval passed
-> execution eval runs candidate_canary in child agent sandbox
-> auto_review reads execution eval JSON
-> fork reviewer report renders Generated Execution Evals
```

安全边界：

- 已实现：fork reviewer report 只读取 execution eval JSON，不重新执行候选 skill。
- 已实现：报告展示 canary 摘要，不改变 approval/promote gate。
- 已实现：manual/deferred/trusted-auto 都能通过同一 report 看到 generated candidate 的 canary 证据。
- 未实现：失败 generated candidate 的 blocked reason 还没有单独形成 `Blocked Candidates` 区块。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_report_includes_generated_canary_summary -q
1 failed  # 实现前红灯：fork reviewer report 缺少 Generated Execution Evals

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_report_includes_generated_canary_summary -q
1 passed
```

下一步计划：

- 后续支持按 event 类型设置不同 threshold。

## 46. 最新推进记录：Blocked Generated Candidates 独立审计区块

日期：2026-08-01

本次把 generated candidate 的 canary 失败从“execution eval 中的一条失败结果”提升为“显式 blocked generated candidate”。如果自动生成的候选 skill 通过 deterministic eval，但在 canary execution eval 中失败，auto review 会写入 candidate manifest 的 blocked 状态，并在 fork reviewer report 中新增 `## Blocked Generated Candidates` 独立区块。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_blocks_failed_generated_candidate_in_report`，用真实 execution eval 构造三轮 canary 失败候选。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_block_failed_generated_candidates()`，把 failed generated execution eval 转为 blocked candidate。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_generated_candidate_block_reason()`，生成 `generated candidate canary failed: 0/3 rounds passed` 格式的原因。
- 修改 `mewcode/evolution/auto_review.py`：`_review_enabled_skill_candidates()` 和 `_review_run_summary()` 纳入 `blocked_generated_candidates`。
- 修改 `mewcode/evolution/auto_review.py`：`_render_fork_reviewer_report()` 新增 `## Blocked Generated Candidates`，展示 proposal、skill、runner、rounds 和 reason。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 blocked generated candidate 的行为边界和验证结果。

当前链路：

```text
generated candidate
-> deterministic eval passed
-> canary execution eval failed
-> manifest.approval_status = blocked
-> review summary blocked_generated_candidates
-> fork reviewer report Blocked Generated Candidates
```

安全边界：

- 已实现：失败 generated candidate 不会进入 approval request，也不会被 trusted-auto promote。
- 已实现：blocked reason 写入 candidate manifest，后续可以从 candidate 目录追踪原因。
- 已实现：fork reviewer report 把 blocked candidates 与普通 generated execution eval 分开展示，避免失败候选被淹没。
- 未实现：把 blocked candidates 汇总到交互式 inbox 列表；当前在 fork reviewer report 和 manifest 可见。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_blocks_failed_generated_candidate_in_report -q
1 failed  # 实现前红灯：缺少 _block_failed_generated_candidates

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_blocks_failed_generated_candidate_in_report -q
1 passed
```

下一步计划：

- 后续支持按 event 类型设置不同 threshold。

## 47. 最新推进记录：Blocked Generated Candidate 通知摘要

日期：2026-08-01

本次把 blocked generated candidate 从“只在 fork reviewer report/manifest 可见”推进到“review notification 也会提示”。此前 `format_review_notification()` 只有 pending approval request 才返回消息；如果自进化只产生 blocked generated candidate，用户界面不会主动提示。现在即使没有 approval request，只要存在 `blocked_generated_candidates`，notification 也会输出候选 id、skill 名称和阻断原因。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_notification_shows_blocked_generated_candidates`。
- 修改 `mewcode/evolution/auto_review.py`：`format_review_notification()` 同时处理 `requests` 和 `blocked_generated_candidates`。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 notification 行为变化。

当前链路：

```text
blocked_generated_candidates present
-> format_review_notification()
-> Self-evolution blocked generated candidate(s)
-> UI/app can surface blocked candidate reason
```

安全边界：

- 已实现：blocked notification 只展示摘要，不创建审批请求、不 promote。
- 已实现：如果同时有 requests 和 blocked candidates，会在同一通知中分段展示。
- 已实现：没有 requests 且没有 blocked candidates 时仍返回空字符串。
- 未实现：交互式 inbox 列表独立筛选 blocked candidates。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_blocked_generated_candidates -q
1 failed  # 实现前红灯：blocked generated candidates 时 notification 为空

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_blocked_generated_candidates -q
1 passed
```

下一步计划：

- 后续支持按 event 类型设置不同 threshold。

## 48. 最新推进记录：Self-Evolution Inbox 分类

日期：2026-08-01

本次新增只读 self-evolution inbox 分类，让 UI 可以同时看到 pending approval request、blocked generated candidate 和尚未进入审批的 generated candidate。此前 TUI 只会查 pending approval request；如果已有 blocked candidate 但当前 auto review 没有新结果，界面不会主动提示。现在 TUI fallback 会读取 inbox 分类，pending 仍打开审批组件，blocked 则显示摘要消息。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_list_self_evolution_inbox_groups_pending_blocked_and_generated`，覆盖 pending/blocked/generated 三类。
- 修改 `mewcode/evolution/engine.py`：新增 `list_self_evolution_inbox()`，返回 `pending_requests`、`blocked_candidates`、`generated_candidates` 和 `counts`。
- 修改 `mewcode/evolution/engine.py`：新增 `_approval_request_inbox_item()`、`_candidate_inbox_item()` 和 `_load_candidate_manifest_items()`。
- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_shows_existing_blocked_candidate`。
- 修改 `mewcode/app.py`：`_run_self_evolution_review()` 的 fallback 改用 `list_self_evolution_inbox()`；没有 pending 但有 blocked 时展示 blocked 摘要。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 inbox 分类和 TUI 展示路径。

当前链路：

```text
candidate manifests + approval requests
-> list_self_evolution_inbox()
-> pending_requests / blocked_candidates / generated_candidates
-> TUI opens first pending request OR shows blocked summary
```

安全边界：

- 已实现：inbox 是只读视图，不创建 request、不 approve、不 promote。
- 已实现：pending request 优先级最高，保持原有审批弹窗行为。
- 已实现：blocked candidate 只显示摘要，不允许从提示中直接应用。
- 未实现：独立的全屏/列表式 inbox UI；当前是 engine 分类接口 + TUI blocked 摘要。

TDD 与验证记录：

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

下一步计划：

- 支持 review notification 或 TUI 文案展示 generated candidate 计数。
- 后续支持按 event 类型设置不同 threshold。

## 49. 最新推进记录：Generated Candidate TUI 可见性

日期：2026-08-02

本次把 self-evolution inbox 中“已生成但尚未进入 eval/approval gate 的 candidate”暴露到 TUI fallback 消息。此前 TUI 只会优先打开 pending approval request，或在没有 pending 时展示 blocked candidate；如果只有 generated candidate，用户界面没有任何提示。现在 TUI 会显示 generated candidate 数量、proposal id、skill 名称、deterministic eval 状态和 execution eval 状态。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_shows_existing_generated_candidate`，覆盖 generated-only 场景。
- 修改 `mewcode/app.py`：扩展 `_format_self_evolution_inbox_message()`，在没有 pending request 时同时展示 blocked 与 generated 摘要。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 generated-only 展示路径、红绿测试和安全边界。

当前链路：

```text
candidate manifests + approval requests
-> list_self_evolution_inbox()
-> pending_requests / blocked_candidates / generated_candidates
-> TUI opens first pending request OR shows blocked/generated summary
```

安全边界：

- 已实现：generated candidate 只进入只读提示，不创建 approval request。
- 已实现：generated candidate 不会因展示而 approve、promote 或写入正式 skill。
- 已实现：pending request 优先级仍最高；已有审批项会先打开审批组件。
- 未实现：独立的 generated candidate 详情页；当前只展示摘要状态。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
1 failed  # 实现前红灯：TUI fallback 忽略 generated_candidates，messages 为空

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
1 passed
```

下一步计划：

- 把 generated candidate 摘要与 fork reviewer run id 关联，方便用户追踪候选来源。
- 继续完善 trusted-auto/canary 结果在 approval inbox 中的列表级展示。

## 50. 最新推进记录：Trusted-Auto Rollback Usage Cursor

日期：2026-08-02

本次修复 trusted-auto rollback 的时间戳脆弱性。此前 rollback 判断只使用 `usage.created_at > approval.resolved_at`，如果系统时间回拨或测试环境时间戳乱序，approval 前的负反馈可能被误判为 approval 后事件，导致候选 skill 被错误 quarantine。现在 approval 成功时会记录 `usage_baseline_count`，rollback 优先按 usage log 追加顺序只检查审批之后新增的记录；旧 approval request 没有 cursor 时仍回退到原时间戳逻辑。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_trusted_auto_rollback_uses_usage_cursor`，复现审批前 usage 时间戳晚于 approval 的误判场景。
- 修改 `mewcode/evolution/models.py`：`SkillApprovalRequest` 新增兼容字段 `usage_baseline_count`。
- 修改 `mewcode/evolution/engine.py`：approval resolve 成功时记录当前 usage log 长度。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_usage_records_after_approval()`，优先用 cursor 截取 post-approval usage，缺失 cursor 时兼容旧时间戳判断。

安全边界：

- 已实现：trusted-auto rollback 不再依赖 wall-clock 单点判断。
- 已实现：旧 JSONL approval request 仍能加载和按原规则工作。
- 已实现：cursor 只影响 rollback 读取范围，不改变 eval、approval 或 promote 条件。
- 未实现：usage log 的全局 monotonic sequence id；当前 cursor 使用 JSONL 追加顺序。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_rollback_uses_usage_cursor -q
1 failed  # 实现前红灯：approval 前 usage 时间戳被改到未来后触发误 quarantine

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_trusted_auto_rollback_uses_usage_cursor -q
1 passed
```

下一步计划：

- 为 approval inbox 增加 cursor/baseline 展示，方便审计 rollback 判断边界。
- 后续可考虑给 usage log 增加显式递增序号，替代单纯依赖文件追加位置。

## 51. 最新推进记录：审批详情展示 Rollback Guard

日期：2026-08-02

本次把 trusted-auto 的回滚边界显示到审批 Markdown 详情里。上一阶段已经让系统在自动批准时记录 `usage_baseline_count`，但用户或审计者打开 approval request 时看不到这个数字。现在 `render_skill_approval_request()` 会在 trusted-auto request 中展示 `## Trusted-Auto Rollback Guard`，说明自动回滚只统计 baseline 之后追加的 usage 记录。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_render_skill_approval_request_shows_trusted_auto_rollback_guard`。
- 修改 `mewcode/evolution/engine.py`：审批详情新增 `Trusted-Auto Rollback Guard` 段落。
- 修改 `mewcode/evolution/engine.py`：approval inbox item 透出 `usage_baseline_count`，便于后续列表页展示。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档展示字段和验证结果。

用户能看到什么：

- `Usage baseline count`：自动批准时 usage log 已有多少条记录。
- `Post-approval usage source`：后续失败从 JSONL 追加位置之后开始算。
- `Timestamp fallback`：该 request 已有 cursor，不再靠时间戳兜底。

安全边界：

- 已实现：只增加审计展示，不改变 approve/promote/rollback 判断。
- 已实现：只有 trusted-auto 且存在 `usage_baseline_count` 的 request 才显示该段。
- 未实现：TUI 列表页直接显示 baseline；目前详情页和 inbox item 数据已准备好。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_trusted_auto_rollback_guard -q
1 failed  # 实现前红灯：审批详情没有 Trusted-Auto Rollback Guard

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_approval_request_shows_trusted_auto_rollback_guard -q
1 passed
```

下一步计划：

- 把 generated candidate 的来源 review run id 显示到 TUI 摘要里。
- 继续把 approval inbox 从“内部数据结构”推进成更完整的列表视图。

## 52. 最新推进记录：Generated Candidate 来源 Review Run

日期：2026-08-02

本次把 generated candidate 的来源 review run id 接入 self-evolution inbox 和 TUI 摘要。此前界面只能看到“有一个 generated candidate”，但看不到它是哪次自动复盘生成的。现在 generated candidate 行会附带 `review=<run_id>`，用户可以把候选 skill 反查到对应 fork reviewer run、报告和生成证据。

修改内容：

- 修改 `tests/test_evolution.py`：扩展 `test_tui_self_evolution_review_shows_existing_generated_candidate`，要求 TUI 消息包含来源 review run id。
- 修改 `mewcode/evolution/engine.py`：candidate inbox item 会根据 review run summary 查找 `generated_candidates` / `blocked_generated_candidates`，并返回 `review_run_id`、`review_run_status`、`review_run_report`。
- 修改 `mewcode/app.py`：generated candidate 摘要行在有来源 run 时追加 `review=<run_id>`。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档展示字段和验证结果。

用户能看到什么：

- candidate proposal id。
- skill 名称。
- eval / execution eval 状态。
- 来源 review run id。

安全边界：

- 已实现：只增加来源展示，不改变候选生成、eval、审批或 promote 行为。
- 已实现：没有来源 run 的手工 candidate 仍按原样显示，不强行造来源。
- 未实现：TUI 中直接打开 review report；目前只展示 run id 和在 inbox item 中准备 report path。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
1 failed  # 实现前红灯：generated candidate 摘要没有 review run id

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
1 passed
```

下一步计划：

- 把 review report path 进一步接到 TUI 操作面，减少用户手动查 JSONL。
- 继续把 approval inbox 从“内部数据结构”推进成可浏览列表。

## 53. 最新推进记录：Blocked Candidate 来源 Review Run

日期：2026-08-02

本次把 blocked generated candidate 的来源 review run id 也接入 TUI 摘要。上一阶段 generated candidate 已经能显示 `review=<run_id>`，但如果候选 skill 被 canary 阻断，blocked 提示仍只展示 proposal id 和原因。现在 blocked 行也会带上来源 review run，方便用户从阻断结果反查 fork reviewer 报告。

修改内容：

- 修改 `tests/test_evolution.py`：扩展 `test_tui_self_evolution_review_shows_existing_blocked_candidate`，要求 blocked 提示包含来源 review run id。
- 修改 `mewcode/app.py`：blocked generated candidate 摘要行在有来源 run 时追加 `review=<run_id>`。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 blocked 来源展示和验证结果。

用户能看到什么：

- blocked candidate proposal id。
- skill 名称。
- canary 阻断原因。
- 来源 review run id。

安全边界：

- 已实现：只增加 blocked 来源展示，不改变阻断、审批或 promote 行为。
- 已实现：没有来源 run 的 blocked candidate 仍按原样显示。
- 未实现：TUI 中直接打开 review report；当前仍先展示 run id。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate -q
1 failed  # 实现前红灯：blocked candidate 摘要没有 review run id

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate -q
1 passed
```

下一步计划：

- 把 review report path 接到 TUI 摘要或详情入口。
- 继续把 approval inbox 从“内部数据结构”推进成可浏览列表。

## 54. 最新推进记录：TUI 摘要展示 Review Report 路径

日期：2026-08-02

本次把来源 review run 的 report 路径接入 TUI 摘要。前两阶段已经能显示 `review=<run_id>`，但用户仍需要自己去查 review run 记录才能找到报告文件。现在 blocked/generated candidate 的摘要会同时显示 `report=<path>`，用户能直接定位 fork reviewer 报告。

修改内容：

- 修改 `tests/test_evolution.py`：扩展 blocked/generated TUI 测试，要求消息包含来源 report path。
- 修改 `mewcode/app.py`：新增 `_format_self_evolution_source_part()`，统一格式化 `review=<id>` 和 `report=<path>`。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 report path 展示和验证结果。

用户能看到什么：

- 来源 review run id。
- 来源 report markdown/json 路径。
- blocked/generated candidate 的当前状态。

安全边界：

- 已实现：只增加 TUI 摘要展示，不读取或修改 report 文件。
- 已实现：没有 report path 的候选仍按原样显示。
- 未实现：TUI 中点击/展开 report；当前先显示可复制路径。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
2 failed  # 实现前红灯：TUI 摘要没有 report path

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
2 passed
```

下一步计划：

- 把 report path 接到可浏览详情入口，而不是只显示文本路径。
- 继续把 approval inbox 从“内部数据结构”推进成可浏览列表。

## 55. 最新推进记录：Markdown Self-Evolution Inbox

日期：2026-08-02

本次新增 Markdown 版 self-evolution inbox。此前 engine 只能返回结构化 dict，TUI 只能拼接很短的提示文本；现在 `render_self_evolution_inbox()` 可以把 pending approval request、blocked generated candidate 和 generated candidate 三类集中渲染成一份可读 Markdown，后续 TUI/API 可以直接展示这个列表。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_render_self_evolution_inbox_summarizes_all_candidate_groups`，覆盖 pending/blocked/generated 三类输出。
- 修改 `mewcode/evolution/engine.py`：新增 `render_self_evolution_inbox()`。
- 修改 `mewcode/evolution/engine.py`：新增 pending、blocked、generated 三类 inbox 行 formatter，并复用 review/report 来源字段。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 Markdown inbox 能力和验证结果。

用户能看到什么：

- pending approval request 列表。
- blocked generated candidate 列表及阻断原因。
- generated candidate 列表及 eval/execution 状态。
- 每个候选关联的 review run 和 report path。

安全边界：

- 已实现：只读渲染，不创建 approval request、不 approve、不 promote。
- 已实现：空分组会显示 `None`，避免用户误以为列表渲染失败。
- 未实现：TUI 全屏列表入口；当前先提供 engine 层 Markdown 渲染函数。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_self_evolution_inbox_summarizes_all_candidate_groups -q
1 failed  # 实现前红灯：EvolutionEngine 缺少 render_self_evolution_inbox()

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_self_evolution_inbox_summarizes_all_candidate_groups -q
1 passed
```

下一步计划：

- 把 Markdown inbox 接入 TUI fallback，替代当前短摘要提示。
- 继续完善 approval inbox 的可浏览列表和详情入口。

## 56. 最新推进记录：TUI Fallback 使用 Markdown Inbox

日期：2026-08-03

本次把 Markdown 版 self-evolution inbox 接入 TUI fallback。此前没有 pending approval request 时，TUI 只展示 blocked/generated 的短摘要；现在会直接展示 `render_self_evolution_inbox()` 生成的完整 Markdown 列表，用户可以一次看到 pending、blocked、generated 三类自进化状态。

修改内容：

- 修改 `tests/test_evolution.py`：更新 blocked/generated TUI fallback 测试，要求消息包含 `# Self-Evolution Inbox` 和对应分组标题。
- 修改 `mewcode/app.py`：`_run_self_evolution_review()` 在无 pending request 时调用 `EvolutionEngine.render_self_evolution_inbox()`。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 TUI fallback 切换行为和验证结果。

用户能看到什么：

- 完整 self-evolution inbox 标题。
- pending approval request 分组。
- blocked generated candidate 分组。
- generated candidate 分组。
- review run 和 report path。

安全边界：

- 已实现：pending request 仍优先打开 approval widget。
- 已实现：Markdown inbox 只在没有 pending request 时展示。
- 已实现：展示列表不创建 request、不 approve、不 promote。
- 未实现：TUI 中点击打开 report；当前仍是文本路径。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
2 failed  # 实现前红灯：TUI fallback 仍展示短摘要，没有 Markdown inbox 标题

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate -q
2 passed
```

下一步计划：

- 把 Markdown inbox 接到更正式的 TUI 列表/详情入口。
- 继续完善 approval inbox 的可浏览列表和详情入口。

## 57. 最新推进记录：清理旧 TUI 短摘要 Formatter

日期：2026-08-03

本次清理 TUI 中已经被 Markdown inbox 取代的旧短摘要 formatter。此前 `MewCodeApp` 里还保留 `_format_self_evolution_inbox_message()` 和 `_format_self_evolution_source_part()`，但 `_run_self_evolution_review()` 已经改为直接展示 `EvolutionEngine.render_self_evolution_inbox()` 的完整 Markdown 输出。继续保留旧 formatter 会让后续维护者误以为还有两套 self-evolution inbox 展示路径。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_drops_legacy_short_summary_formatter`，要求 TUI 不再暴露旧短摘要 formatter。
- 修改 `mewcode/app.py`：删除 `_format_self_evolution_inbox_message()` 和 `_format_self_evolution_source_part()`。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档清理原因、行为边界和验证记录。

用户能看到什么：

- TUI fallback 仍展示完整 `# Self-Evolution Inbox` Markdown 列表。
- pending approval request 仍优先打开 approval widget。
- blocked/generated 候选仍显示 review run 和 report path，但渲染来源统一在 engine 层。

安全边界：

- 未改变审批模式。
- 未改变 trusted-auto 自动批准条件。
- 未改变 promote、rollback、quarantine 行为。
- 未新增用户命令；只是删除未使用的旧展示代码。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_drops_legacy_short_summary_formatter -q
1 failed  # 初次红灯是导入 mcp 依赖缺失，修正测试夹具后继续验证目标失败

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_drops_legacy_short_summary_formatter -q
1 failed  # 实现前红灯：MewCodeApp 仍有 _format_self_evolution_inbox_message

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_drops_legacy_short_summary_formatter -q
1 passed
```

下一步计划：

- 跑完整 `tests/test_evolution.py` 确认没有 self-evolution 回归。
- 把 Markdown inbox 继续推进到更正式的 TUI 列表/详情入口。

## 58. 最新推进记录：Review Run 报告只读读取接口

日期：2026-08-03

本次新增 engine 层的 review run 报告读取接口。此前 inbox 只能显示 `report=<path>`，后续 TUI/详情页如果要打开报告，还需要自己拼路径、自己读文件。现在 `EvolutionEngine.read_self_evolution_review_report(review_run_id)` 可以按 review run id 读取对应 `report.md`，并在 engine 层统一做路径安全校验。

修改内容：

- 修改 `tests/test_evolution.py`：新增正常读取 report Markdown 的测试。
- 修改 `tests/test_evolution.py`：新增 artifact path 指向 review_runs 外部时拒绝读取的测试。
- 修改 `mewcode/evolution/engine.py`：新增 `review_run_artifacts_path`、`read_self_evolution_review_report()` 和 `_resolve_review_run_report_path()`。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档报告读取接口、路径边界和测试结果。

用户能看到什么：

- 后续界面可以通过 review run id 读取 fork reviewer report 内容。
- 当前 inbox 仍只显示路径；本次先补 engine 层只读能力。
- 缺失 report、未知 run 或非法路径会返回错误文本，不会抛到 UI。

安全边界：

- 只允许读取 `.mewcode/evolution/review_runs/` 下的 `report.md`。
- artifact path 如果指向项目其它文件，例如 `README.md`，会被拒绝。
- 不改变候选生成、审批、promote、rollback 或 quarantine 逻辑。
- 不新增用户命令，不允许通过该接口写文件。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_read_self_evolution_review_report_returns_markdown tests/test_evolution.py::TestEvolutionEngine::test_read_self_evolution_review_report_rejects_escaped_report_path -q
2 failed  # 实现前红灯：EvolutionEngine 缺少 read_self_evolution_review_report()

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_read_self_evolution_review_report_returns_markdown tests/test_evolution.py::TestEvolutionEngine::test_read_self_evolution_review_report_rejects_escaped_report_path -q
2 passed
```

下一步计划：

- 把该只读 report 接口接入 TUI inbox 详情入口。
- 增加 missing report / unknown review run 的展示测试。

## 59. 最新推进记录：TUI Inbox 内联 Review Report 详情

日期：2026-08-03

本次把上一阶段新增的 `read_self_evolution_review_report()` 接入 TUI self-evolution fallback。此前 TUI 只能显示 `review=<id>` 和 `report=<path>`，用户需要自己去文件系统查报告。现在当 inbox 中只有一个可追踪 review run 时，TUI 会在 `# Self-Evolution Inbox` 后追加 `## Review Report Details`，直接展示该 fork reviewer 的 Markdown 报告内容。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_inlines_single_review_report`，覆盖单个 generated candidate 时内联 report 内容。
- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_report_detail_rejects_escaped_path`，覆盖污染的 report path 不会泄露项目文件。
- 修改 `mewcode/app.py`：新增 `_format_self_evolution_review_report_detail()`，并在 `_run_self_evolution_review()` 的 inbox fallback 中追加 report detail。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 TUI report detail 的展示行为和安全边界。

用户能看到什么：

- 单个 blocked/generated candidate 对应一个 review run 时，TUI 直接展示 `## Review Report Details`。
- report 内容来自 `.mewcode/evolution/review_runs/<review_id>/report.md`。
- 如果 report path 非法或报告缺失，TUI 展示安全错误，不展示项目其它文件内容。
- 多个候选时仍先展示 inbox 列表，不自动展开多个报告，避免界面刷屏。

安全边界：

- 不新增用户命令。
- 不改变审批、promote、rollback 或 quarantine 行为。
- 不改变 trusted-auto 自动批准条件。
- report 读取仍走 engine 层路径校验，只允许读取 review_runs 下的 `report.md`。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_inlines_single_review_report tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_rejects_escaped_path -q
2 failed  # 实现前红灯：TUI inbox 没有 Review Report Details

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_inlines_single_review_report tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_rejects_escaped_path -q
2 passed
```

下一步计划：

- 给多个候选场景补一个明确的“只列出 report，不展开全部”的测试。
- 继续把 inbox 从文本消息推进到可选择的 TUI widget。

## 60. 最新推进记录：多 Review Run 不自动展开报告

日期：2026-08-03

本次补齐 TUI self-evolution inbox 的多候选安全提示。上一阶段只在“单个 review run”时内联展示报告；如果同时有多个 blocked/generated candidate，直接展开所有 report 会让界面很长，也可能把多份评测细节一次性刷出来。现在多个 review run 时，TUI 会展示 `## Review Report Details`，但只列出 review run id，并明确说明报告内容已省略。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_omits_multiple_review_reports`。
- 修改 `mewcode/app.py`：`_format_self_evolution_review_report_detail()` 在多个 review run 时返回省略提示和 id 列表。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档多候选 report 展示边界。

用户能看到什么：

- 多个候选对应多个 review run 时，界面会说明 `multiple review runs`。
- 用户能看到相关 review run id。
- 多份 report 内容不会被自动展开。

安全边界：

- 不读取多个 report 文件。
- 不改变单个 review run 的内联 report 展示。
- 不改变审批、promote、rollback、quarantine 或 trusted-auto 条件。
- 不新增用户命令。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_omits_multiple_review_reports -q
1 failed  # 实现前红灯：多 review run 时没有 Review Report Details 提示

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_omits_multiple_review_reports -q
1 passed
```

下一步计划：

- 继续把 inbox 从文本消息推进到可选择的 TUI widget。
- 给 report 缺失场景补 UI 层测试，确保错误提示可读。

## 61. 最新推进记录：缺失 Review Report 的 UI 脱敏提示

日期：2026-08-04

本次补齐 TUI self-evolution inbox 的缺失报告展示。此前单个 review run 的 report 不存在时，底层读取接口会返回包含本机绝对路径的诊断信息；这对开发调试有用，但不适合直接展示给用户。现在 TUI 会把这类错误脱敏成 `report missing for <review_run_id>`，用户能知道哪个 review run 缺报告，但看不到本机临时目录路径。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_report_detail_sanitizes_missing_report`。
- 修改 `mewcode/app.py`：`_format_self_evolution_review_report_detail()` 对 `review report not found` 错误做 UI 层脱敏。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档缺失报告提示和路径脱敏边界。

用户能看到什么：

- 单个 review run 缺失 report 时，仍显示 `## Review Report Details`。
- 错误提示变成 `report unavailable: report missing for <review_run_id>`。
- 不再向 UI 暴露 `/tmp/...` 这类本机绝对路径。

安全边界：

- 只改变 UI 展示文本。
- 不改变 engine 的底层读取诊断。
- 不改变审批、promote、rollback、quarantine 或 trusted-auto 条件。
- 不新增用户命令。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_sanitizes_missing_report -q
1 failed  # 实现前红灯：UI 没有脱敏缺失 report 的绝对路径

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_sanitizes_missing_report -q
1 passed
```

下一步计划：

- 继续把 inbox 从文本消息推进到可选择的 TUI widget。
- 给 unknown review run 的 UI 提示补测试。

## 62. 最新推进记录：未知 Review Run 的 UI 脱敏提示

日期：2026-08-04

本次补齐 TUI self-evolution report detail 的未知 review run 提示。未来如果 TUI 详情入口拿到过期、损坏或已清理的 `review_run_id`，底层 engine 会返回 `review run <id> not found`。这个文案对内部诊断足够，但对用户不够稳定。现在 TUI 展示层会把它改成 `review run unavailable: <review_run_id>`。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_report_detail_sanitizes_unknown_run`。
- 修改 `mewcode/app.py`：`_format_self_evolution_review_report_detail()` 对 unknown review run 错误做 UI 层脱敏。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 unknown review run 的展示规则。

用户能看到什么：

- 详情入口拿到未知 review run id 时，仍显示 `## Review Report Details`。
- 错误提示变成 `report unavailable: review run unavailable: <review_run_id>`。
- 不直接暴露底层 store 查询失败文案。

安全边界：

- 只改变 UI 展示文本。
- 不改变 engine 的底层读取诊断。
- 不改变审批、promote、rollback、quarantine 或 trusted-auto 条件。
- 不新增用户命令。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_sanitizes_unknown_run -q
1 failed  # 实现前红灯：UI 直接展示 review run <id> not found

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_detail_sanitizes_unknown_run -q
1 passed
```

下一步计划：

- 继续把 inbox 从文本消息推进到可选择的 TUI widget。
- 将 report detail 错误脱敏规则提取成小 helper，减少后续分支堆叠。

## 63. 最新推进记录：Review Report 错误脱敏 Helper

日期：2026-08-04

本次把 TUI self-evolution report detail 中的错误脱敏规则提取成 `_sanitize_self_evolution_review_report_error()`。此前缺失 report 和未知 review run 的脱敏逻辑直接写在 `_format_self_evolution_review_report_detail()` 里；随着错误分支增加，继续堆在展示函数里会让后续维护更容易遗漏边界。现在脱敏规则集中到一个小 helper，展示函数只负责拼接 Markdown。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_report_error_sanitizer`，覆盖缺失 report、未知 run 和普通安全错误三类文案。
- 修改 `mewcode/app.py`：新增 `_sanitize_self_evolution_review_report_error()`，并让 `_format_self_evolution_review_report_detail()` 调用该 helper。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 helper 提取原因和验证结果。

用户能看到什么：

- 用户可见文案不变。
- 缺失 report 仍显示 `report missing for <review_run_id>`。
- 未知 run 仍显示 `review run unavailable: <review_run_id>`。
- 路径逃逸等安全错误仍保留原安全拒绝原因。

安全边界：

- 只重构 TUI 展示层。
- 不改变 engine 的读取、查询或路径校验。
- 不改变审批、promote、rollback、quarantine 或 trusted-auto 条件。
- 不新增用户命令。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_error_sanitizer -q
1 failed  # 初次红灯是测试夹具未安装 fake MCP，修正后继续验证目标失败

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_error_sanitizer -q
1 failed  # 实现前红灯：MewCodeApp 缺少 _sanitize_self_evolution_review_report_error()

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_report_error_sanitizer -q
1 passed
```

下一步计划：

- 继续把 inbox 从文本消息推进到可选择的 TUI widget。
- 提取测试构造 helper，减少 TUI self-evolution 测试重复样板。

## 64. 最新推进记录：Self-Evolution Inbox 可选择 Widget 基础

日期：2026-08-04

本次新增 `InlineSelfEvolutionInboxWidget`，作为把 self-evolution inbox 从纯文本系统消息推进到可选择 TUI 入口的基础。当前 app fallback 仍使用原来的系统消息路径；本次先把 widget 本体、选项渲染和选择事件做成可测试组件，避免一次性替换现有 TUI 路径造成大范围回归。

修改内容：

- 修改 `tests/test_self_evolution_dialog.py`：新增 inbox widget 渲染测试，覆盖 inbox 内容、查看报告和关闭动作。
- 修改 `tests/test_self_evolution_dialog.py`：新增 inbox widget 事件测试，覆盖 `VIEW_REPORT` 和 `DISMISS` 两个选择事件。
- 修改 `mewcode/self_evolution_dialog.py`：新增 `SelfEvolutionInboxChoice` 和 `InlineSelfEvolutionInboxWidget`。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 widget 基础能力和未接入边界。

用户能看到什么：

- 当前用户可见 TUI fallback 行为不变。
- 新 widget 已具备显示 inbox Markdown、查看 report detail、关闭 inbox 的基础交互。
- 下一步可以把 app fallback 从系统消息切到该 widget。

安全边界：

- 不改变审批、promote、rollback、quarantine 或 trusted-auto 条件。
- 不新增用户命令。
- 不读取 report 文件；widget 只接收已经准备好的 Markdown 字符串。
- 不替换现有 fallback 展示路径，本次只增加组件基础。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_self_evolution_inbox_widget_shows_inbox_and_actions tests/test_self_evolution_dialog.py::test_self_evolution_inbox_widget_emits_view_report_and_dismiss -q
1 error  # 实现前红灯：InlineSelfEvolutionInboxWidget 尚不存在

PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_self_evolution_inbox_widget_shows_inbox_and_actions tests/test_self_evolution_dialog.py::test_self_evolution_inbox_widget_emits_view_report_and_dismiss -q
2 passed
```

下一步计划：

- 把 app fallback 中的 self-evolution inbox 系统消息切到 `InlineSelfEvolutionInboxWidget`。
- 处理 widget 的 `VIEW_REPORT` 事件，让用户按需查看 report detail，而不是自动展开。

## 65. 最新推进记录：TUI Fallback 接入可选择 Inbox Widget

日期：2026-08-04

本次把 self-evolution TUI fallback 从系统消息切到 `InlineSelfEvolutionInboxWidget`。此前没有 pending approval request 时，TUI 会直接把完整 inbox 和 report detail 作为系统消息展示；现在 app 会把 inbox Markdown 和 report detail Markdown 分开传入 widget，用户先看到 inbox 列表，只有选择 `View report details` 时才显示报告内容。

修改内容：

- 修改 `tests/test_evolution.py`：更新 blocked/generated/inbox report 相关 fallback 测试，要求 app 调用 `_show_self_evolution_inbox(inbox_markdown, report_detail_markdown)`。
- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_inbox_response_views_report_or_dismisses`，覆盖 `VIEW_REPORT` 显示报告、`DISMISS` 不改变状态也不显示报告。
- 修改 `mewcode/app.py`：`_run_self_evolution_review()` 改为调用 `_show_self_evolution_inbox()`。
- 修改 `mewcode/app.py`：新增 `_mount_self_evolution_inbox()` 和 `on_inline_self_evolution_inbox_widget_responded()`。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 widget 接入、按需查看报告和安全边界。

用户能看到什么：

- 没有 pending approval request 时，TUI 显示可选择的 self-evolution inbox widget。
- inbox 列表不再自动混入 report detail。
- 选择 `View report details` 后才显示 report Markdown。
- 选择 `Dismiss inbox` 只关闭/忽略当前展示，不修改候选状态。

安全边界：

- 不改变 approval request、promote、rollback、quarantine 或 trusted-auto 行为。
- 不新增用户命令。
- report 仍由 engine/app 读取和脱敏后传给 widget。
- dismiss 不 approve、不 reject、不 quarantine。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_inlines_single_review_report -q
2 failed  # 实现前红灯：app 仍走 _show_system_message()

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_inlines_single_review_report -q
2 passed

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_response_views_report_or_dismisses -q
1 failed  # 实现前红灯：app 缺少 inbox widget 响应处理器

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_response_views_report_or_dismisses -q
1 passed
```

下一步计划：

- 给 `_mount_self_evolution_inbox()` 补挂载层测试，确认输入框禁用/恢复行为。
- 清理 TUI fallback 测试重复样板。

## 66. 最新推进记录：Self-Evolution Inbox 去重展示

日期：2026-08-04

本次修复 TUI fallback 的重复 inbox 展示问题。此前如果同一个 generated/blocked candidate 在多轮 assistant 响应后仍未被处理，`_run_self_evolution_review()` 会再次渲染同一个 inbox，导致聊天区出现重复 widget，并可能让用户误以为产生了新的候选项。现在 app 会记录当前待处理 inbox 的内容 key；同内容未响应前只展示一次，用户选择 `View report details` 或 `Dismiss inbox` 后才允许下一次展示。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_inbox_deduplicates_pending_display`，覆盖同一 inbox 未处理前不会重复挂载、响应后可再次展示。
- 修改 `mewcode/app.py`：新增 `_pending_self_evolution_inbox_key` 状态字段。
- 修改 `mewcode/app.py`：`_show_self_evolution_inbox()` 对同一 `inbox_markdown + report_detail_markdown` 做 pending 去重。
- 修改 `mewcode/app.py`：`on_inline_self_evolution_inbox_widget_responded()` 在用户响应后清理 pending key。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档本次去重策略、行为边界和验证结果。

用户能看到什么：

- 同一个 self-evolution inbox 不会在多轮响应后重复刷屏。
- 用户处理当前 inbox 后，后续仍可以再次展示新的或同一个 inbox。
- `View report details` 和 `Dismiss inbox` 仍不改变 candidate 状态。

安全边界：

- 不改变 candidate 生成、评测、审批、promote、rollback、quarantine 或 trusted-auto 行为。
- 不新增用户命令。
- 只影响 TUI 展示去重，不影响 engine 存储状态。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_deduplicates_pending_display -q
1 failed  # 实现前红灯：同一 inbox 被挂载两次

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_deduplicates_pending_display -q
1 passed
```

下一步计划：

- 补 `_mount_self_evolution_inbox()` 挂载层测试，确认输入框禁用与恢复行为。
- 继续减少 TUI self-evolution fallback 测试样板。

## 67. 最新推进记录：Inbox 挂载失败恢复 Pending 状态

日期：2026-08-04

本次补齐上一节去重逻辑的异常恢复路径。去重依赖 `_pending_self_evolution_inbox_key`，如果异步挂载 widget 时发生异常但 pending key 没清理，后续同一个 inbox 会被误判为“已经展示”，从而不再出现。现在 `_show_self_evolution_inbox()` 会通过 guarded coroutine 调用 `_mount_self_evolution_inbox()`；如果挂载失败，会消费异常并在 key 仍匹配时清理 pending 状态，允许后续 review tick 重新展示。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_inbox_clears_pending_when_mount_fails`，覆盖挂载失败后 pending key 清理和后续重试。
- 修改 `mewcode/app.py`：新增 `_mount_self_evolution_inbox_guarded()`，封装 inbox widget 异步挂载。
- 修改 `mewcode/app.py`：`_show_self_evolution_inbox()` 改为调度 guarded coroutine。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档异常恢复路径。

用户能看到什么：

- 如果某次 TUI widget 挂载失败，同一个 self-evolution inbox 后续仍能重新展示。
- 不会因为一次 UI 异常导致 self-evolution inbox 永久沉默。

安全边界：

- 不改变 candidate、review run、approval request 或 skill 状态。
- 不新增用户命令。
- 只处理 TUI 挂载失败后的 pending 展示状态。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_clears_pending_when_mount_fails -q
1 failed  # 实现前红灯：挂载失败后 pending key 没清理，且任务异常未被消费

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_clears_pending_when_mount_fails -q
1 passed
```

下一步计划：

- 补真实挂载路径的输入框禁用/恢复测试。
- 清理 self-evolution TUI fallback 测试的重复 app 构造代码。

## 68. 最新推进记录：Inbox 挂载层输入框状态测试

日期：2026-08-04

本次补充 self-evolution inbox 真实挂载路径的回归测试。前几节已经验证 widget 本体、app fallback 接入、去重和挂载失败恢复；本次专门验证 `_mount_self_evolution_inbox()` 会在展示 widget 时禁用 `#chat-input`，并且 `on_inline_self_evolution_inbox_widget_responded()` 会在用户响应后移除 widget、恢复输入框并重新 focus。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_inbox_mount_disables_and_restores_input`。
- 测试使用 fake chat/input/inline widget，不启动完整 Textual app，直接覆盖 app 挂载与响应路径。
- 本次没有修改生产代码；测试确认现有实现符合预期。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档测试补强。

用户能看到什么：

- self-evolution inbox 展示期间输入框会被禁用，避免用户在待选择状态下继续输入造成状态混乱。
- 用户选择 `View report details` 或 `Dismiss inbox` 后，输入框恢复可用并重新获得焦点。

安全边界：

- 不改变 candidate、review run、approval request 或 skill 状态。
- 不新增用户命令。
- 不改变 runtime 逻辑，只增加回归测试覆盖。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_mount_disables_and_restores_input -q
1 passed
```

下一步计划：

- 清理 self-evolution TUI fallback 测试的重复 app 构造代码。
- 继续补充用户可见 inbox 状态的边界测试，例如没有 report detail 时只显示 dismiss 动作。

## 69. 最新推进记录：Self-Evolution TUI 测试 App 构造 Helper

日期：2026-08-04

本次清理 `tests/test_evolution.py` 中 self-evolution TUI fallback 测试的重复 app 构造代码。此前每个测试都手写 `MewCodeApp(providers=[ProviderConfig(...)])`，导致新增 inbox/approval 测试时需要复制十几行样板，后续修改测试 provider 配置也容易漏改。现在新增 `_make_test_mewcode_app()` helper，统一生成测试用 app；需要配置 self-evolution approval mode 的测试仍通过参数传入。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `_make_test_mewcode_app(**kwargs)`。
- 修改 `tests/test_evolution.py`：将 self-evolution approval、fallback inbox、report detail、pending 去重、挂载失败、输入框状态和 skill approval 响应测试切到 helper。
- 保留直接使用 `MewCodeApp` 静态方法的测试，不强行抽象。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档测试结构清理。

用户能看到什么：

- 运行时行为不变。
- 后续新增 self-evolution TUI 测试更短、更集中，减少重复 provider 配置。

安全边界：

- 不修改生产代码。
- 不改变 candidate、review run、approval request、promote、rollback、quarantine 或 trusted-auto 行为。
- 只清理测试结构。

验证记录：

```text
PYTHONPATH=. pytest tests/test_self_evolution_dialog.py tests/test_evolution.py -q
110 passed  # 重构前基线

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_approval_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_mount_disables_and_restores_input tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills -q
4 passed
```

下一步计划：

- 补充 no report detail 场景下 inbox widget 只显示 `Dismiss inbox` 的边界测试。
- 继续检查 self-evolution TUI 事件是否存在重复弹窗或状态泄露。

## 70. 最新推进记录：Self-Evolution Inbox 单 Pending 限制

日期：2026-08-04

本次收紧 self-evolution TUI inbox 的展示边界。此前只会去重相同内容的 pending inbox；如果旧 inbox 还没被用户处理，又来了内容不同的新 inbox，TUI 仍可能继续挂载第二个 widget。现在只要存在一个待处理 self-evolution inbox，就不会再挂载新的 inbox，直到用户选择 `View report details` / `Dismiss inbox`，或挂载失败后 pending 状态被清理。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_inbox_allows_only_one_pending_widget`，覆盖不同内容 inbox 在旧 widget 未处理前不会重复挂载。
- 修改 `mewcode/app.py`：`_show_self_evolution_inbox()` 从“同 key 去重”改为“pending key 非空即阻止新挂载”。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档单 pending 限制。

用户能看到什么：

- 同一时间聊天区最多出现一个 self-evolution inbox widget。
- 用户处理当前 inbox 后，后续新 inbox 可以正常展示。
- 挂载失败仍会清理 pending 状态并允许重试。

安全边界：

- 不改变 candidate、review run、approval request、promote、rollback、quarantine 或 trusted-auto 行为。
- 不新增用户命令。
- 只影响 TUI 展示层的 pending widget 串行化。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_allows_only_one_pending_widget -q
1 failed  # 实现前红灯：不同内容 inbox 会继续挂载第二个 widget

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_allows_only_one_pending_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_deduplicates_pending_display tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_clears_pending_when_mount_fails -q
3 passed
```

下一步计划：

- 补充 no report detail 场景下 inbox widget 只显示 `Dismiss inbox` 的边界测试。
- 继续检查 self-evolution TUI 事件是否存在状态泄露。

## 71. 最新推进记录：Inbox 无报告详情时隐藏查看动作

日期：2026-08-05

本次补充 self-evolution inbox widget 的 no-report-detail 回归测试。`InlineSelfEvolutionInboxWidget` 已经通过 `report_detail_markdown.strip()` 判断是否显示 `View report details`；当 report detail 为空或只有空白字符时，widget 应只显示 `Dismiss inbox`，并且选择后不会返回任何 report Markdown。

修改内容：

- 修改 `tests/test_self_evolution_dialog.py`：新增 `test_self_evolution_inbox_widget_hides_report_action_without_detail`。
- 测试覆盖空白 report detail、隐藏 `View report details`、保留 `Dismiss inbox`、选择后返回 `DISMISS` 且 report 为空。
- 本次不修改生产代码；现有实现已满足该边界。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档测试补强。

用户能看到什么：

- 没有可读报告详情时，inbox 不会展示无效的 `View report details` 动作。
- 用户只会看到可执行的 `Dismiss inbox` 动作，避免点开空报告。

安全边界：

- 不改变 candidate、review run、approval request、promote、rollback、quarantine 或 trusted-auto 行为。
- 不新增用户命令。
- 不修改生产代码，只补测试覆盖。

验证记录：

```text
PYTHONPATH=. pytest tests/test_self_evolution_dialog.py::test_self_evolution_inbox_widget_hides_report_action_without_detail -q
1 passed
```

下一步计划：

- 继续检查 self-evolution TUI 事件是否存在状态泄露。
- 考虑把 inbox widget 的选项列表暴露为只读测试 helper，减少直接依赖 `_build_content()` 字符串断言。

## 72. 最新推进记录：Approval 优先时清理 Pending Inbox

日期：2026-08-05

本次修复 self-evolution TUI 的优先级状态泄露。approval request 应高于 inbox 展示；此前如果 inbox widget 仍处于 pending 状态，随后出现 approval request，app 会继续挂载 approval widget，但不会主动移除旧 inbox，导致聊天区可能同时存在 inbox 和 approval 两个交互入口。现在 `_show_self_evolution_approval()` 在展示 approval 前会先清理 pending inbox widget 和 pending key。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_approval_clears_pending_inbox`，覆盖 approval 出现时旧 inbox 被移除、pending key 被清空、approval 继续挂载。
- 修改 `mewcode/app.py`：新增 `_clear_pending_self_evolution_inbox()`，集中处理 inbox widget 移除和 pending key 清理。
- 修改 `mewcode/app.py`：`on_inline_self_evolution_inbox_widget_responded()` 复用该 helper。
- 修改 `mewcode/app.py`：`_show_self_evolution_approval()` 在挂载 approval 前先清理 pending inbox。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 approval 优先级修复。

用户能看到什么：

- 当候选 skill 进入 approval 阶段时，旧 inbox 不会继续占着界面。
- TUI 同一时间不会同时要求用户处理 inbox 和 approval。

安全边界：

- 不改变 candidate、review run、approval request、promote、rollback、quarantine 或 trusted-auto 行为。
- 不新增用户命令。
- 只调整 TUI 展示优先级和 pending 状态清理。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox -q
1 failed  # 实现前红灯：approval 出现时旧 inbox 未移除

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_response_views_report_or_dismisses tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_allows_only_one_pending_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_skill_approval_response_approves_and_reloads_skills -q
4 passed
```

下一步计划：

- 检查 approval widget 已存在时重复调用 `_show_self_evolution_approval()` 的去重路径是否也会清理旧 inbox。
- 继续减少 TUI 状态测试里的 fake widget 样板。

## 73. 最新推进记录：重复 Approval 调用仍清理 Pending Inbox

日期：2026-08-05

本次补充 approval 去重路径的回归测试。`_show_self_evolution_approval()` 在检测到同一个 approval request 已经 pending 时会直接返回；如果这个 duplicate path 不清理旧 inbox，就可能留下无效 inbox widget。当前实现已经把 `_clear_pending_self_evolution_inbox()` 放在 duplicate 检查之前，本次用测试固定该行为。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_duplicate_self_evolution_approval_still_clears_pending_inbox`。
- 测试覆盖同一个 approval request 已经 pending 时，调用 `_show_self_evolution_approval()` 仍会移除残留 inbox、清空 pending inbox key，并且不会重复挂载 approval widget。
- 本次不修改生产代码；现有实现已满足该边界。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档 duplicate approval 路径覆盖。

用户能看到什么：

- 重复触发同一个 approval request 时，不会重复挂载 approval widget。
- 如果此前残留了 inbox，重复 approval 调用仍会把 inbox 清掉。

安全边界：

- 不改变 candidate、review run、approval request、promote、rollback、quarantine 或 trusted-auto 行为。
- 不新增用户命令。
- 不修改生产代码，只补测试覆盖。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_duplicate_self_evolution_approval_still_clears_pending_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox -q
2 passed
```

下一步计划：

- 抽取 TUI self-evolution fake widget/test fixture，减少测试样板。
- 继续检查 approval reject path 的状态清理和用户反馈文案。

## 74. 最新推进记录：Self-Evolution TUI Fake Fixture 抽取

日期：2026-08-05

本次清理 self-evolution TUI 状态测试中的重复 fake widget 样板。此前 inbox mount、approval mount、pending inbox query 的 fake 逻辑分散在多个测试里，后续每补一个状态边界都要复制同样的 async fake mount 和 removable inbox。现在抽出三个测试 helper，后续新增状态测试可以直接复用。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `_capture_self_evolution_inbox_mount(app)`，统一捕获 inbox widget 挂载参数。
- 修改 `tests/test_evolution.py`：新增 `_capture_self_evolution_approval_mount(app)`，统一捕获 approval widget 挂载参数。
- 修改 `tests/test_evolution.py`：新增 `_install_fake_pending_inbox_query(app)`，统一模拟可移除的 pending inbox widget。
- 修改相关 self-evolution TUI 测试，移除重复 fake class 和 async fake mount。
- 修改本文档和 `docs/self-evolution-config-approval-recap-zh.md`：留档测试 fixture 抽取。

用户能看到什么：

- 运行时行为不变。
- 后续继续补 self-evolution TUI 边界测试会更快，样板更少。

安全边界：

- 不修改生产代码。
- 不改变 candidate、review run、approval request、promote、rollback、quarantine 或 trusted-auto 行为。
- 只清理测试结构。

验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_deduplicates_pending_display tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_inbox_allows_only_one_pending_widget tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_approval_clears_pending_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_duplicate_self_evolution_approval_still_clears_pending_inbox -q
4 passed
```

下一步计划：

- 继续检查 approval reject path 的状态清理和用户反馈文案。
- 把输入框 fake chat/input 也抽成 helper，进一步减少 TUI 测试样板。

## 75. 最新推进记录：Fork Reviewer 并发运行保护

日期：2026-08-05

本次补 self-evolution review pass 的执行边界。此前 `.mewcode/evolution/review_runs.jsonl` 中如果已经存在 `running` 状态的 `fork_reviewer`，再次触发 `review_ready_skill_candidates()` 仍会新建第二个 review run。这样在真实后台/多入口触发时，可能并发生成候选 skill、重复提交 approval request，削弱“候选 skill 先评测再审批”的可信度。现在 review pass 会先检查 active fork reviewer；如果已有运行中的 run，直接返回 `busy`，不创建新 run、不生成候选、不提交审批。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_skips_when_fork_reviewer_is_running`，覆盖 running fork reviewer 存在时不启动第二个 run。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_active_fork_reviewer_run()`，在启动 review run 前执行 active run gate。
- 修改 `mewcode/evolution/auto_review.py`：`_empty_review_result()` 新增 `active_review_run_id` 和 `active_review_report` 字段，便于上层识别 busy 原因。
- 修改 `mewcode/evolution/models.py`：`SelfEvolutionReviewRunStatus` 增加 `busy`，保持状态枚举与返回值一致。

用户能看到什么：

- 如果自进化评审已经在运行，新的触发不会重复启动第二个评审。
- `busy` 结果会带上当前 active review run id 和报告路径，后续 UI/日志可以明确提示用户“已有评审在运行”。

安全边界：

- 不改变候选 skill 生成规则。
- 不改变 eval、execution eval、canary、approval、promote、rollback 或 quarantine 逻辑。
- 不新增用户命令。
- 只限制并发 review pass，避免同一批候选被多路处理。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_skips_when_fork_reviewer_is_running -q
1 failed  # 实现前红灯：已有 running run 时仍返回 idle 并启动新 run

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_skips_when_fork_reviewer_is_running tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_records_fork_reviewer_run -q
2 passed
```

下一步计划：

- 为 busy 状态补 TUI/CLI 用户可见提示，避免用户看不懂“为什么这次没有生成新审批”。
- 继续补 active run 超时/异常恢复策略，避免进程崩溃后 `running` 状态永久阻塞自进化。

## 76. 最新推进记录：Fork Reviewer Busy 状态用户可见化

日期：2026-08-05

本次把上一轮新增的 `busy` 状态接到用户可见层。此前 `format_review_notification()` 不处理 `busy`，CLI 会静默；TUI 中 `_run_self_evolution_review()` 会先进入 inbox 渲染逻辑，可能展示一个全为空的 Self-Evolution Inbox，而不是告诉用户已有 review run 正在运行。现在 `busy` 会生成明确提示，并且 TUI 在 inbox 之前优先展示该提示。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_notification_shows_busy_review_run`，覆盖 `busy` formatter 文案。
- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_shows_busy_message`，覆盖 TUI 忙碌状态不挂空 inbox，而是显示系统消息。
- 修改 `mewcode/evolution/auto_review.py`：`format_review_notification()` 支持 `status == "busy"`，输出 active run id 和 report 路径。
- 修改 `mewcode/app.py`：`_run_self_evolution_review()` 在 inbox 分支前处理 `busy`，避免 busy 被空 inbox 截走。

用户能看到什么：

- CLI：如果自进化 review 已经运行，会在 stderr 输出 `Self-evolution review already running...`。
- TUI：如果自进化 review 已经运行，会显示系统消息，而不是出现一个没有候选、没有审批、没有阻断项的空 inbox。

安全边界：

- 不改变候选生成、评测、审批、推广、回滚或隔离逻辑。
- 不新增用户命令。
- 只改变 busy 状态的提示路径和 TUI 分支优先级。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_busy_review_run -q
1 failed  # 实现前红灯：busy formatter 返回空字符串

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_busy_message -q
1 failed  # 实现前红灯：TUI 挂载空 inbox，没有显示 busy message

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_busy_message tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_busy_review_run tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_skips_when_fork_reviewer_is_running -q
4 passed
```

下一步计划：

- 补 active run 过期恢复：避免进程崩溃后旧 `running` run 永久阻塞新的自进化 review。
- 给 review run lifecycle 增加更清晰的状态分类，区分 `busy` 返回结果和持久化 run 状态。

## 77. 最新推进记录：Stale Fork Reviewer 自动恢复

日期：2026-08-05

本次补上 active fork reviewer 的异常恢复。上一轮新增了 `busy` gate，但如果进程崩溃或异常退出，旧 review run 可能一直停留在 `running`，从而永久阻塞后续自进化。现在 active run 判定会识别超过默认 1 小时的 `running` fork reviewer，将其标记为 `failed` 并写入错误原因，然后允许新的 review pass 正常启动。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_recovers_stale_running_fork_reviewer`，覆盖 stale running run 不再永久阻塞。
- 修改 `mewcode/evolution/auto_review.py`：新增 `FORK_REVIEWER_STALE_SECONDS = 60 * 60`。
- 修改 `mewcode/evolution/auto_review.py`：`_active_fork_reviewer_run()` 对 stale run 调用 `_expire_stale_fork_reviewer_run()`，然后继续查找是否存在其他新鲜 running run。
- 修改 `mewcode/evolution/auto_review.py`：新增 `_fork_reviewer_run_is_stale()` 和 `_expire_stale_fork_reviewer_run()`，集中处理过期判断和失败落盘。

用户能看到什么：

- 如果上一次 self-evolution review 因崩溃卡在 `running` 超过 1 小时，下一次触发会自动恢复，不会一直返回 busy。
- 旧 run 会在 `.mewcode/evolution/review_runs.jsonl` 中保留为 `failed`，错误原因包含 `stale fork reviewer lock expired...`。

安全边界：

- 只恢复 review run 生命周期，不修改候选 skill 内容。
- 不自动 approve、promote 或 quarantine。
- 新鲜 running run 仍会返回 `busy`，不会并发启动第二个 fork reviewer。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_recovers_stale_running_fork_reviewer -q
1 failed  # 实现前红灯：旧 running run 仍返回 busy，阻止新 review pass

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_recovers_stale_running_fork_reviewer tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_skips_when_fork_reviewer_is_running -q
2 passed
```

下一步计划：

- 考虑把 stale recovery 的 run id 也加入 notification，让用户在 CLI/TUI 里看到“已恢复旧卡死评审”。
- 继续补更完整的多轮对话 candidate-skill 匹配测试。

## 78. 最新推进记录：Stale Recovery 通知可见化

日期：2026-08-05

本次把 stale fork reviewer 恢复结果接到通知层。上一轮已经能自动把过期 `running` run 标记为 `failed` 并启动新 review pass，但返回结果没有告诉上层恢复了哪个旧 run；CLI/TUI 也可能因为空 inbox 分支导致用户看不到恢复动作。现在 review result 会携带 `expired_review_run_ids`，formatter 会渲染恢复提示，TUI 会在空 inbox 前优先展示该提示。

修改内容：

- 修改 `tests/test_evolution.py`：扩展 `test_self_evolution_review_recovers_stale_running_fork_reviewer`，要求结果包含 `expired_review_run_ids`。
- 修改 `tests/test_evolution.py`：新增 `test_self_evolution_review_notification_shows_recovered_stale_run`，覆盖 formatter 文案。
- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_shows_recovered_stale_message`，覆盖 TUI 不挂空 inbox，而是显示 stale recovery message。
- 修改 `mewcode/evolution/auto_review.py`：`_active_fork_reviewer_run()` 支持回传 expired run 列表；`review_ready_skill_candidates()` 把 expired ids 写入 result。
- 修改 `mewcode/evolution/auto_review.py`：`format_review_notification()` 支持 `expired_review_run_ids`。
- 修改 `mewcode/app.py`：`_run_self_evolution_review()` 在 inbox 分支前处理 stale recovery message。

用户能看到什么：

- CLI/TUI 会显示 `Self-evolution recovered stale review run(s): ...`。
- 用户能知道本次触发先恢复了旧卡死评审，而不是误以为系统没有动作。

安全边界：

- 不改变候选 skill 生成、评测、审批、推广、回滚或隔离逻辑。
- 不自动批准 stale run 中可能遗留的候选。
- 只暴露恢复结果和调整 TUI 分支优先级。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_recovers_stale_running_fork_reviewer tests/test_evolution.py::TestEvolutionEngine::test_self_evolution_review_notification_shows_recovered_stale_run -q
2 failed  # 实现前红灯：result 无 expired_review_run_ids，formatter 返回空字符串

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_recovered_stale_message -q
1 failed  # 实现前红灯：TUI 挂载空 inbox，没有显示 stale recovery message

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_recovered_stale_message tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_busy_message tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate -q
3 passed
```

下一步计划：

- 开始补多轮对话 candidate-skill 匹配：当前任务如何匹配历史生成的候选 skill。
- 优先从可测试的规则层做起，不引入不透明模型判断。

## 79. 最新推进记录：候选 Skill 与当前任务的确定性匹配 API

日期：2026-08-05

本次补多轮对话 self-evolution 的基础能力：当前任务如何判断是否匹配之前生成的候选 skill。新增 `EvolutionEngine.match_skill_candidates_for_task()`，给定任务文本后扫描 `.mewcode/evolution/candidates/**/manifest.json` 中仍处于 `proposed` 的 skill 候选，基于候选 skill 的 name、description、body 与任务文本做确定性词面匹配，返回分数、匹配词、候选状态和候选文件路径。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_match_skill_candidates_for_task_ranks_relevant_candidate`，覆盖“复盘文档/测试结果”任务能匹配复盘类候选 skill，并排除无关部署类候选。
- 修改 `mewcode/evolution/engine.py`：新增 `match_skill_candidates_for_task()` 只读 API。
- 修改 `mewcode/evolution/engine.py`：新增 `_skill_match_tokens()`，英文按词匹配，中文按连续汉字 bigram 匹配，并过滤少量泛化词如 `任务`、`流程`。

用户能看到什么：

- 未来跨对话时，可以先调用该 API 判断当前任务是否与历史候选 skill 相关。
- 匹配结果包含 `matched_terms`，能解释“为什么这个候选 skill 被认为相关”。

安全边界：

- 该 API 只读，不会自动 approve、promote、activate 或修改 skill。
- 不使用模型判断，避免不可复现的候选匹配结果。
- 只匹配 `proposed` 状态的 skill proposal，不把已 rejected/applied 的候选重新拿来用。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_match_skill_candidates_for_task_ranks_relevant_candidate -q
1 failed  # 实现前红灯：EvolutionEngine 没有 match_skill_candidates_for_task API

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_match_skill_candidates_for_task_ranks_relevant_candidate -q
1 passed
```

下一步计划：

- 将匹配 API 接到 review report 或 TUI inbox，让用户能看到“当前任务匹配到了哪些候选 skill”。
- 后续再考虑自动激活，但必须先经过审批策略和测试门槛，不能直接启用未批准候选。

## 80. 最新推进记录：TUI 不再展示空 Self-Evolution Inbox

日期：2026-08-05

本次修复 self-evolution TUI 的空状态噪音。此前 `_run_self_evolution_review()` 在 self-evolution enabled 且 review result 为 idle 时，会调用 `engine.render_self_evolution_inbox()`；该函数即使没有 pending request、blocked candidate、generated candidate，也会返回一个完整但全是 `None` 的 inbox，导致 TUI 弹出无意义的空面板。现在 TUI 会先检查 inbox counts，三类计数全为 0 时直接返回，不再展示空 inbox。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_tui_self_evolution_review_does_not_show_empty_inbox`，覆盖 idle 且无候选时不显示系统消息、不挂 inbox。
- 修改 `mewcode/app.py`：`_run_self_evolution_review()` 在渲染 inbox 前检查 `pending_requests`、`blocked_candidates`、`generated_candidates` 三个计数。

用户能看到什么：

- 没有待审批、阻断或生成候选时，TUI 不再弹出空 Self-Evolution Inbox。
- 有 pending approval、blocked candidate 或 generated candidate 时，原有 inbox/approval 展示保持不变。

安全边界：

- 不改变 engine 的 inbox 数据结构。
- 不改变候选 skill 生成、评测、审批或推广逻辑。
- 只减少 TUI 空状态打扰。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_does_not_show_empty_inbox -q
1 failed  # 实现前红灯：idle 无候选时仍挂载空 inbox

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_does_not_show_empty_inbox tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_blocked_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_shows_existing_generated_candidate tests/test_evolution.py::TestEvolutionEngine::test_tui_self_evolution_review_opens_existing_pending_request -q
4 passed
```

下一步计划：

- 把候选 skill 匹配结果接入用户可见报告，而不是只暴露 API。
- 继续清理 TUI self-evolution 分支，让 approval、busy、stale、inbox 的优先级更集中。

## 81. 最新推进记录：候选 Skill 匹配报告与 TUI 提示

日期：2026-08-05

本次把候选 skill 匹配从“只读 API”推进到“用户可见”。新增 `render_skill_candidate_task_matches()`，把当前任务匹配到的候选 skill 渲染成 Markdown，包含 score、matched terms、eval 状态、execution eval 状态、approval 状态和 candidate path。TUI 在普通用户消息发送给 Agent 前调用该报告；如果有匹配项，会先显示系统消息，但不会自动加载或启用候选 skill。

修改内容：

- 修改 `tests/test_evolution.py`：新增 `test_render_skill_candidate_task_matches_shows_gate_status`，覆盖匹配报告必须显示 gate 状态和 `not auto-activated`。
- 修改 `tests/test_evolution.py`：新增 `test_tui_user_message_shows_self_evolution_candidate_matches`，覆盖用户消息命中候选 skill 时 TUI 显示匹配报告，并仍继续发送原消息给 Agent。
- 修改 `mewcode/evolution/engine.py`：新增 `render_skill_candidate_task_matches()`。
- 修改 `mewcode/app.py`：新增 `_show_self_evolution_task_matches()`，并在非 slash command 消息进入 Agent 前执行只读匹配提示。

用户能看到什么：

- 跨对话时，如果当前任务与历史候选 skill 相关，TUI 会展示 `Self-Evolution Candidate Skill Matches`。
- 用户能看到为什么匹配：`Matched terms`、`Score` 和候选 gate 状态。
- 系统明确显示 `Runtime: not auto-activated`，避免误以为未审批候选已经生效。

安全边界：

- 不自动调用 `LoadSkill`。
- 不自动 approve、promote 或 activate 候选 skill。
- 只读扫描 `.mewcode/evolution/candidates/**/manifest.json` 和 proposals。
- 匹配提示失败时只写 debug log，不影响主消息发送。

TDD 与验证记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status -q
1 failed  # 实现前红灯：缺少 render_skill_candidate_task_matches API

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_shows_self_evolution_candidate_matches -q
1 failed  # 实现前红灯：TUI 不显示候选 skill 匹配报告

PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_tui_user_message_shows_self_evolution_candidate_matches tests/test_evolution.py::TestEvolutionEngine::test_render_skill_candidate_task_matches_shows_gate_status -q
2 passed
```

下一步计划：

- 进一步限制提示噪音：只在候选达到更高分数或用户开启 self-evolution 时展示。
- 给匹配报告加 “下一步建议”，例如等待评测、提交审批或拒绝候选。
