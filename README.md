# MewCode

MewCode 是一个终端内运行的 AI 代码智能体项目，目标是提供类似 Claude Code 的本地开发体验：读取和修改代码、执行命令、管理权限、压缩上下文、加载技能、调用 MCP、创建子智能体团队，并支持 checkpoint/rewind 与 Hermes 风格自进化机制。

项目入口命令为 `mewcode`，对应 Python 包入口：

```text
mewcode.__main__:main
```

---

## 核心能力

- **终端 TUI**：基于 Textual 构建交互式聊天与工具执行界面。
- **多模型适配**：支持 Anthropic、OpenAI 和 OpenAI-compatible provider。
- **工具系统**：内置 `ReadFile`、`WriteFile`、`EditFile`、`Bash`、`Glob`、`Grep` 等代码操作工具。
- **权限控制**：按 read/write/command 分类工具，结合 sandbox、规则引擎和危险命令检测。
- **上下文压缩**：包含大工具结果预算控制、自动 compact、恢复附件和语义压缩计划。
- **Checkpoint / Rewind**：支持 `/checkpoint`、`/rewind --preview`、`/rewind --undo`，可回退代码和对话状态。
- **Hermes 自进化**：通过 `/evolve` 记录经验、生成提案、审批并受控写入项目记忆。
- **Skills**：支持项目级、用户级和内置技能，技能可 inline 或 fork 执行。
- **MCP**：支持 stdio / HTTP MCP server，并包装为可调用工具。
- **子智能体与团队协作**：支持 task、team、mailbox、trace、worktree 等多智能体能力。
- **Hooks**：支持会话、轮次和工具调用相关 hook。

---

## 项目结构

```text
mewcode/
  agent.py                 Agent 主循环、工具执行、compact、checkpoint 触发
  app.py                   Textual TUI 应用
  client.py                Anthropic / OpenAI / OpenAI-compatible 客户端
  config.py                配置加载与 provider 定义
  conversation.py          对话消息结构和 token 估算
  prompts.py               System prompt 和环境上下文构造

  tools/                   内置工具与工具注册表
  permissions/             权限模式、危险命令检测、路径沙箱、规则引擎
  context/                 工具结果预算、auto compact、语义压缩计划
  memory/                  长期记忆、会话恢复、相关记忆选择
  checkpoint/              checkpoint / rewind 编排与持久化
  evolution/               Hermes 风格自进化 evidence / proposal / apply
  skills/                  skill 解析、加载、执行和内置技能
  commands/                Slash command 框架与命令 handlers
  agents/                  子智能体定义、加载、trace、任务管理
  teams/                   团队协作、mailbox、tmux/iTerm/in-process backend
  mcp/                     MCP client、manager、tool wrapper
  hooks/                   hooks 配置、条件、执行器
  worktree/                worktree 创建、清理、会话集成

docs/                      架构设计、审计、压缩、自进化与 rewind 文档
tests/                     单元测试与集成测试
scripts/                   实验脚本
```

---

## 环境要求

- Python `>=3.11`
- 推荐使用虚拟环境
- 至少配置一个 LLM provider

主要依赖见 `pyproject.toml`：

```text
textual
anthropic
openai
pyyaml
pydantic
mcp
httpx[socks]
```

---

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

开发测试依赖：

```bash
pip install pytest pytest-asyncio
```

如果使用 `uv`，也可以根据仓库中的 `uv.lock` 和 `dependency-groups.dev` 管理开发环境。

---

## 配置

MewCode 会按顺序读取配置：

```text
~/.mewcode/config.yaml
<project>/.mewcode/config.yaml
<project>/.mewcode/config.local.yaml
```

最小配置示例：

```yaml
providers:
  - name: claude
    protocol: anthropic
    base_url: https://api.anthropic.com
    model: claude-sonnet-4-20250514
    api_key: ${ANTHROPIC_API_KEY}
    thinking: false

permission_mode: default
```

OpenAI 示例：

```yaml
providers:
  - name: openai
    protocol: openai
    base_url: https://api.openai.com/v1
    model: gpt-4.1
    api_key: ${OPENAI_API_KEY}
```

本地项目配置、会话、checkpoint、evolution 记录等默认写入 `.mewcode/`。该目录已在 `.gitignore` 中忽略。

---

## 运行

交互式启动：

```bash
mewcode
```

非交互式执行单条任务：

```bash
mewcode -p "阅读 README 并总结项目结构"
```

