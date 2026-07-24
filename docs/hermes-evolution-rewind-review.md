# Hermes 自进化机制与 Claude Rewind 机制复盘

> 日期：2026-07-17
> 相关代码：
> - `mewcode/evolution/`
> - `mewcode/commands/handlers/evolve.py`
> - `mewcode/checkpoint/`
> - `mewcode/commands/handlers/checkpoint.py`
> - `mewcode/commands/handlers/rewind.py`
> 验证命令：
> - `PYTHONPATH=. pytest tests/test_evolution.py -q`
> - `PYTHONPATH=. pytest tests/test_checkpoint.py -q`

---

## 1. 复盘结论

本次补充的是一个**安全型自进化闭环**，不是让 Agent 直接无约束修改自身代码。

当前实现把 Hermes 风格自进化拆成五步：

```text
memory: observe -> propose -> validate -> approve -> apply
skill:  learn/propose -> candidate -> validate -> eval-case -> eval -> run-eval -> show-eval -> approve -> promote
```

并用 Claude Code 风格 rewind/checkpoint 机制作为安全保护：

```text
自进化应用前
  -> track 目标文件
  -> 创建 checkpoint
  -> apply memory 或 promote skill
  -> 如果结果不好，可用 /rewind 回退
```

当前可自动落地的目标是 `memory` 和 `skill`，但路径不同：memory 经 `/evolve apply` 写入 `.mewcode/memories.md`；skill 先写入 candidate，并且至少记录三个任务 eval case，只有 `/evolve eval`、`/evolve run-eval` 通过且用户通过 `/evolve show-eval` 看过报告后，才能经 approve/promote 进入 `.mewcode/skills/<name>/SKILL.md`。

这个边界是有意设计的：Hermes 风格运行时自进化应沉淀外部、可审计的行为资产，而不是直接修改工具实现、系统提示词或代码。

---

## 2. 需求拆解

用户提出的目标有两部分：

1. 给项目增加 Hermes 的自进化机制。
2. 给项目增加 Claude 的 rewind 机制。

实际审计后发现：

- rewind 机制已经有较完整实现：`CheckpointManager`、`CheckpointStore`、`/checkpoint`、`/rewind --preview`、`/rewind --undo` 都已存在。
- Hermes 自进化机制此前没有独立子系统，因此本次主要新增的是 `mewcode/evolution/` 和 `/evolve` 命令。
- 最重要的工程问题不是“能不能自动改”，而是“自动改之前有没有证据、审批、验证、回退点和留档”。

因此本次实现目标调整为：

```text
先建立可审计的自进化提案系统
再用 rewind/checkpoint 保护任何实际落地动作
把可复用经验落到 memory 和 skill，尤其是 skill
```

---

## 3. Hermes 自进化机制设计

### 3.1 核心思想

Hermes 风格自进化的核心不是“模型自己改代码”，而是：

```text
从真实执行经验中提取可复用规律
  -> 形成候选改进
  -> 经过验证和审批
  -> 写入长期可复用资产
  -> 在后续任务中影响行为
```

在本项目中，这些长期可复用资产包括：

| 资产 | 当前状态 | 风险 |
|---|---|---|
| `memory` | 已支持自动应用 | 低 |
| `skill` | 已支持审批后创建项目级 skill | 中 |

运行时自进化只接受这两类资产，原因是：

- `.mewcode/memories.md` 本来就是项目长期记忆入口。
- `.mewcode/skills/<name>/SKILL.md` 是可审计、可 reload、可回滚的任务流程资产。
- 二者都不会直接修改工具实现、系统 prompt 或代码执行路径。
- 即使写错，也能通过 rewind 或手动编辑恢复。
- `code/tool/prompt` 相关想法只能作为人工开发建议处理，不属于 `/evolve apply`。

---

### 3.2 数据模型

新增文件：

```text
mewcode/evolution/models.py
```

核心模型：

```python
EvolutionEvidence
EvolutionProposal
EvolutionValidation
```

#### EvolutionEvidence

表示自进化证据。

字段：

| 字段 | 含义 |
|---|---|
| `id` | 证据 ID，例如 `ev_xxx` |
| `kind` | 证据类型 |
| `summary` | 证据摘要 |
| `source` | 来源，例如 `slash-command` |
| `metadata` | 附加结构化信息 |
| `created_at` | 创建时间 |

支持的 `kind`：

```python
"manual"
"success"
"failure"
"user_feedback"
"test_result"
"rewind"
```

设计理由：

- 自进化不能只凭模型主观判断，必须有“为什么要改”的证据。
- 后续可以从测试失败、用户纠正、rewind 事件、成功轨迹中自动生成 evidence。

#### EvolutionProposal

表示自进化提案。

字段：

| 字段 | 含义 |
|---|---|
| `id` | 提案 ID，例如 `prop_xxx` |
| `title` | 提案标题 |
| `rationale` | 提案理由 |
| `target` | 变更目标 |
| `change` | 具体变更内容 |
| `evidence_ids` | 关联证据 ID |
| `risk` | 风险等级 |
| `status` | 当前状态 |
| `created_at` | 创建时间 |
| `applied_at` | 应用时间 |

