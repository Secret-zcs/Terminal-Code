# Hermes Skill 自进化实现留档

本文记录本次将自进化机制从“只写 memory”扩展到“审批后可生成项目级 skill”的实现结果。

## 1. 修改目标

目标是让项目更接近 Hermes 的自进化策略：复杂问题完成后，可以把可复用的解决流程沉淀为 `SKILL.md`，而不是只写一条记忆。

首轮实现的是安全的显式路径：

```text
observe -> propose-skill -> validate -> approve -> apply -> reload
```

2026-07-18 继续补齐 Hermes 的核心学习策略后，路径扩展为：

```text
learn / observe
  -> create skill proposal 或 patch skill proposal
  -> 写入 candidate skill
  -> validate
  -> approve
  -> checkpoint
  -> promote
  -> reload
```

运行时仍不让 `/evolve` 修改代码、工具或系统 prompt。原因是 Hermes 的自进化本质是沉淀外部行为资产，不能让后台学习线程直接改 Agent 核心执行路径。

## 2. 新增能力

### 2.1 Skill 提案

新增 `EvolutionEngine.propose_skill()`。

提案内容仍复用 `EvolutionProposal`，但 `target="skill"`，`change` 字段保存结构化 JSON：

```json
{
  "action": "create",
  "name": "debug-regression-loop",
  "description": "复杂调试任务的回归测试优先流程",
  "mode": "inline",
  "context": "recent",
  "allowedTools": ["Bash", "ReadFile"],
  "body": "# 任务\n\n先复现失败，再写回归测试，最后实现最小修复。"
}
```

这样做的原因是保持现有 proposal 存储格式不变，避免一次性迁移 `.mewcode/evolution/proposals.jsonl`。

### 2.2 Skill 校验

`EvolutionEngine.validate()` 现在按 target 分派：

- `memory`：沿用原有文本校验。
- `skill`：校验 skill 名称、描述、正文、`mode`、`context`、`allowedTools` 和是否已有同名 skill。
- `code`、`prompt`、`tool`：不属于运行时自进化 target，不能通过 `/evolve` 创建或应用。

Skill 名称复用项目已有规则：小写字母开头，只允许小写字母、数字和 `-`。

### 2.3 Skill Patch 提案

2026-07-18 新增 `EvolutionEngine.propose_skill_patch()`，用于 patch 已存在的项目级 skill。

Patch 提案仍然使用 `target="skill"`，但 JSON 中的 `action` 为 `patch`：

```json
{
  "action": "patch",
  "name": "review-loop",
  "description": "Updated review flow",
  "mode": "inline",
  "context": "full",
  "allowedTools": ["Bash", "ReadFile"],
  "body": "# Updated\n\n复盘后优先 patch 已有 skill，再考虑创建新 skill。"
}
```

校验规则：

- `create`：同名目录 skill 或 flat skill 已存在时拒绝，避免覆盖。
- `patch`：同名项目 skill 不存在时拒绝，避免把 patch 当 create 写入。
- `patch` 只允许命中 `.mewcode/skills/<name>/SKILL.md` 或 `.mewcode/skills/<name>.md`，不修改内置 skill 或用户全局 skill。
- 若已有 skill 可解析，patch 默认继承已有 `allowedTools`、`mode`、`context`，除非调用方显式覆盖。

### 2.4 Skill 写入

审批后的 `create` skill proposal 会写入：

```text
.mewcode/skills/<name>/SKILL.md
```

写入格式兼容现有 `SkillLoader` 和 `parse_skill_file()`：

```markdown
---
name: debug-regression-loop
description: 复杂调试任务的回归测试优先流程
allowedTools:
- Bash
- ReadFile
mode: inline
context: recent
---

# 任务

先复现失败，再写回归测试，最后实现最小修复。
```

选择目录型 skill 的原因是后续可以自然扩展 `references/`、`templates/`、`scripts/`，更接近 Hermes 的 skill 资产结构。

