# ch10：钩子系统 — `hooks/`

> 文件：`models.py`, `events.py`, `conditions.py`, `loader.py`, `executors.py`, `engine.py`

---

## 一、16 个生命周期事件

```python
class LifecycleEvent(StrEnum):
    SESSION_START, SESSION_END      # 会话级
    TURN_START, TURN_END            # 轮次级
    PRE_TOOL_USE, POST_TOOL_USE     # 工具级（★PRE_TOOL_USE可拒绝）
    PRE_SEND, POST_RECEIVE          # 消息级
    STARTUP, SHUTDOWN, ERROR, ...   # 系统级
```

---

## 二、条件表达式

```python
# 操作符：==（精确）, !=（不等）, =~（正则）, ~=（通配符）
# 逻辑：&&（and）, ||（or），不能混用

# 示例
parse_condition("tool == Bash && file_path ~= *.py")
# → ConditionGroup(
#     conditions=[Condition(field="tool", operator="==", value="Bash"),
#                 Condition(field="file_path", operator="~=", value="*.py")],
#     logic="and"
#   )
```

---

## 三、四种 Action

```python
_EXECUTOR_MAP = {
    "command": execute_command,   # asyncio.create_subprocess_shell + 超时kill
    "prompt":  execute_prompt,    # 文本注入 system prompt
    "http":    execute_http,      # loop.run_in_executor 线程池（不阻塞事件循环）
    "agent":   execute_agent,     # 占位，未实现
}
```

---

## 四、HookEngine

```python
class HookEngine:
    async def run_hooks(self, event, ctx):
        for hook in matched:
            hook.mark_executed()
            if hook.async_exec:
                asyncio.ensure_future(...)  # 不等
            else:
                await self._run_single(...) # 等结果

    async def run_pre_tool_hooks(self, ctx) -> ToolRejectedError | None:
        # 特殊：支持拒绝工具调用
        for hook in matched:
            if hook.reject:
                return ToolRejectedError(...)
        return None
```

**约束（loader 加载时检查）**：`reject` 只能用 `pre_tool_use`，`async` 不能用 `pre_tool_use`。
