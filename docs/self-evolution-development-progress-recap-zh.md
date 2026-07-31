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