支持的状态：

```python
"proposed"
"approved"
"rejected"
"applied"
```

设计理由：

- 提案必须从创建到应用有状态流转。
- 运行时 target 被限制为 `memory` 和 `skill`。
- `evidence_ids` 让每个变更都能追溯来源。

---

### 3.3 存储层

新增文件：

```text
mewcode/evolution/store.py
```

存储位置：

```text
.mewcode/evolution/evidence.jsonl
.mewcode/evolution/proposals.jsonl
```

为什么用 JSONL：

- 与项目已有 checkpoint/session 的持久化风格一致。
- 追加写简单，便于审计。
- 每行都是独立 JSON，坏一行不会影响整份文件。

关键 API：

```python
save_evidence()
load_evidence()
save_proposal()
load_proposals()
get_evidence()
get_proposal()
update_proposal()
recent_evidence_ids()
```

写入策略：

- 先写 `.tmp`。
- 再 `rename` 到正式文件。
- 避免中途崩溃留下半写状态。

---

### 3.4 引擎层

新增文件：

```text
mewcode/evolution/engine.py
```

核心 API：

```python
record_evidence()
propose()
validate()
approve()
reject()
apply()
```

#### observe

```python
record_evidence(summary, kind="manual", source="manual")
```

作用：

- 把一次经验、失败、用户反馈或测试结果记录为 evidence。
- evidence 本身不改变系统行为。

#### propose

```python
propose(title, change, target="memory", evidence_ids=[...])
```

作用：

- 基于 evidence 创建改进提案。
- 默认 target 是 `memory`。
- 如果不传 evidence，会自动关联最近 evidence。

#### validate

```python
validate(proposal)
```

当前验证规则：

- proposal 状态必须是 `proposed` 或 `approved`。
- 只有 `target == "memory"` 或 `target == "skill"` 可以进入 apply。
- `change` 不能为空。
- `skill` 会校验名称、描述、正文、`mode`、`context`、`allowedTools` 和同名冲突。
- 非低风险提案会产生 warning。
- 缺失 evidence 会产生 warning。

为什么 `code/tool/prompt` 不进入运行时自进化：

- 它们会改变 Agent 的执行面或提示面，影响范围大于 memory/skill。
- 即使有 rewind，也无法保证错误工具或 prompt 在回滚前没有造成副作用。
- 如果确实需要改代码、工具或 prompt，应走普通人工开发流程、测试和代码审查，而不是 `/evolve apply`。

#### approve / reject

```python
approve(proposal_id)
reject(proposal_id)
```

作用：

- 人工控制提案是否允许进入 apply 阶段。
- 避免模型把观察到的单次经验直接固化。

#### apply

```python
apply(proposal_id)
```

当前 `apply()` 只支持 memory；skill 已从 direct apply 改为 candidate/promote：

```text
approved memory proposal
  -> append 到 .mewcode/memories.md 的 ### 项目知识
  -> proposal.status = applied

approved skill proposal
  -> apply() 返回 promote 提示
  -> 必须走 eval -> run-eval -> show-eval -> approve -> promote
```

写入规则：

- 如果 memory 文件不存在，则创建。
- 如果没有 `### 项目知识` 标题，则追加该标题。
- 如果同一条 bullet 已存在，则不重复写入。

---

## 4. `/evolve` 命令设计

新增文件：

```text
mewcode/commands/handlers/evolve.py
```

注册位置：

```text
mewcode/commands/handlers/__init__.py
```

命令：

```text
/evolve
/evolve observe <summary>
/evolve propose <title> :: <memory change>
/evolve list
/evolve show <proposal_id>
/evolve approve <proposal_id>
/evolve reject <proposal_id>
/evolve apply <proposal_id>
/evolve add-eval-case <proposal_id> :: <task> :: <must_contain_csv> [:: <must_not_contain_csv>]
/evolve eval <proposal_id>
/evolve run-eval <proposal_id>
/evolve show-eval <proposal_id>
/evolve promote <proposal_id>
```

### 4.1 使用示例

记录经验：

```text
/evolve observe 用户指出：自进化改动前必须创建 checkpoint。
```

创建提案：

```text
/evolve propose checkpoint-before-evolve :: 自进化应用前必须创建 rewind checkpoint。
```

查看提案：

```text
/evolve list
/evolve show prop_xxxxx
```

审批并应用：

```text
/evolve approve prop_xxxxx
/evolve apply prop_xxxxx
```

应用后会写入：

```text
.mewcode/memories.md
```

---

## 5. Rewind 机制现状复盘

### 5.1 已有能力

当前 rewind 机制已经具备 Claude Code 风格的核心能力：