指定权限模式：

```bash
mewcode --mode plan
mewcode --mode default
```

---

## 常用命令

| 命令 | 作用 |
|---|---|
| `/help` | 查看命令列表 |
| `/plan [任务]` | 切换到 Plan 模式 |
| `/do [任务]` | 退出 Plan 模式并恢复执行 |
| `/compact` | 手动压缩上下文 |
| `/checkpoint "label"` | 创建命名 checkpoint |
| `/rewind` | 列出 checkpoint |
| `/rewind N --preview` | 预览回退影响 |
| `/rewind N` | 回退代码和对话 |
| `/rewind N --code` | 仅回退代码 |
| `/rewind N --conv` | 仅回退对话 |
| `/rewind --undo` | 撤销最近一次 rewind |
| `/evolve observe <summary>` | 记录自进化 evidence |
| `/evolve propose <title> :: <change>` | 创建自进化 proposal |
| `/evolve propose-skill <name> :: <description> :: <body>` | 创建新 skill 提案 |
| `/evolve propose-skill-patch <name> :: <description> :: <body>` | 创建既有 skill patch 提案 |
| `/evolve preview <id>` | 预览 memory 追加内容或 skill candidate diff |
| `/evolve approve <id>` | 批准 proposal |
| `/evolve apply <id>` | 应用已批准的 memory proposal |
| `/evolve add-eval-case <id> :: <task> :: <must_contain_csv>` | 为 candidate skill 追加任务评估用例 |
| `/evolve eval <id>` | 评估 candidate skill 是否可启用 |
| `/evolve run-eval <id>` | 对 candidate skill 执行至少三轮任务评估并生成报告 |
| `/evolve show-eval <id>` | 展示 candidate skill 的执行评估报告 |
| `/evolve promote <id>` | 将已批准的 candidate skill 提升为正式 skill |
| `/evolve quarantine <name> [:: reason]` | 将不可靠的项目级正式 skill 移入隔离区 |
| `/learn <name> :: <description> :: <body>` | 将可复用流程蒸馏为 skill 提案；同名项目 skill 存在时优先 patch |
| `/skill list` | 查看 skills |
| `/memory list` | 查看自动记忆 |
| `/status` | 查看当前状态 |

---

## 上下文压缩

当前上下文压缩分为三层：

1. **工具结果预算控制**：超大 `tool_result` 落盘并在上下文中保留预览。
2. **自动摘要压缩**：接近 context window 时摘要早期对话，原样保留近期尾部。
3. **语义压缩计划**：对消息打标签、评分、分类，识别用户约束、TODO、错误、代码事实和工具噪音。

详细说明：

- `docs/context-compression-strategy.md`
- `docs/semantic-context-compression.md`
- `docs/compact-strategy-experiment-results.md`

---

## Checkpoint / Rewind

项目实现了 Claude Code 风格的回退机制：

- `CheckpointManager` 负责创建、预览、回退和撤销。
- `CheckpointStore` 使用 JSONL 持久化 checkpoint 元数据。
- `FileHistory` 负责文件级备份和恢复。
- Agent 会在 turn-end、pre-write、pre-bash、pre-compact 等时机自动创建 checkpoint。

详细说明：

- `docs/rewind-feature-design.md`
- `docs/hermes-evolution-rewind-review.md`

---

## Hermes 自进化

自进化机制位于 `mewcode/evolution/`，采用安全闭环：

```text
memory: observe -> propose -> validate -> approve -> apply
skill:  learn/propose -> candidate -> validate -> eval-case -> eval -> run-eval -> show-eval -> approve -> promote
```

当前支持两类受控落地：

- approved `memory` proposal 自动追加到 `.mewcode/memories.md`。
- skill proposal 先写入 `.mewcode/evolution/candidates/<proposal_id>/SKILL.md`，不会立即进入正式 skill loader。
- candidate skill 必须先记录 eval case，通过 `/evolve eval <proposal_id>`，再通过 `/evolve run-eval <proposal_id>` 至少三轮任务评估，并用 `/evolve show-eval <proposal_id>` 向用户展示报告后，才能经 approve/promote 写入 `.mewcode/skills/<name>/SKILL.md`。
- eval case 写入 `.mewcode/evolution/evals/<skill-name>/cases.jsonl`，用于检查候选 SOP 是否覆盖任务所需关键步骤、且不包含明确禁止的错误策略。
- execution eval 报告写入 `.mewcode/evolution/candidates/<proposal_id>/eval_report.json` 和 `eval_report.md`，并同步记录到 candidate manifest。
- execution eval 每轮都会在 `.mewcode/evolution/candidates/<proposal_id>/execution_sandbox/` 下生成隔离产物，包括 `task.md`、候选 `SKILL.md` 快照、`rendered_prompt.md` 和 `result.json`。
- `/learn` 是 Hermes 风格显式学习入口：同名项目 skill 存在时创建 `patch` 提案，否则创建 `create` 提案，避免重复 skill 膨胀。
- `/learn` 会先记录 learn evidence，再把 evidence id 关联到生成的 proposal。

