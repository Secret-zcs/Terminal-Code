# 自进化开发进度总复盘

> 日期：2026-07-21
> 基线提交：`f01966c 为候选 skill 增加 eval case 门禁`
> 最新阶段：候选 skill 执行评估报告门禁、只读 preview、usage log 与 quarantine
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
- candidate skill 必须通过 deterministic eval，并且至少完成三轮 execution eval，才能被 promote。
- execution eval 会生成用户可见的 JSON/Markdown 报告，用户可用 `/evolve show-eval` 先看测试效果再 approve/promote。
- 用户可用 `/evolve preview <proposal_id>` 在 approve/apply/promote 前查看 memory 追加内容或 skill unified diff。
- `LoadSkill` 成功激活 skill 会写入 `.mewcode/evolution/skill_usage.jsonl`，用于后续追踪 skill 影响。
- 用户可用 `/evolve quarantine <skill-name> [:: reason]` 将不可靠的项目级正式 skill 移入隔离区。
- promote 前会尝试 checkpoint，promote 后会尝试 reload skill loader。
- 运行时自进化明确只允许 `memory | skill`，不允许 `code | tool | prompt` 自动落地。

整体进度可以概括为：**安全版 Hermes skill evolution 的主干闭环已完成，并新增了多轮评估报告门禁、只读预览、usage log 与手动 quarantine；Hermes 原版的后台自动 review、真实模型沙盒任务执行、自动 usage 归因/自动降级还未完成。**

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
- execution eval 在 candidate 目录内生成 deterministic sandbox artifacts，包含任务、候选 skill 快照、渲染 SOP 和结构化结果。
- `/evolve preview <proposal_id>` 已支持 memory 追加预览和 skill unified diff，且不会写正式 memory/skill；candidate 缺失时也只从 proposal payload 内存渲染。
- `LoadSkill` 成功加载 skill 后会记录 `load` usage event。
- `/evolve quarantine <skill-name> [:: reason]` 只隔离项目级正式 skill，不隔离内置 skill 或用户全局 skill。
- eval case 路径校验 skill name，避免路径逃逸。
- 危险命令片段会被 validate 阻断。
- 宽泛词如“永远/所有任务/必须/禁止”会产生 warning，提示人工 review scope。
- apply/promote 前会尝试 checkpoint。

### 仍未实现

- 没有后台 background review 自动从对话中蒸馏 skill。
- 没有 fork reviewer 隔离运行自进化审查。
- execution eval 目前是确定性 SOP 覆盖检查，不是真实模型沙盒任务执行。
- usage log 目前记录 skill load/quarantine，尚未自动记录任务成功、失败和用户纠正。
- quarantine 目前是手动命令，尚未根据 usage failure 阈值自动建议或执行隔离。
- 没有自动从失败任务或 rewind 事件反推 skill 需要 patch。
- 没有受限 fork agent 真实执行 eval case；当前 sandbox runner 仍是 deterministic checker。

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
- `/evolve` 命令 observe/propose/list/preview/apply/eval/run-eval/show-eval/promote/quarantine。
- `/evolve preview` 的只读语义：memory preview 不创建 memory 文件，skill candidate 缺失时不重建 candidate 目录。
- `/evolve quarantine` 移动正式项目 skill 到隔离区并 reload loader。
- `/learn` create/patch 优先级。
- `/learn` evidence 关联。
- `/learn` 提示指向 eval/run-eval/show-eval/promote 新流程。

本次最新验证：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
37 passed
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

### P1：引入 usage log 与 quarantine（基础版已完成）

- 已增加 `.mewcode/evolution/skill_usage.jsonl`。
- 已记录 `LoadSkill` 成功加载事件和 `/evolve quarantine` 隔离事件。
- 已增加 `/evolve quarantine <skill-name> [:: reason]`，把项目级正式 skill 移入 `.mewcode/evolution/quarantine/<skill-name>/`。
- 已在 command 层隔离后 reload skill loader，避免后续任务继续使用该正式 skill。
- 尚未自动记录任务成功/失败、用户纠正，也未根据失败阈值自动建议 quarantine 或 patch。

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

但它还不是完整 Hermes：缺少后台 review、真实模型沙盒任务回放、自动 usage feedback 归因和自动 patch 推荐。下一阶段不建议直接追求“自动生成并启用 skill”，而应优先补齐 **任务成功/失败 usage 归因、quarantine 建议器、受限 fork-agent eval runner**，让 skill 在隔离环境中真实完成一些任务后再进入长期能力库。
