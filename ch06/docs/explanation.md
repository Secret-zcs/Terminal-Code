# ch06：Agent 主循环 + 上下文管理 — `agent.py` + `context/manager.py`

> 文件：`mewcode/agent.py` (1256行)、`mewcode/context/manager.py` (854行)
> 依赖：ch01~ch05 全部
> 被依赖：app.py

---

## 第一部分：Agent 事件体系

### 1.1 AgentEvent — 12 种事件

Agent 通过 `async generator` 不断 yield 事件给上层 TUI：

```python
AgentEvent = (
    StreamText | ThinkingText | RetryEvent | ToolUseEvent |
    ToolResultEvent | TurnComplete | LoopComplete | UsageEvent |
    ErrorEvent | PermissionRequest | CompactNotification | HookEvent
)
```

**设计模式**：观察者模式。Agent 是发布者（publisher），TUI 是消费者。Agent 不调用 TUI 的任何方法——只 yield 事件，TUI 自己决定怎么渲染。这保证了 Agent 可以被不同 UI（终端/GUI/非交互模式）复用。

### 1.2 StreamCollector — 翻译层

```python
class StreamCollector:
    async def consume(self, stream: AsyncIterator[StreamEvent]) -> AsyncIterator[AgentEvent]:
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text      # 拼接文本
                yield StreamText(text=event.text)     # 转发 TUI
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield ToolUseEvent(...)
            elif isinstance(event, StreamEnd):
                self.response.stop_reason = event.stop_reason
                ...
```

**一边累积一边转发**：`self.response.text += event.text` 累积到 LLMResponse，`yield StreamText(...)` 同时转发给 TUI 实时渲染。

---

## 第二部分：Agent 主循环

### 2.1 每轮迭代的完整流程

```python
while True:
    ① Layer2: auto_compact() → 超阈值？压缩对话
    ② 环境注入：system prompts, hooks, deferred tools 提示
    ③ Layer1: apply_tool_result_budget() → 生成 api_conv（副本）
    ④ collector.consume(client.stream(api_conv)) → 调 LLM
    ⑤ max_tokens 截断？→ 断点续传（提升上限+让LLM继续，最多4次）
    ⑥ 没工具调用？→ 退出循环
    ⑦ 有工具调用？→ 工具分区 → 权限检查 → 钩子 → 执行 → 回到①
```

### 2.2 先 Layer2 后 Layer1 的原因

Layer2 会修改 `conversation.history`（替换为摘要+尾部），Layer1 依赖它的最新状态生成 api_conv 副本。先压缩再预算，避免 Layer1 白干。

### 2.3 max_tokens 断点续传

```python
if response.stop_reason == "max_tokens":
    if not max_tokens_escalated:
        self.client.set_max_output_tokens(64000)   # 第一次：提上限
        max_tokens_escalated = True
    elif output_recoveries < MAX_OUTPUT_TOKENS_RECOVERIES:  # 最多 3 次
        output_recoveries += 1
    conversation.add_assistant_message(response.text)
    conversation.add_user_message("从你停下的地方继续，不要重复")
    continue   # 回到循环
```

### 2.4 工具分区执行

```python
def partition_tool_calls(tool_calls, registry) -> list[ToolBatch]:
    batches = []
    for tc in tool_calls:
        safe = tool.is_concurrency_safe and tool.is_enabled
        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)   # 追加到当前并发批
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))  # 新开一批
    return batches
```

安全工具（ReadFile,Glob,Grep）可并发，有副作用工具（EditFile,WriteFile）串行。`asyncio.gather` 执行并发批。

### 2.5 _execute_tool() — 单个工具执行路径

```python
async def _execute_tool(self, tc):
    ① 工具是否存在？是否被禁用？
    ② Hook: pre_tool_use → reject? → 返回错误
    ③ 权限检查: deny? → 返回错误 | ask? → yield PermissionRequest → 等用户
    ④ 用户选 allow_always → 自动追加规则到 permissions.local.yaml
    ⑤ Pydantic: tool.params_model.model_validate(tc.arguments) → execute(params)
    ⑥ Recovery: _snapshot_for_recovery() 记录 ReadFile 的内容
```

### 2.6 恢复快照

```python
def _snapshot_for_recovery(self, tc, result):
    if result.is_error or tc.tool_name != "ReadFile":
        return
    self.recovery_state.record_file_read(path, content)
```

每次 ReadFile 成功后记录内容到 RecoveryState。Layer2 压缩后将最近 5 个文件内容作为"恢复附件"重新注入对话，防止压缩后模型遗忘关键文件。

---

## 第三部分：上下文管理（context/manager.py）

### 3.1 Layer1：工具结果预算（每轮运行）

```python
def apply_tool_result_budget(conversation, session_dir, state):
    # Design B: 不修改原始 conversation，生成副本 api_conv

    # Pass 1: 单条 > 50K 字符 → 落盘 + <persisted-output> 预览(2K)
    # Pass 2: 全部合计 > 200K 字符 → 从最大的开始逐个落盘
    # Pass 3: 超过 10 轮的旧结果 > 2K 字符 → <snipped> 预览

    return new_conv, new_records  # 返回副本，原始 conversation 不变
```

**为什么 Design B（不修改原始对话）？** Layer2 需要完整的原始对话来生成摘要。如果 Layer1 把原始对话改了，Layer2 就只能拿到被裁过的版本。

**状态追踪**：`ContentReplacementState` 记住每个 tool_use_id 的决策，跨迭代保持一致性。决策持久化到 replacement_records.jsonl，会话恢复时重建。

### 3.2 Layer2：LLM 摘要压缩（触发式运行）

**触发条件**：`current_tokens() > 167,000`（200K - 20K 摘要预留 - 13K 安全边距）

```python
async def auto_compact(conversation, client, context_window, ...):
    # ① 计算保留窗口：尾部 10K token + 最少 5 条 + 上限 40K/条
    keep_start = _compute_keep_start_index(conversation.history)
    # ② 对齐：不拆散 tool_use/tool_result 对
    keep_start = _align_keep_start_to_tool_pair(messages, keep_start)
    # ③ 前缀 < 2K token → 跳过（不值得压缩）
    # ④ 调 LLM 生成 9 段结构化摘要
    summary = extract_summary(llm_output)
    # ⑤ 重建对话：[摘要+恢复附件] + [保留的尾部原文]
    new_messages = build_compact_messages(summary, attachment=recovery_attachment(...))
    # ⑥ 替换 history + 清除锚点 + 清除旧工具结果文件
    conversation.replace_history(new_messages + list(keep_tail))
```

**熔断器**：连续 3 次压缩失败 → `CompactCircuitBreaker.is_open()` → 之后不再自动压缩，防止反复浪费 Token。

**恢复附件**：`build_recovery_attachment()` 附加最近 5 个文件（≤5K token/个）、激活技能（≤25K token 总计）、可用工具列表。

### 3.3 压缩常数速查

| 常量 | 值 | 来源 |
|------|-----|------|
| 触发阈值（自动） | 167,000 | `200K - 20K - 13K` |
| 触发阈值（手动） | 177,000 | `200K - 20K - 3K` |
| 保留窗口 Token | 10,000 | Claude Code compact.ts |
| 最少保留消息 | 5 | Claude Code compact.ts |
| 单条上限 | 40,000 | 防单条巨消息拖走整段历史 |
| Layer1 单条上限 | 50,000 字符 | |
| Layer1 合计上限 | 200,000 字符 | Claude Code toolLimits.ts |
| 摘要最小前缀 | 2,000 token | 不值得压缩的下限 |
| 熔断器阈值 | 3 次 | |