| 能力 | 当前状态 |
|---|---|
| 手动创建 checkpoint | 已有 `/checkpoint` |
| checkpoint 列表 | 已有 `/checkpoint` 和 `/rewind` |
| 回退代码 + 对话 | 已有 `/rewind N` |
| 仅回退代码 | 已有 `/rewind N --code` |
| 仅回退对话 | 已有 `/rewind N --conv` |
| 预览回退影响 | 已有 `/rewind N --preview` |
| 撤销最近 rewind | 已有 `/rewind --undo` |
| checkpoint 元数据持久化 | 已有 `CheckpointStore` |
| 自动 checkpoint | 已接入 turn_end、pre_write、pre_bash、pre_compact |

---

### 5.2 架构组成

核心文件：

```text
mewcode/checkpoint/models.py
mewcode/checkpoint/store.py
mewcode/checkpoint/manager.py
mewcode/commands/handlers/checkpoint.py
mewcode/commands/handlers/rewind.py
```

组件职责：

| 组件 | 职责 |
|---|---|
| `Checkpoint` | 描述一个 checkpoint 元数据 |
| `CheckpointStore` | 持久化 checkpoint 和 undo checkpoint |
| `CheckpointManager` | 编排创建、预览、回退、撤销 |
| `FileHistory` | 文件级备份和恢复 |
| `/checkpoint` | 人工创建或查看 checkpoint |
| `/rewind` | 查看、预览、执行回退 |

---

### 5.3 自动 checkpoint 触发点

Agent 当前会在这些时机创建 checkpoint：

```text
turn_end
pre_write
pre_bash
pre_compact
```

具体含义：

| 触发点 | 目的 |
|---|---|
| `turn_end` | 每轮完成后保留一个可回退状态 |
| `pre_write` | 写文件前保护用户工作区 |
| `pre_bash` | 危险命令前保护工作区 |
| `pre_compact` | 上下文压缩前保护对话状态 |

危险 Bash 判断来自 `_is_destructive_bash()`，例如：

```text
rm
mv
pip install
npm install
git reset
git clean
curl
wget
docker rm
```

---

### 5.4 rewind 执行流程

用户执行：

```text
/rewind N
```

内部流程：

```text
1. 根据 seq 找到 Checkpoint
2. 保存 rewind 前 undo checkpoint
3. 如果 option 包含 code：
     FileHistory.rewind(seq - 1)
4. 如果 option 包含 conv：
     conversation.replace_history(history[:message_index])
5. 删除目标 checkpoint 之后的 checkpoint 元数据
6. 重置 seq counter
7. 返回 RewindResult
```

为什么同时支持 code 和 conv：

- 有时只想撤销代码改动，不想丢对话。
- 有时只想回到旧对话状态，不想改文件。
- 默认两者都回退，最符合“回到某个历史点”的直觉。

---

### 5.5 preview 设计

命令：

```text
/rewind N --preview
```

展示：

- 哪些文件会恢复。
- 文件大小变化。
- 会删除多少条对话消息。
- 回退后保留的最后一条消息预览。

设计理由：

- rewind 是破坏性操作，必须先能看影响面。
- 用户可以在确认前判断是否使用 `--code` 或 `--conv`。

---

## 6. 自进化与 rewind 的组合方式

本次最重要的设计是把自进化和 rewind 绑定在一起。

### 6.1 应用前 checkpoint

`/evolve apply` 写入 memory 前、`/evolve promote` 启用 skill candidate 前会尝试：

```text
1. target_path = .mewcode/memories.md 或 .mewcode/skills/<name>/SKILL.md
2. file_history.track_edit(target_path)
3. checkpoint_manager.create_checkpoint(
       label="Hermes evolution: <title>",
       trigger="manual",
   )
4. engine.apply(proposal_id) 或 engine.promote(proposal_id)
```

这样自进化应用后，如果用户发现 memory 或 skill 写错，可以：

```text
/rewind
/rewind <N> --preview
/rewind <N>
```

恢复到应用前状态。

### 6.2 为什么不直接让 Hermes 改代码

直接让自进化系统修改代码存在几个风险：

| 风险 | 后果 |
|---|---|
| 单次失败被固化 | Agent 后续持续犯同类错误 |
| prompt 被污染 | 行为变化不易定位 |
| tool 描述被误改 | 模型错误调用工具 |
| 代码自改不跑测试 | 回归风险进入主路径 |
| 无 checkpoint | 无法快速恢复 |

当前做法把运行时自进化限制为外部行为资产：

```text
memory -> approved 后可 apply
skill  -> candidate + eval passed + execution eval report passed + approved 后可 promote
code/tool/prompt -> 不属于 /evolve target
```

这是更接近 Hermes 的选择：把复杂经验固化为 skill，而不是让 Agent 直接改自身执行面。

---

## 7. 测试复盘

新增测试：

```text
tests/test_evolution.py
```

覆盖内容：

