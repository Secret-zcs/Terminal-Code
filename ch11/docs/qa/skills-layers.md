# ch11 答疑：技能来源的三层设计

> 日期：2026-06-19

---

## Q1: 为什么技能来源有三层？

mewcode 里反复出现同一个分层模式：配置、权限规则、技能。都是**三层优先级**：项目 > 用户 > 内置/全局。

```
内置技能（mewcode/skills/builtins/） — mewcode 自带的 review/test/commit
用户技能（~/.mewcode/skills/）      — 你的个人偏好，所有项目共享
项目技能（.mewcode/skills/）        — 团队规范，加入项目的人自动用
```

**核心逻辑**：项目级覆盖用户级覆盖内置——团队规范 > 个人习惯 > 默认行为。

**为什么不用更细粒度的合并？** 和配置的 mcp_servers 不同，技能不是"同名合并，新名追加"。因为同名技能通常是**互相替代**的关系（"我的前端设计规范"替代"内置的前端设计规范"），而不是互补关系。

---

## Q2: 内置技能对用户展示吗？

**不对用户展示。** 内置技能打包在 Python 包源码里（`mewcode/skills/builtins/`），通过 `importlib.resources` 加载，不在用户可见的 `.mewcode/skills/` 目录。

Claude Code 也是一样的设计——`/review`、`/commit`、`/init` 等内置命令打包在安装包里，用户看不到源文件。用户装的第三方技能才出现在 `~/.claude/skills/`。

mewcode 照搬了这个设计，确保格式兼容——Claude Code 社区的技能文件可以直接在 mewcode 里用。