审批后的 `patch` skill proposal 会写回已存在的项目 skill 文件。这样做符合 Hermes 的优先级：先维护已有能力资产，再创建新的 class-level skill。

### 2.5 命令入口

`/evolve` 新增：

```text
/evolve propose-skill <name> :: <description> :: <skill body>
/evolve propose-skill-patch <name> :: <description> :: <skill body>
```

另新增 Hermes 风格显式学习入口：

```text
/learn <name> :: <description> :: <skill body>
```

`/learn` 的选择逻辑：

```text
同名项目 skill 存在 -> 创建 patch proposal
同名项目 skill 不存在 -> 创建 create proposal
```

`/learn` 会先写入一条 `source="learn-command"` 的 evidence，再把该 evidence id 关联到生成的 proposal。这样显式学习入口也满足 Hermes 的 evidence-first 要求，而不是直接凭空生成长期行为资产。

完整流程示例：

```text
/evolve observe 复杂调试任务中，先写失败测试能防止回归。
/evolve propose-skill debug-regression-loop :: 复杂调试任务的回归测试优先流程 :: # 任务
先复现失败，再写回归测试，最后实现最小修复。
/evolve approve prop_xxx
/evolve promote prop_xxx
```

学习入口示例：

```text
/learn review-loop :: 复盘复杂任务的流程 :: # 任务
先总结可复用流程；如果同名 skill 已存在，优先 patch。
/evolve approve prop_xxx
/evolve promote prop_xxx
```

skill proposal 创建后只会写入 `.mewcode/evolution/candidates/<proposal_id>/SKILL.md`。`promote` 成功后，如果命令上下文中存在 `skill_loader`，会尝试执行 `reload()`，让新 skill 尽快进入可用目录。

## 3. 安全边界

本次实现保留以下边界：

- memory 必须先 `approve`，才能 `apply`；skill 必须先 `approve`，才能 `promote`。
- 写入前会 `validate`。
- 不覆盖已有 `.mewcode/skills/<name>/` 或 `.mewcode/skills/<name>.md`。
- patch 只能更新已存在的项目级 skill，不能越权更新内置 skill 或用户全局 skill。
- memory apply 前、skill promote 前会尝试创建 checkpoint，便于通过 rewind 回退。
- `code`、`prompt`、`tool` 不进入 `/evolve apply` 路径；相关想法只能作为人工开发建议处理。
- `/learn` 只创建 proposal 和 candidate，不直接启用；用户仍需要显式 approve/promote。
- 当前没有后台自动 apply，不会在用户不知情时写入 skill。

这些边界对应 Hermes 的核心思想：自进化写入的是外部、可审计资产，而不是无约束修改 Agent 自身。

## 4. 与完整 Hermes 的差距

当前实现已经覆盖 Hermes 运行时自进化的安全核心：学习入口、skill create、skill patch、candidate 隔离、验证、审批、promote、checkpoint、reload。与 Hermes 原版的差距主要在后台 fork review 和任务回放 eval 的自动化程度。

| 能力 | 当前项目 | Hermes 更完整方向 |
|---|---|---|
| 触发 | 用户手动 `/learn` 或 `/evolve propose-skill*` | 回合结束 background review |
| 来源 | 用户提供正文或复盘摘要 | 从会话、文件、URL、trace 自动蒸馏 |
| 更新策略 | 同名项目 skill 优先 patch，否则 create | 优先 patch 已加载 skill，再 patch umbrella skill，最后创建新 skill |
| 隔离 | 主命令流创建 candidate，promote 前 checkpoint | fork 隔离 review agent，限制工具白名单 |
| 验证 | 静态格式和冲突校验 | skill verifier + reload + 任务回放评估 |

## 5. 修改清单