| 测试 | 覆盖点 |
|---|---|
| `test_records_evidence_and_proposal` | evidence 和 proposal 能持久化 |
| `test_approved_memory_proposal_applies_to_project_memory` | approved memory proposal 能写入 `.mewcode/memories.md` |
| `test_non_memory_target_is_proposal_only` | 非 memory 目标不能自动应用 |
| `test_observe_and_propose_flow` | `/evolve observe/propose/list` 可用 |
| `test_apply_requires_approval_then_updates_memory` | `/evolve apply` 必须先 approve |

验证结果：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
5 passed in 0.12s
```

rewind 回归测试：

```text
PYTHONPATH=. pytest tests/test_checkpoint.py -q
36 passed in 1.80s
```

测试结论：

- 新增自进化机制可独立运行。
- `/evolve` 命令主流程可用。
- 已有 checkpoint/rewind 测试未受影响。

---

## 8. 当前实现的优点

### 8.1 自进化闭环是显式的

当前实现没有隐藏式自动改动。

所有演进都要经历：

```text
evidence -> proposal -> approve -> apply
```

这让用户能看到：

- 为什么改。
- 改什么。
- 改到哪里。
- 当前状态是什么。

### 8.2 风险边界清楚

当前只有 memory 能 apply，其他目标只生成 proposal。

这保证了第一版不会突然改变：

- 工具行为。
- Agent 主提示词。
- skill SOP。
- 代码执行路径。

### 8.3 与 rewind 形成安全组合

自进化应用前创建 checkpoint，让“系统改进自己”这件事有回退点。

这是比单独自进化更重要的安全基础。

### 8.4 存储可审计

所有 evidence 和 proposal 都在 JSONL 中：

```text
.mewcode/evolution/evidence.jsonl
.mewcode/evolution/proposals.jsonl
```

这便于后续：

- 查看演进历史。
- 分析哪些经验被固化。
- 做离线评估。
- 回放提案质量。

---

## 9. 当前实现的限制

### 9.1 还不是自动学习

当前需要用户主动执行：

```text
/evolve observe ...
```

还没有自动从以下事件生成 evidence：

- 测试失败。
- 工具错误。
- 用户纠正。
- rewind 发生。
- 同类任务重复成功。

这是下一阶段最值得补的能力。

### 9.2 memory 应用粒度较粗

当前 apply 只是把一条 bullet 写入 `### 项目知识`。

后续应支持：

- 写入 `### 用户偏好`。
- 写入 `### 纠正反馈`。
- 写入 `### 参考资料`。
- 根据 proposal target 自动选择 section。

### 9.3 diff preview 已补齐

`/evolve show` 能展示 proposal change，当前已新增专门的只读预览命令：

```text
/evolve preview <proposal_id>
```

当前行为：

- memory proposal 展示目标 `.mewcode/memories.md` 和即将追加的 bullet。
- skill proposal 展示 candidate 路径、formal target 和 formal/candidate unified diff。
- preview 不写正式 memory/skill，不改变 proposal 状态；candidate 缺失时也只从 proposal payload 内存渲染，不重建 candidate 目录。

这让 apply/promote 前的人工 review 不再只依赖 proposal JSON，也与 rewind 的预览体验形成对称。

相关边界仍需注意：

```text
/rewind --preview
```

仍只展示 rewind 影响概览，还没有内容级文件 diff。

### 9.4 Skill patch 已有主干落地

当前 `skill` 已支持两条路径：

- create：创建新的项目级 skill candidate，promote 后写入 `.mewcode/skills/<name>/SKILL.md`。
- patch：对已有项目级 skill 创建 patch candidate，promote 后覆盖对应项目 skill。

已具备的保护：

- frontmatter 解析校验。
- 同名 create 冲突检查。
- patch 只允许已有项目级 skill，不 patch 内置 skill 或用户全局 skill。
- diff preview。
- checkpoint 尝试。
- eval case gate 与 execution eval report gate。

后续如果要更接近 Hermes，还需要补齐：

| 能力 | 需要的保护 |
|---|---|
| 创建 skill | 更严格 allowedTools 校验、reload 失败回滚 |
| patch skill | 三方 diff/冲突提示、自动 usage 回归、失败 quarantine |
| skill references/templates/scripts | 路径约束、文件类型约束、大小限制、人工 approve |

---

## 10. 下一阶段建议

### 10.1 从 rewind 事件生成 evidence

当用户执行 `/rewind` 后，可以自动记录：

```text
kind = "rewind"
summary = "User rewound from checkpoint X after change Y"
```

价值：

- rewind 是强负反馈信号。
- 如果某类行为频繁导致 rewind，应进入自进化提案。

### 10.2 从测试失败生成 evidence

当 Bash 工具执行 pytest 失败时，记录：

```text
kind = "test_result"
summary = "pytest failed after modifying context manager"
```

价值：

- 能把失败模式转为长期经验。
- 后续可以生成“修改该模块后必须跑某些测试”的 memory 或 skill。

### 10.3 强化 `/evolve preview`

当前已支持：

```text
/evolve preview <proposal_id>
```

示例输出：

```diff
### 项目知识
+ - 自进化应用前必须创建 rewind checkpoint。
```