运行时自进化只接受 `memory` 和 `skill`。`code`、`tool`、`prompt` 不进入 `/evolve apply` 路径；相关想法只能作为人工开发建议处理。

常用命令：

```text
/evolve observe <summary>
/evolve propose <title> :: <memory change>
/evolve propose-skill <name> :: <description> :: <skill body>
/evolve propose-skill-patch <name> :: <description> :: <skill body>
/learn <name> :: <description> :: <skill body>
/evolve preview <proposal_id>    # memory append or skill diff preview
/evolve add-eval-case <proposal_id> :: <task> :: <must_contain_csv> [:: <must_not_contain_csv>]
/evolve eval <proposal_id>       # parse + eval case gate
/evolve run-eval <proposal_id>   # at least 3 execution eval rounds + report
/evolve show-eval <proposal_id>  # user-visible eval report
/evolve approve <proposal_id>
/evolve apply <proposal_id>      # memory only
/evolve promote <proposal_id>    # skill candidate only
/evolve quarantine <skill-name> [:: reason]
```

`/evolve apply` 在写入 memory 前会尝试创建 checkpoint；`/evolve promote` 在启用 skill candidate 前会尝试创建 checkpoint，并在成功后 reload skill loader。`LoadSkill` 成功激活 skill 会记录到 `.mewcode/evolution/skill_usage.jsonl`；`/evolve quarantine` 会把项目级正式 skill 移入 `.mewcode/evolution/quarantine/<skill-name>/`，并 reload skill loader。

详细说明：

- `docs/hermes-evolution-rewind-review.md`
- `docs/hermes-skill-evolution-implementation.md`
- `docs/verified-skill-evolution-recap-zh.md`
- `docs/skill-execution-eval-gate-recap-zh.md`

---

## 测试

运行全部测试：

```bash
PYTHONPATH=. pytest -q
```

运行重点测试：

```bash
PYTHONPATH=. pytest tests/test_context.py -q
PYTHONPATH=. pytest tests/test_checkpoint.py -q
PYTHONPATH=. pytest tests/test_evolution.py -q
PYTHONPATH=. pytest tests/test_commands.py -q
```

最近一次相关验证：

```text
PYTHONPATH=. pytest tests/test_evolution.py tests/test_skills.py tests/test_commands.py tests/test_checkpoint.py tests/test_context.py -q
212 passed
```

---

## 重要文档

| 文档 | 内容 |
|---|---|
| `docs/audit-claude-code-standards.md` | 按 Claude Code 标准的架构审计 |
| `docs/codebase-walkthrough.md` | 逐模块代码讲解 |
| `docs/context-compression-strategy.md` | 上下文压缩策略详解 |
| `docs/compact-strategy-experiment-results.md` | 压缩策略前后实验结果 |
| `docs/rewind-feature-design.md` | Rewind 设计文档 |
| `docs/hermes-evolution-rewind-review.md` | Hermes 自进化与 Rewind 复盘 |
| `docs/self-evolution-development-progress-recap-zh.md` | 当前自进化开发进度总复盘 |
| `docs/skill-execution-eval-gate-recap-zh.md` | 候选 skill 多轮执行评估门禁复盘 |
| `docs/agent-interview-qa.md` | Agent 项目问答材料 |

---

## 安全说明

- 不要提交 `.mewcode/`、`config.yaml`、API key、会话记录和本地权限规则。
- 写文件和危险命令前建议使用 `/checkpoint`。
- 自进化提案默认只写 memory，高风险目标必须先形成 proposal 并人工审核。
- 运行 Bash 工具前会经过权限检查和危险命令检测。

---

## License

当前仓库未声明许可证。公开发布前请补充明确的 LICENSE 文件。
