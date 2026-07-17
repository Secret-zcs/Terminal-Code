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
observe -> propose -> validate -> approve -> apply
```

并用 Claude Code 风格 rewind/checkpoint 机制作为安全保护：

```text
自进化应用前
  -> track 目标文件
  -> 创建 checkpoint
  -> apply 变更
  -> 如果结果不好，可用 /rewind 回退
```

当前可自动应用的目标只有 `memory`，也就是项目记忆 `.mewcode/memories.md`。`code`、`prompt`、`tool`、`skill` 这类高风险目标目前只允许形成提案，不自动应用。

这个边界是有意设计的：自进化系统如果一开始就允许改工具实现、系统提示词或代码，很容易形成不可审计、不可回退、难以测试的隐性行为变化。

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
最后为未来 code/prompt/skill/tool 自进化留下扩展点
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
| `skill` | 只允许形成提案 | 中 |
| `prompt` | 只允许形成提案 | 中 |
| `tool` | 只允许形成提案 | 高 |
| `code` | 只允许形成提案 | 高 |

第一阶段只应用 `memory`，原因是：

- `.mewcode/memories.md` 本来就是项目长期记忆入口。
- 写入记忆不会改变代码执行路径。
- 即使写错，也能通过 rewind 或手动编辑恢复。
- 记忆型自进化已经能让 Agent 在后续任务中记住经验。

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
- 高风险目标必须停留在 proposal 阶段，不能直接写入。
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
- 只有 `target == "memory"` 可以自动应用。
- `change` 不能为空。
- 非低风险提案会产生 warning。
- 缺失 evidence 会产生 warning。

为什么 `code/tool/prompt/skill` 暂时不能自动 apply：

- 这些目标会改变 Agent 行为或代码执行路径。
- 需要专门的 patch 生成、测试运行、diff 预览、人工确认和 rollback 机制。
- 当前只把它们作为可审计提案保留。

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

当前仅支持 memory：

```text
approved memory proposal
  -> append 到 .mewcode/memories.md 的 ### 项目知识
  -> proposal.status = applied
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

`/evolve apply` 会在真正写 `.mewcode/memories.md` 之前尝试：

```text
1. target_path = .mewcode/memories.md
2. file_history.track_edit(target_path)
3. checkpoint_manager.create_checkpoint(
       label="Hermes evolution: <title>",
       trigger="manual",
   )
4. engine.apply(proposal_id)
```

这样自进化应用后，如果用户发现记忆写错，可以：

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

当前做法先把高风险目标限制为提案：

```text
code/tool/prompt/skill -> proposed only
memory -> approved 后可 apply
```

这是更适合当前项目成熟度的选择。

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

### 9.3 缺少 diff preview

`/evolve show` 能展示 change，但 `/evolve apply` 前没有专门 diff。

目前依赖：

```text
/rewind --preview
```

但更好的交互应该是：

```text
/evolve preview <proposal_id>
```

展示即将写入的 memory diff。

### 9.4 非 memory 自进化还没有落地通道

当前 `skill`、`prompt`、`tool`、`code` 都只能提案。

未来如果要支持，需要分别设计：

| target | 需要的保护 |
|---|---|
| `skill` | frontmatter 校验、allowedTools 校验、skill reload 测试 |
| `prompt` | prompt diff、行为回归测试、版本号 |
| `tool` | schema 校验、权限校验、工具单测 |
| `code` | patch preview、pytest、lint、checkpoint、人工确认 |

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

### 10.3 增加 `/evolve preview`

建议新增：

```text
/evolve preview <proposal_id>
```

输出：

```diff
### 项目知识
+ - 自进化应用前必须创建 rewind checkpoint。
```

价值：

- apply 前可视化影响。
- 与 `/rewind --preview` 形成对称体验。

### 10.4 引入 proposal verifier

后续可增加专门 verifier：

```text
memory verifier
skill verifier
prompt verifier
tool verifier
code verifier
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

当前实现是 Hermes 风格的最小安全闭环，但还不是完整 Hermes。

差距：

| 维度 | 当前项目 | 完整自进化方向 |
|---|---|---|
| evidence 来源 | 手动 observe | 自动从 trace、tests、rewind、feedback 提取 |
| mutation | 手动 propose | 自动生成候选改进 |
| selection | 人工 approve | 指标驱动排序 + 人工确认 |
| validation | 简单规则 | 针对 target 的 verifier 和回归测试 |
| application | 只写 memory | 支持 skill/prompt/tool/code 的受控更新 |
| evaluation | 单元测试 | 长期任务 benchmark 和回放评估 |

下一步不应直接跳到自动改代码，而应先补：

```text
自动 evidence 收集
  -> proposal 质量评分
  -> preview
  -> target-specific verifier
```

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
- 设计边界：第一版只允许 approved memory proposal 自动应用；code、tool、prompt、skill 暂时保持 proposal-only。