已实现价值：

- apply 前可视化影响。
- 与 `/rewind --preview` 形成对称体验。

后续可继续增强：

- preview 输出 candidate eval 状态摘要。
- preview 同时展示最近 execution eval report 路径。
- 对 patch skill 展示更清晰的“新增/删除/修改段落”摘要。

### 10.4 引入 proposal verifier

后续可增加专门 verifier：

```text
memory verifier
skill verifier
```

每种 verifier 都负责：

- 校验格式。
- 判断风险。
- 生成测试建议。
- 决定是否允许 apply。

---

## 11. 与 Claude Rewind 的差距

当前 rewind 已接近 Claude Code 的核心体验，但仍有差距：

| 维度 | 当前项目 | 更接近 Claude 的方向 |
|---|---|---|
| checkpoint UI | 文本列表 | 更清晰的 diff/preview 交互 |
| undo | 已有 | 需要更强的多级 undo |
| 文件 diff | 只有大小变化 | 增加内容 diff |
| 自动触发 | 已有部分触发点 | 更细粒度地覆盖 delegate、hook、自进化 |
| 对话恢复 | 截断 history | 截断后重渲 UI 和状态同步更完善 |
| 持久化 | checkpoint 元数据持久化 | FileHistory snapshot 跨重启完整恢复需持续验证 |

最需要补的是：

```text
/rewind N --diff
```

因为 preview 只看文件大小，不够判断具体代码变化。

---

## 12. 与 Hermes 自进化的差距

当前实现是 Hermes 风格的安全闭环，并已支持将 approved 且通过 eval/execution eval 的 skill candidate 落地为项目级 `.mewcode/skills/<name>/SKILL.md`。2026-07-18 后，项目进一步补齐了 `/learn` 显式学习入口和已有项目 skill 的 patch 路径；2026-07-21 后，promote 前还必须展示多轮 execution eval 报告；当前版本又补齐了基础 usage log 和 `/evolve quarantine` 手动隔离。

差距：

| 维度 | 当前项目 | 完整自进化方向 |
|---|---|---|
| evidence 来源 | 手动 observe | 自动从 trace、tests、rewind、feedback 提取 |
| mutation | 手动 propose | 自动生成候选改进 |
| selection | 人工 approve | 指标驱动排序 + 人工确认 |
| validation | 简单规则 + eval case + execution eval report + sandbox artifacts | 针对 memory/skill 的 verifier 和回归测试 |
| application | 写 memory；candidate skill 经 eval/run-eval/show-eval/approve/promote 后创建或 patch 项目级 skill；支持 `/learn` 蒸馏 | skill references/templates/scripts 与回放验证 |
| evaluation | 单元测试 + 确定性 sandbox artifact 报告 + usage/quarantine 记录 | 长期任务 benchmark、真实回放评估和自动降级建议 |

下一步不应直接跳到自动改代码，而应先补：

```text
自动 evidence 收集
  -> proposal 质量评分
  -> preview
  -> memory/skill verifier
```

其中 skill 方向已实现：

```text
会话复盘/learn
  -> 识别可复用流程
  -> 优先 patch 已加载 skill
  -> 无合适 skill 时创建新 skill
  -> 写入 candidate
  -> eval gate
  -> execution eval report gate
  -> approve 后 promote
  -> reload
  -> usage log / quarantine
```

准确边界是：当前项目实现的是手动 `/learn` 触发的候选学习闭环，并能对正式 skill 做基础 load 追踪和手动隔离；Hermes 原版的后台 fork review、自动筛选会话片段、自动蒸馏正文、任务回放 eval 和自动降级建议仍未接入主循环。

---

## 13. 变更留档

### 2026-07-17

- 新增自进化模型：`mewcode/evolution/models.py`。
- 新增自进化存储：`mewcode/evolution/store.py`。
- 新增自进化引擎：`mewcode/evolution/engine.py`。
- 新增模块导出：`mewcode/evolution/__init__.py`。
- 新增命令：`mewcode/commands/handlers/evolve.py`。
- 补齐命令：`mewcode/commands/handlers/do.py`，使 `/do` 与既有 `/plan` 测试和交互语义对齐。
- 更新命令注册：`mewcode/commands/handlers/__init__.py`。
- 新增测试：`tests/test_evolution.py`。
- 更新测试：`tests/test_commands.py`，将 `/checkpoint` 和 `/evolve` 纳入当前命令集合期望。
- 新增复盘文档：`docs/hermes-evolution-rewind-review.md`。
- 验证结果：`PYTHONPATH=. pytest tests/test_evolution.py -q` 通过，5 个测试成功。
- 验证结果：`PYTHONPATH=. pytest tests/test_checkpoint.py -q` 通过，36 个测试成功。
- 历史边界：第一版只允许 approved memory proposal 自动应用；当时 skill 尚未落地，code/tool/prompt 不应进入 runtime self-evolution。

### 2026-07-17 补充：Skill 自进化落地