- 修改 `mewcode/evolution/engine.py`：新增 skill proposal、memory/skill validation、skill 写入和实际 target path 返回。
- 修改 `mewcode/evolution/engine.py`：2026-07-18 新增 `propose_skill_patch()`、`action=create|patch` 载荷、项目 skill 命中检查和 patch 写回逻辑。
- 新增 `mewcode/commands/handlers/learn.py`：提供 `/learn` 显式学习入口，同名项目 skill 存在时自动创建 patch proposal，并自动记录 learn evidence。
- 修改 `mewcode/commands/handlers/evolve.py`：新增 `propose-skill` / `propose-skill-patch` / `promote` 子命令、skill promote 前 checkpoint、promote 后 loader reload。
- 修改 `mewcode/commands/handlers/__init__.py`：注册 `/learn`。
- 修改 `tests/test_evolution.py`：新增 engine 与 slash command 的 skill 自进化测试，并覆盖损坏 skill proposal 的可读错误返回。
- 修改 `tests/test_evolution.py`：2026-07-18 新增 skill patch、缺失 skill patch 拒绝、`/learn` patch/create 优先级和 evidence 关联测试。
- 修改 `tests/test_commands.py`：2026-07-18 将 `/learn` 纳入命令注册测试。
- 修改 `mewcode/evolution/models.py` 和 `mewcode/evolution/engine.py`：将运行时自进化 target 收紧为 `memory | skill`，拒绝 `code/tool/prompt`。
- 修改 `README.md`：更新 Hermes 自进化说明和命令用法。
- 新增本文档：记录实现策略、边界和验证结果。

## 6. 测试记录

TDD 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
3 failed, 5 passed
```

失败原因符合预期：

- `EvolutionEngine` 缺少 `propose_skill()`。
- `/evolve` 缺少 `propose-skill` 分支。

绿灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
9 passed
```

扩展回归记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
183 passed
```

全量测试记录：

```text
PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

该失败点发生在 Agent 端到端测试中，`WriteFile` 返回 `Error: file has not been read yet. Read it first before editing.`。这与本次 self-evolution/skill 改动无直接依赖，属于既有写文件安全策略与旧测试预期之间的不一致。

### 2026-07-18 收紧验证记录

本次将运行时自进化 target 收紧为 `memory | skill` 后，重新执行：

```text
PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
183 passed
```

同时执行：

```text
PYTHONPATH=. pytest -q -x
FAILED tests/test_agent.py::test_multi_step_autonomous
```

全量首个失败点仍为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突，和本次收紧 `ProposalTarget` 无直接依赖。

### 2026-07-18 `/learn` 与 Skill Patch 验证记录

TDD 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
4 failed, 9 passed
```

失败原因符合预期：

- `EvolutionEngine` 缺少 `propose_skill_patch()`。
- `mewcode.commands.handlers.learn` 尚不存在。

命令注册红灯记录：

```text
PYTHONPATH=. pytest tests/test_commands.py::TestRegisterAllCommands::test_all_commands_registered -q
1 failed
```

失败原因符合预期：`/learn` 尚未注册。

追加 evidence-first 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
1 failed, 13 passed
```

失败原因符合预期：`/learn` 尚未记录 evidence，也没有把 evidence id 关联到 proposal。

绿灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
14 passed
```

```text
PYTHONPATH=. pytest tests/test_commands.py::TestRegisterAllCommands::test_all_commands_registered -q
1 passed
```

扩展回归记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
188 passed
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

全量首个失败点仍为既有 `WriteFile` 写前必须先 `ReadFile` 的安全策略与旧测试预期冲突，和本次 `/learn`、skill patch、自进化策略修改无直接依赖。

### 2026-07-18 Candidate / Promote 验证记录

本次将 skill 启用路径从 direct apply 改为 candidate/promote。

TDD 红灯记录：

```text
PYTHONPATH=. pytest tests/test_evolution.py -q
8 failed, 11 passed
```

失败原因符合预期：

- `EvolutionEngine` 缺少 candidate skill/manifest 路径 API。
- `EvolutionEngine` 缺少 `promote()`。
- approved skill proposal 仍能通过 `apply()` 直接写正式 skill。
- 危险命令 candidate 尚未被静态校验阻断。
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
