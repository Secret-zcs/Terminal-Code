# MewCode 项目答疑索引

> 共 7 个 Q&A 文档，覆盖 6 个模块
> 更新日期：2026-07-17

## 答疑分布

| 模块 | 文档 | 问题数 | 核心话题 |
|------|------|--------|---------|
| ch06 Agent+上下文 | ch06/docs/qa/layer1-layer2-faq.md | 3 | Layer1/Layer2 顺序、167K阈值、tool pair 对齐 |
| ch07 权限系统 | ch07/docs/qa/permissions-faq.md | 3 | 安全检测、六模式对比、五层流程 |
| ch09 记忆系统 | ch09/docs/qa/memory-qa.md | 2 | 记忆提取注入流程、LLM选择安全性 |
| ch11 技能系统 | ch11/docs/qa/skills-layers.md | 2 | 三层来源、内置技能展示 |
| ch12 MCP集成 | ch12/docs/qa/mcp-faq.md | 5 | 流程、stdio/HTTP区别、参数、懒加载、执行路径 |
| ch13 子Agent | ch13/docs/qa/agents-faq.md | 5 | 工具过滤、AgentLoader、类型选择、fork对比 |
| ch14 多Agent团队 | ch14/docs/qa/teams-faq.md | 3 | 通信机制、协作模式、兜底情况 |

## 面试文档

| 文档 | 路径 | 用途 |
|------|------|------|
| 基础面试问答 | interview-qa.md | 12道 STAR 法则问答，架构+设计+挑战 |
| 深度拷打 | interview-hard-qa.md | 11道高难度追问，兜底+容错+边界 |
| 项目架构复审 | docs/audit-claude-code-standards.md | Claude Code 标准对比审查 |
| 代码走查 | docs/codebase-walkthrough.md | 逐模块代码导读 |

## 文档规则

- 每章 Q&A 超过 5 个文件时合并为 `faq.md`
- 完成模块学习后更新对应 `explanation.md`
- 命名：`qa-{主题}.md` 或 `{模块}-faq.md`