- 修改 `mewcode/evolution/engine.py`：新增 `propose_skill()`，支持把可复用解决流程保存为 `target="skill"` 的结构化 proposal。
- 修改 `mewcode/evolution/engine.py`：`validate()` 改为按 target 分派，`skill` proposal 会校验名称、描述、正文、`mode`、`context`、`allowedTools` 和同名冲突。
- 修改 `mewcode/evolution/engine.py`：approved skill proposal 可写入 `.mewcode/skills/<name>/SKILL.md`，并返回实际写入路径。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve propose-skill <name> :: <description> :: <skill body>`。
- 修改 `mewcode/commands/handlers/evolve.py`：`/evolve apply` 改为对真实 target path 创建 checkpoint，skill apply 成功后尝试 reload skill loader。
- 修改 `tests/test_evolution.py`：新增 skill proposal 写入、拒绝覆盖已有 skill、命令 apply 后 reload、损坏 skill proposal 可读错误返回的测试。
- 修改 `README.md`：更新 Hermes 自进化能力说明和命令用法。
- 新增文档：`docs/hermes-skill-evolution-implementation.md`。
- 验证结果：先运行 `PYTHONPATH=. pytest tests/test_evolution.py -q` 得到 3 个预期失败；实现后当前通过，9 个测试成功。
- 验证结果：`PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q` 通过，183 个测试成功。
- 全量测试记录：`PYTHONPATH=. pytest -q -x` 停在 `tests/test_agent.py::test_multi_step_autonomous`；失败原因为 `WriteFile` 触发“写前必须先 ReadFile”的既有安全策略，与本次 self-evolution/skill 修改无直接依赖。

### 2026-07-18 补充：收紧为 Memory + Skill only

- 修改 `mewcode/evolution/models.py`：`ProposalTarget` 收紧为 `Literal["memory", "skill"]`。
- 修改 `mewcode/evolution/engine.py`：新增运行时 target 白名单，拒绝创建 `code/tool/prompt` 自进化 proposal。
- 修改 `mewcode/commands/handlers/evolve.py`：帮助文案明确 runtime evolution 仅限 memory 和 skill。
- 修改 `tests/test_evolution.py`：将非 memory/skill target 的测试改为创建阶段直接拒绝。
- 修改 `README.md`、`docs/hermes-skill-evolution-implementation.md` 和本文档：统一说明 `code/tool/prompt` 不属于 `/evolve apply`，只能作为人工开发建议处理。
- 验证结果：`PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q` 通过，183 个测试成功。
- 全量测试记录：`PYTHONPATH=. pytest -q -x` 仍停在 `tests/test_agent.py::test_multi_step_autonomous`；失败原因为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突。

### 2026-07-18 补充：Hermes `/learn` 与 Skill Patch 优先级

- 修改 `mewcode/evolution/engine.py`：新增 `propose_skill_patch()`，将 skill proposal payload 扩展为 `action=create|patch`。
- 修改 `mewcode/evolution/engine.py`：新增项目 skill 命中逻辑，只允许 patch `.mewcode/skills/<name>/SKILL.md` 或 `.mewcode/skills/<name>.md`，不 patch 内置 skill 或用户全局 skill。
- 修改 `mewcode/evolution/engine.py`：`validate()` 区分 create 与 patch；create 遇到同名 skill 时拒绝，patch 找不到项目 skill 时拒绝。
- 新增 `mewcode/commands/handlers/learn.py`：实现 `/learn <name> :: <description> :: <skill body>`，同名项目 skill 存在时优先创建 patch proposal，否则创建 create proposal，并自动记录 `source="learn-command"` 的 evidence。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve propose-skill-patch <name> :: <description> :: <skill body>`。
- 修改 `mewcode/commands/handlers/__init__.py`：注册 `/learn` 命令。
- 修改 `tests/test_evolution.py`：新增 skill patch 写回、缺失 skill patch 拒绝、`/learn` patch/create 优先级和 evidence 关联测试。
- 修改 `tests/test_commands.py`：将 `/learn` 纳入命令注册集合。
- 修改 `README.md` 和 `docs/hermes-skill-evolution-implementation.md`：同步记录 `/learn`、`propose-skill-patch`、patch 优先级和安全边界。
- TDD 红灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 得到 4 个预期失败；`PYTHONPATH=. pytest tests/test_commands.py::TestRegisterAllCommands::test_all_commands_registered -q` 得到 1 个预期失败。
- 追加红灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 得到 1 个预期失败，原因是 `/learn` 尚未记录 evidence 并关联到 proposal。
- 绿灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 通过，14 个测试成功；命令注册单测通过，1 个测试成功。
- 扩展回归记录：`PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q` 通过，188 个测试成功。
- 格式检查记录：`git diff --check` 无输出。
- 全量测试记录：`PYTHONPATH=. pytest -q -x` 仍停在 `tests/test_agent.py::test_multi_step_autonomous`；失败原因为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突。

### 2026-07-18 补充：Verified Skill Evolution Candidate / Promote

