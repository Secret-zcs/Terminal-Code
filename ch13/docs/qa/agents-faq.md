# ch13 答疑：工具过滤、AgentLoader、fork 模式

> 日期：2026-06-23

---

## Q1: 工具过滤流程

五层递进：
1. MCP 工具直接放行（不参与过滤）
2. 全局禁用 7 个（Agent、AskUserQuestion、ExitPlanMode...）
3. 自定义 Agent 额外过滤
4. 后台任务白名单（16 个工具）
5. Agent 定义自己的 tools/disallowed_tools

---

## Q2: AgentLoader 是什么

和 SkillLoader 完全一样的设计：三层加载 `.md` 文件（项目>用户>内置），热更新。内置 Agent 类型：Explore、Plan、general-purpose、claude、claude-code-guide。

---

## Q3: 子 Agent 类型

**类型是人提前写好的**（.md 文件定义边界：tools、maxTurns、system_prompt）。主 Agent 只能从已有类型里选 + 填 description。不能发明新类型（安全约束）。不传类型 → fork 模式（全量复制主对话）。

---

## Q4: Agent fork vs Skill fork

| | Agent fork | Skill fork |
|------|-----------|------------|
| 上下文 | 全量深拷贝（没有选项） | full/recent/none（SKILL.md 定义） |
| prompt | 主 Agent 临时写 | 技能模板 |
| 用途 | 分担主对话工作 | 按手册干活 |

**都不是轻重问题，是有没有预置流程的问题。** 两种都不污染主对话上下文。区别只是子 Agent 带多少前置信息。

---

## Q5: 为什么 fork 出来后不能再 fork

`FORK_BOILERPLATE_TAG` 检测 → 防止递归爆炸。fork 出的 Agent 的 prompt 里明确写了"不能再 Fork"。
