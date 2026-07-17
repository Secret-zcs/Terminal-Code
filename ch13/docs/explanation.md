# ch13：子 Agent 系统 — `agents/`

> 文件：`loader.py`, `task_manager.py`, `fork.py`, `tool_filter.py`

---

## 一、AgentLoader — 三层加载

和 SkillLoader 相同模式：项目 (`.mewcode/agents/`) > 用户 (`~/.mewcode/agents/`) > 内置 (`mewcode/agents/builtins/`)。热更新。

内置类型：Explore、Plan、general-purpose、claude、claude-code-guide。

---

## 二、TaskManager — 后台任务调度

```python
def launch(self, agent, task, fork_conversation=None):
    task_id = uuid.uuid4().hex[:8]
    async_task = asyncio.create_task(self._run_background(task_id, fork_conversation))
    return task_id   # 立刻返回，主Agent不阻塞

async def _run_background(self, task_id, fork_conversation):
    result = await bg.agent.run_to_completion(task, fork_conversation)
    bg.status = "completed"
    await self._notify_queue.put(task_id)   # 完成通知
```

app.py 每 2 秒轮询 `poll_completed()`，结果注入主对话。

---

## 三、fork — 对话分身

```python
def build_forked_messages(conversation, task):
    fork_conv.history = copy.deepcopy(conversation.history)  # 全量深拷贝
    # 处理悬空 tool_use → 填 "interrupted" 占位
    # 注入 fork 规则：不能再 fork、不要对话、直接干活
    fork_conv.add_user_message(f"{FORK_BOILERPLATE}\n你的任务：{task}")
```

`FORK_BOILERPLATE_TAG` 防嵌套：fork 出的 Agent 不能继续 fork。

---

## 四、工具过滤 — 五层递进

```
Layer 0: MCP 工具直接放行
Layer 1: 全局禁用（Agent/AskUserQuestion/ExitPlanMode 等 7 个）
Layer 2: 自定义 Agent 额外禁用
Layer 3: 后台任务白名单（16 个工具）
Layer 4: Agent 定义自身的 tools/disallowed_tools
```

**核心规则**：子 Agent 不能调 `Agent` 工具（不能派孙 Agent），后台子 Agent 不能调 `AskUserQuestion`（没人可问）。

---

## 五、Skill fork vs Agent fork

| | Skill fork | Agent fork |
|------|-----------|------------|
| prompt | 技能模板 | 主Agent临时描述 |
| 上下文 | full/recent/none | 全量复制 |
| 工具 | allowed_tools 白名单 | 五层过滤 |
| 用途 | 重复性任务 | 一次性任务 |