- 修改 `mewcode/evolution/engine.py`：新增 `.mewcode/evolution/candidates/<proposal_id>/SKILL.md` 与 `manifest.json` 候选区。
- 修改 `mewcode/evolution/engine.py`：新增 `candidate_dir()`、`candidate_skill_path()`、`candidate_manifest_path()` 和 `promote()`。
- 修改 `mewcode/evolution/engine.py`：skill proposal 创建后只写 candidate；`apply()` 对 skill proposal 返回 promote 提示，不再直接写正式 skill。
- 修改 `mewcode/evolution/engine.py`：新增危险命令静态策略，阻断包含 `rm -rf /`、`sudo rm -rf`、`chmod 777 /`、`curl | sh` 等片段的 candidate。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve promote <proposal_id>`，promote 前创建 checkpoint，promote 后 reload skill loader。
- 修改 `tests/test_evolution.py`：新增 candidate 写入、direct apply 拒绝、promote 启用、promote 必须先 approve、patch promote、危险命令阻断和命令层 promote/reload 测试。
- 修改 `README.md`、`docs/hermes-skill-evolution-implementation.md`，新增 `docs/verified-skill-evolution-recap-zh.md`：同步记录 candidate/promote 的设计、边界和验证结果。
- TDD 红灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 得到 8 个预期失败，覆盖缺失 candidate/promote API、direct apply 未拒绝、危险命令未阻断和命令层 promote 未接入。
- 绿灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 通过，19 个测试成功。
- 扩展回归记录：`PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q` 通过，193 个测试成功。
- 格式检查记录：`git diff --check` 无输出。
- 全量测试记录：`PYTHONPATH=. pytest -q -x` 仍停在 `tests/test_agent.py::test_multi_step_autonomous`；失败原因为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突。

### 2026-07-20 补充：Candidate Eval Gate

- 修改 `mewcode/evolution/engine.py`：新增 `evaluate()`，对 skill candidate 执行 deterministic eval，并将 `eval_status`、`eval_checks`、`eval_errors`、`evaluated_at` 写入 manifest。
- 修改 `mewcode/evolution/engine.py`：`promote()` 新增 eval 门禁，只有 `eval_status == "passed"` 才能启用正式 skill。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve eval <proposal_id>`。
- 修改 `tests/test_evolution.py`：新增 eval manifest、promote 未 eval 拒绝、eval 后 promote、命令层 eval/promote 测试。
- 修改 `README.md`、`docs/verified-skill-evolution-recap-zh.md` 和本文档：同步记录 eval gate。
- TDD 红灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 得到 5 个预期失败，覆盖缺少 `evaluate()`、manifest 缺 `eval_status`、promote 未要求 eval 和命令层 eval 未接入。
- 绿灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 通过，21 个测试成功。
- 扩展回归记录：`PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q` 通过，195 个测试成功。
- 格式检查记录：`git diff --check` 无输出。
- 全量测试记录：`PYTHONPATH=. pytest -q -x` 停在 `tests/test_agent.py::test_message_splicing`；失败原因为既有 agent 消息拼接测试期望消息数 5、实际 4，和本次 candidate eval gate 修改无直接依赖。

### 2026-07-20 补充：Candidate Eval Case Gate

