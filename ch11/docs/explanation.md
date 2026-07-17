# ch11：技能系统 — `skills/`

> 文件：`parser.py`, `loader.py`, `executor.py`, `directory.py`

---

## 一、SkillDef — 技能数据结构

```python
@dataclass
class SkillDef:
    name: str               # 唯一标识 "frontend-design"
    description: str        # 给用户看的一句话描述
    prompt_body: str        # 核心：注入 LLM 的 prompt
    allowed_tools: list[str]  # 限制技能可用工具
    mode: "inline" | "fork"   # inline=注入主对话, fork=子Agent独立执行
    model: str | None        # 可选指定模型
    context: "full" | "recent" | "none"  # fork 模式带的上下文量
```

---

## 二、三层加载

项目 (`.mewcode/skills/`) > 用户 (`~/.mewcode/skills/`) > 内置 (`mewcode/skills/builtins/`)。同名不覆盖。热更新：每次 `get()` 重新读文件。

---

## 三、两种执行模式

**inline**：`agent.activate_skill(name, prompt)` → prompt 拼入 system prompt。

**fork**：`execute_fork()` 创建子 Agent → 工具集过滤为 `allowed_tools` → 带上下文 (full/recent/none) → `fork_agent.run()` → 结果返回主对话。

---

## 四、自定义工具

`tool.json` 定义 + `references/*.py`（导出 `execute` 函数）+ `importlib` 动态加载 → `SkillCustomTool`。