- 修改 `mewcode/evolution/engine.py`：新增 `.mewcode/evolution/evals/<skill-name>/cases.jsonl`，用于保存 candidate skill 的任务评估用例。
- 修改 `mewcode/evolution/engine.py`：新增 `add_eval_case()`，写入 `task`、`must_contain`、`must_not_contain` 和 `created_at`。
- 修改 `mewcode/evolution/engine.py`：`evaluate()` 现在要求至少一个 eval case，并将每个 case 的通过/失败明细写入 `eval_case_results`。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve add-eval-case <proposal_id> :: <task> :: <must_contain_csv> [:: <must_not_contain_csv>]`。
- 修改 `tests/test_evolution.py`：新增无 eval case 阻断、case 通过、case 失败和命令层 add-eval-case 到 promote 的完整流程测试。
- 修改 `README.md`、`docs/hermes-skill-evolution-implementation.md`、`docs/verified-skill-evolution-recap-zh.md` 和本文档：同步记录 eval case gate。
- TDD 红灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 得到 5 个预期失败，覆盖无 case 仍通过、缺少 `add_eval_case()`、manifest 缺 `eval_case_results` 和命令层缺 `add-eval-case`。
- 追加安全红灯记录：`PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_add_eval_case_rejects_invalid_skill_name -q` 得到 1 个预期失败，覆盖无效 skill name 仍可写入 eval case 路径。
- 绿灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 通过，24 个测试成功。
- 扩展回归记录：`PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q` 通过，198 个测试成功。
- 格式检查记录：`git diff --check` 无输出。
- 全量测试记录：`PYTHONPATH=. pytest -q -x` 停在 `tests/test_agent.py::test_multi_step_autonomous`；失败原因为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突，和本次 eval case gate 修改无直接依赖。

### 2026-07-21 补充：Skill Execution Eval Report Gate

- 修改 `mewcode/evolution/engine.py`：新增 `run_execution_eval()`，要求 candidate skill 至少有 3 个 eval case，并逐轮生成执行评估结果。
- 修改 `mewcode/evolution/engine.py`：新增 `read_execution_eval_report()`、`execution_eval_report_path()` 和 `execution_eval_markdown_path()`。
- 修改 `mewcode/evolution/engine.py`：写入 `.mewcode/evolution/candidates/<proposal_id>/eval_report.json` 和 `eval_report.md`，并同步 `execution_eval_status`、报告路径、轮次结果和评估时间到 manifest。
- 修改 `mewcode/evolution/engine.py`：`promote()` 新增 execution eval 门禁，只有 `execution_eval_status == "passed"` 才能启用正式 skill。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve run-eval <proposal_id>` 和 `/evolve show-eval <proposal_id>`。
- 修改 `mewcode/commands/handlers/learn.py`：创建提示和 help 指向 `run-eval/show-eval`，要求用户先看报告再 approve/promote。
- 修改 `tests/test_evolution.py`：新增少于三轮阻断、报告写入、sandbox artifacts 落地、新增 eval case 失效旧报告、promote 未 execution eval 拒绝、execution eval 后 promote 成功、命令层报告展示测试。
- 修改 `README.md`、`docs/hermes-skill-evolution-implementation.md`、`docs/verified-skill-evolution-recap-zh.md`、`docs/self-evolution-development-progress-recap-zh.md` 和本文档：同步记录 execution eval gate。
- TDD 红灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 得到 1 个预期失败，覆盖命令层缺少 `run-eval/show-eval` 导致 promote 前门禁不通过。
- 追加 `/learn` 红灯记录：`PYTHONPATH=. pytest tests/test_evolution.py::TestEvolveCommand::test_learn_command_points_to_eval_promote_flow -q` 得到 1 个预期失败，覆盖 `/learn` 未提示 `run-eval/show-eval`。
- 绿灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 通过，30 个测试成功。
- 扩展回归记录：`PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q` 通过，204 个测试成功。
- 格式检查记录：`git diff --check` 无输出。
- 全量测试记录：`PYTHONPATH=. pytest -q -x` 停在 `tests/test_agent.py::test_multi_step_autonomous`；失败原因为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突，和本次 execution eval gate 修改无直接依赖。
- 限制说明：当前 execution eval 是确定性 SOP 覆盖检查，不是真实模型沙盒执行；它用于提交应用前展示多轮测试效果，后续仍应补受限 fork agent 任务回放。

### 2026-07-24 补充：Skill Usage Log 与 Quarantine

- 修改 `mewcode/evolution/engine.py`：新增 `skill_usage_path`、`quarantine_skills_path`、`record_skill_usage()`、`load_skill_usage()` 和 `quarantine_skill()`。
- 修改 `mewcode/tools/load_skill.py`：`LoadSkill` 成功激活 skill 后记录 `event=load`，包括来源标签和注册工具数量。
- 修改 `mewcode/skills/loader.py`：暴露 `work_dir`，供 `LoadSkill` 将 usage 写回当前项目的 `.mewcode/evolution/skill_usage.jsonl`。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `/evolve quarantine <skill-name> [:: reason]`，只隔离项目级正式 skill，隔离后 reload skill loader。
- 修改 `tests/test_evolution.py`：新增 usage JSONL 写入、项目 skill 隔离、命令层 quarantine/reload 测试。
- 修改 `tests/test_skills.py`：新增 `LoadSkill` 成功加载项目 skill 后记录 usage 的测试。
- 修改 `README.md`、`docs/self-evolution-development-progress-recap-zh.md`、`docs/hermes-skill-evolution-implementation.md` 和 `docs/verified-skill-evolution-recap-zh.md`：同步记录 usage/quarantine 当前能力和剩余边界。
- TDD 红灯记录：`PYTHONPATH=. pytest tests/test_evolution.py::TestEvolutionEngine::test_record_skill_usage_writes_jsonl tests/test_evolution.py::TestEvolutionEngine::test_quarantine_project_skill_moves_it_out_of_loader_path tests/test_evolution.py::TestEvolveCommand::test_quarantine_command_moves_skill_and_reloads_loader tests/test_skills.py::TestLoadSkillTool::test_load_existing_project_skill_records_usage -q` 得到 4 个预期失败。
- 绿灯记录：`PYTHONPATH=. pytest tests/test_evolution.py -q` 通过，37 个测试成功；`PYTHONPATH=. pytest tests/test_skills.py -q` 通过，44 个测试成功。
- 扩展回归记录：`PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q` 通过，212 个测试成功。
- 格式检查记录：`git diff --check` 无输出。
- 限制说明：当前 usage log 只覆盖 skill load/quarantine；任务成功、失败、用户纠正和自动 quarantine 建议仍需后续接入。
