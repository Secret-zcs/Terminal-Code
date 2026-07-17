# MewCode 项目深度面试拷打 — 兜底、容错、边界场景

> 角色：大厂后端/Agent 开发面试官
> 考察重点：系统的健壮性、错误恢复、边界处理、设计权衡

---

## Q1: 任务执行到一半失败了怎么办？比如 Agent 在改 3 个文件，改完第 2 个时工具调用失败，系统怎么处理？

**面试官追问**：第 1 个文件的改动已经写盘了，第 2 个失败，第 3 个还没开始。这时候对话上下文里有什么？用户看到什么？

**S（背景）**：
Agent 的核心执行模型是"LLM 决策 → 工具执行 → 结果反馈 → LLM 再决策"的循环。每轮可能有多个工具调用。工具执行失败是常态——文件不存在、命令超时、权限拒绝都会发生。

**T（任务）**：
需要保证单次工具失败不导致整个对话崩溃，但也不静默吞错让 LLM 在错误前提下继续。

**A（行动）**：

1. **工具失败的统一表示**：所有工具返回 `ToolResult(is_error=True)`，不抛异常。Agent 层的 `_execute_tool()` 将所有异常（Pydantic 校验失败、execute 抛异常）统一包装为 `ToolResult(output="Error: ...", is_error=True)`。

```python
# agent.py
try:
    params = tool.params_model.model_validate(tc.arguments)
    result = await tool.execute(params)
except ValidationError as e:
    result = ToolResult(output=f"Parameter validation error: {e}", is_error=True)
except Exception as e:
    result = ToolResult(output=f"Tool execution error: {e}", is_error=True)
```

2. **单次失败不中断循环**：失败的 ToolResult 和其他成功的结果一起被追加到 `conversation.add_tool_results_message()`。LLM 在下一轮能看到"第 1 个成功了，第 2 个失败了，第 3 个还没执行"。LLM 可以决定重试第 2 个、跳过、或换方案。

3. **连续未知工具的熔断**：
```python
# agent.py
if consecutive_unknown >= 3:
    yield ErrorEvent("Agent terminated: too many consecutive unknown tool calls")
    break
```
连续三次调用不存在的工具 → 判定为 LLM 产生幻觉，终止循环。

4. **文件级写保护**：FileStateCache 要求"写前先读"。Agent 忘了读文件就写 → WriteFile 拒绝执行，返回描述性错误。这防止了"没读代码就改代码"的灾难。

5. **用户感知**：失败的 ToolResult 在 TUI 显示为 ✗，成功为 ✓。用户可以中断 Agent（Ctrl+C），查看已完成的改动，决定是继续还是回滚。

**R（结果）**：
单次工具失败不会导致任务崩溃。Agent 能根据错误信息自我纠正（比如"文件不存在"→ 先 Glob 找到正确路径再读）。连续异常有熔断保护。用户可见失败状态，随时可干预。

---

## Q2: 上下文压缩（Layer2 auto_compact）本身失败了怎么处理？

**面试官追问**：压缩是要调 LLM 生成摘要的。如果这次 LLM 调用也失败了呢？如果连续失败呢？系统会不会陷入"越压越失败，越失败越压"的死循环？

**S（背景）**：
Layer2 压缩依赖一次额外的 LLM 调用来生成对话摘要。但这个调用本身可能因为 prompt too long、网络错误、Rate Limit 等原因失败。如果失败后没有保护机制，Agent 每轮都会尝试压缩→失败→再尝试→再失败，无效消耗 Token。

**T（任务）**：
需要一个熔断机制防止连续失败的无限重试，同时在压缩调用本身遇到 prompt too long 时有渐进式降级策略。

**A（行动）**：

1. **熔断器（Circuit Breaker）**：
```python
@dataclass
class CompactCircuitBreaker:
    max_failures: int = 3
    consecutive_failures: int = 0

    def record_failure(self): self.consecutive_failures += 1
    def record_success(self): self.consecutive_failures = 0
    def is_open(self) -> bool: return self.consecutive_failures >= self.max_failures
```

连续 3 次失败 → `is_open()` 返回 True → Agent 主循环跳过压缩步骤：
```python
if not manual and breaker is not None and breaker.is_open():
    return "自动压缩已熔断（连续失败 3 次），请手动处理或使用 /compact"
```

**只有手动 `/compact` 命令才能重置熔断器**（因为 manual 模式下不检查 breaker）。

2. **渐进式重试（针对 prompt too long）**：
```python
for attempt in range(max_retries):    # 最多 3 次
    try:
        async for event in client.stream(summary_conv, ...):
            collected_text += event.text
        break
    except Exception as e:
        if "prompt" in err_msg and "long" in err_msg or "too many" in err_msg:
            # 按轮次分组，丢弃最旧的 20% 轮次
            groups = _group_messages_by_turn(summary_conv.history[1:-1])
            drop_count = max(1, len(groups) // 5)
            remaining = groups[drop_count:]
            summary_conv.history = [header] + flattened(remaining) + [footer]
            continue     # 重试
        breaker.record_failure()
        return f"摘要生成失败: {e}"
```

3. **早期退出**：前缀 < 2,000 token 时跳过压缩——不值得为这一点空间冒 LLM 调用失败的风险。

**R（结果）**：
熔断器防止了"越压越失败"的死循环。渐进式重试处理了 prompt too long 场景。一旦熔断，Agent 继续运行但不再压缩——对话可能最终因窗口溢出而报错，但至少不是静默的 Token 消耗。

---

## Q3: LLM 输出被截断（max_tokens）怎么恢复？

**面试官追问**：LLM 可能在输出工具调用的 JSON 中间被截断——比如 `{"file_path": "config.py", "off` 就停了。这个不完整的 JSON 怎么处理？后续怎么让 LLM 继续？

**S（背景）**：
LLM API 有 `max_output_tokens` 限制。当输出达到上限时，API 返回 `stop_reason: "max_tokens"`。对于长回复（大段代码生成、大量文件操作），截断是常见场景。不完整的工具调用 JSON 会引发 `json.JSONDecodeError`。

**T（任务）**：
需要在截断发生时安全降级（不完整的 JSON 不崩溃），并设计断点续传机制让 LLM 从截断处继续。

**A（行动）**：

1. **JSON 解析安全降级**（client.py）：
```python
elif event.type == "content_block_stop":
    if current_tool_name:
        try:
            args = json.loads(json_accum) if json_accum else {}
        except json.JSONDecodeError:
            args = {}           # ← 不完整 JSON → 空 dict，不崩溃
        yield ToolCallComplete(...)
```
截断发生时，最后一个 ToolCallComplete 的 `arguments` 是空 `{}`。LLM 下一轮能看到"这个工具调用参数不完整"的错误信息。

2. **断点续传机制**（agent.py）：
```python
if response.stop_reason == "max_tokens":
    if not max_tokens_escalated:
        # 第一次截断：提升上限到 64,000
        self.client.set_max_output_tokens(MAX_TOKENS_CEILING)
        max_tokens_escalated = True
    elif output_recoveries < MAX_OUTPUT_TOKENS_RECOVERIES:
        # 后续（最多 3 次）：不提升上限，只让 LLM 拆分继续
        output_recoveries += 1

    # 把已输出的内容存进对话，告诉 LLM 继续
    conversation.add_assistant_message(response.text, thinking_blocks=...)
    conversation.add_user_message(
        "Output token limit hit. Resume directly from where you stopped. "
        "Do not apologize or repeat previous content. Pick up mid-thought if needed."
    )
    continue    # ← 回到 while True，不执行工具
```

3. **关键设计**：截断时要保留 `response.text`（已输出的文本）和 `thinking_blocks`。下一轮 LLM 能看到"我上次说了这些，被截断了，继续"。

**R（结果）**：
截断最多恢复 4 次（1 次提上限 + 3 次续传）。不完整的 JSON 静默降级为空 dict 而非崩溃。超出恢复次数后正常退出循环，返回已有内容。

---

## Q4: MCP 服务器在执行中途崩溃了怎么办？

**面试官追问**：MCP 服务器是独立进程（stdio）或远程服务（HTTP）。它崩溃时 Agent 正在等工具结果。怎么检测崩溃？怎么恢复？对用户可见吗？

**S（背景）**：
MCP 服务器作为独立进程运行。网络故障、进程 OOM、npm 包更新都可能导致 Server 终止。Agent 不能因为外部工具坏了就整体崩溃。

**T（任务）**：
实现断线检测 + 自动重连 + 优雅降级。用户不应感知到重连的细节。

**A（行动）**：

1. **断线检测和执行层重连**（MCPToolWrapper.execute）：
```python
async def execute(self, params):
    if not self._client.is_alive:       # ① 检测断线
        try:
            await self._client.connect()  # ② 尝试重连
        except Exception as e:
            return ToolResult(output=f"MCP server reconnect failed: {e}", is_error=True)

    try:
        result = await self._client.call_tool(...)
    except Exception as e:
        self._client._alive = False      # ③ 标记断线，下次自动重连
        return ToolResult(output=f"MCP tool call failed: {e}", is_error=True)
```

2. **优雅降级**：工具调用失败返回 `ToolResult(is_error=True)`，和其他工具失败的流程完全一样。Agent 看到错误信息后可以决定重试、跳过、或告诉用户。

3. **启动时的容错**（MCPManager.register_all_tools）：
```python
for name, config in self._configs.items():
    try:
        client = MCPClient(config)
        await client.connect()
        tools = await client.list_tools()
        # 注册工具...
    except Exception as e:
        errors.append(f"MCP server '{name}': {e}")  # 不崩溃，继续注册其他的
```

**R（结果）**：
单个 MCP Server 崩溃不影响其他 Server 和本地工具。崩溃对用户透明（最多看到一次工具执行失败的 ✗）。恢复的 Server 下次调用时自动重连。

---

## Q5: 权限系统的五层安检门有没有被绕过的可能？

**面试官追问**：BYPASS 模式下 `rm -rf /` 真的拦得住吗？用户自定义规则能覆盖黑名单吗？路径沙箱能不能被符号链接绕过？

**S（背景）**：
权限系统需要保证：即使在最宽松的模式下，明确危险的命令也必须被拦截。同时不能因为检查顺序的 bug 让"用户规则"绕过"黑名单"。

**T（任务）**：
设计检查顺序，确保硬拦截（黑名单、路径沙箱）在任何模式下都生效，检查顺序不可被用户配置改变。

**A（行动）**：

1. **黑名单排在模式矩阵前面**（checker.py 检查顺序）：
```
Layer 1b: 危险命令黑名单（←硬拦截）→ deny
Layer 4:  权限模式矩阵（←可变策略）→ allow/deny/ask
```

即使 BYPASS 模式矩阵返回 "allow"，`rm -rf /` 在 Layer 1b 就已经被拦截了。**检查顺序不可被用户配置改变**——这是硬编码的。

2. **路径沙箱的防逃逸**：
```python
def check(self, path):
    p = Path(path).expanduser()
    real_path = p.resolve(strict=False)    # ★ resolve 展开所有符号链接
    for root in self._allowed_roots:
        real_path.relative_to(root)        # 用 relative_to 而非 startswith
```

`resolve()` 将 `~/.ssh/../../../etc/passwd` 展成 `/etc/passwd`。`relative_to()` 抛异常 → 拦截。`startswith` 是字符串匹配，会被 `/etc/passwd` 以 `/etc` 开头的路径骗过。`relative_to` 是路径语义匹配，不会被骗。

3. **规则引擎不能覆盖系统拦截**：用户规则在 Layer 3，黑名单在 Layer 1b。Layer 3 的 `evaluate()` 在 Layer 1b 之后执行。即使规则文件写了 `Bash(rm*)` allow，代码执行流不会到 Layer 3——Layer 1b 已经 return deny 了。

**R（结果）**：
黑名单和路径沙箱是系统级硬拦截，用户配置不可绕过。`resolve()` + `relative_to()` 防御了符号链接和 `../` 逃逸。`startswith` vs `relative_to` 的选择体现了对路径遍历攻击的理解深度。

---

## Q6: 会话恢复（Session Resume）时，如果上次崩溃在工具执行过程中，恢复后状态是对的吗？

**面试官追问**：JSONL 文件里记录了 assistant(tool_use: ReadFile)，但程序在 tool_result 写入之前崩溃了。恢复时这个悬空的 tool_use 会怎么样？

**S（背景）**：
对话持久化到 JSONL 文件。但写文件不是原子操作——可能在 assistant 消息写入后、tool_result 写入前崩溃。恢复时必须处理不完整消息链。

**T（任务）**：
检测并裁剪不完整的消息链，防止恢复后对话处于非法状态（有 tool_use 没有 tool_result，LLM 会困惑）。

**A（行动）**：

1. **validate_message_chain()** 逐条扫描记录：
```python
def validate_message_chain(records):
    last_valid = 0
    pending_tool_uses: set[str] = set()

    for i, record in enumerate(records):
        if record 是 assistant 且包含 tool_use 块:
            for block in record.content:
                pending_tool_uses.add(block["id"])   # 记录悬空调用

        if record 是 tool_result 且有 tool_use_id:
            pending_tool_uses.discard(record.tool_use_id)  # 清除已配对的

        if not pending_tool_uses:         # 所有调用都配对了
            last_valid = i + 1            # → 这是一个安全的截断点

    return last_valid   # 从这里开始裁掉后面的所有记录
```

2. **resume() 调用裁剪**：
```python
records = records[:valid_count]   # 只保留到最后一个完整配对点
messages = records_to_messages(records)
```

3. **COMPACT_BOUNDARY 处理**：找到最后一个压缩标记，之前的内容不重放（已被摘要替代）。

**R（结果）**：
崩溃在最坏情况下丢失最后 1-2 轮未完成的对话，但恢复后的状态始终合法——不会有悬空的 tool_use。丢失的轮次对用户透明（对话显示"上次帮你读 config.py"→ 实际上重来一次即可）。

---

## Q7: 记忆提取失败或提取到错误信息时，对主对话有什么影响？

**面试官追问**：自动提取每 5 轮跑一次。如果 LLM 提取了错误信息（比如把一次性的调试命令记成了用户偏好），这个错误记忆会持久化吗？会影响后续对话吗？

**S（背景）**：
记忆系统完全依赖 LLM 做信息提取和选择，没有任何人工审核。LLM 本身可能犯错——提取了错误信息、遗漏了重要信息、或记了过时的信息。

**T（任务）**：
设计防御机制，确保错误的记忆不会永久污染后续对话，同时提取失败不影响主对话。

**A（行动）**：

1. **异步执行，不阻塞主对话**：
```python
asyncio.ensure_future(self._extract_memories(conversation))
```
提取失败 → 日志记录 → 主循环继续。用户无感知。

2. **增量提取**：每轮只处理上次提取之后的新消息（`history[self._last_extraction_msg_count:]`），不重复处理整个对话。单次提取的错误影响面有限。

3. **_is_placeholder() 过滤**：
```python
@staticmethod
def _is_placeholder(line):
    stripped = line.strip().lstrip("- ").strip()
    return stripped in {"", "...", "…", "无", "暂无", "N/A"}
```
LLM 可能在无内容的分类下写 `- ...` 或 `- 无`——被过滤掉。

4. **时效性警告**：>1 天的记忆自动标注"可能过时"：
```
This memory is 3 days old. Memories are point-in-time observations, not live state.
Verify against current code before asserting as fact.
```
这降低了过期记忆对 LLM 决策的影响——LLM 看到警告后会更谨慎。

5. **用户可手动清理**：`MemoryManager.clear()` 清空所有自动记忆。最严重的纠错手段交给用户。

**R（结果）**：
记忆系统是"尽力而为"的辅助功能。提取失败或错误的影响有限——增量提取限制错误面，时效性警告降低过期信息的权重。如果记忆完全不准，用户可以一键清空。

---

## Q8: 两个并发工具操作同一个文件会发生什么？比如同时 ReadFile 和 WriteFile 同一个文件？

**面试官追问**：工具分区机制把 ReadFile 和 WriteFile 分到了不同批——ReadFile 在并发批，WriteFile 在串行批。但如果 LLM 在同一轮同时调用 ReadFile(a.py) 和 EditFile(a.py) 呢？

**S（背景）**：
工具分区的逻辑是 `is_concurrency_safe` 决定工具能否并发。ReadFile 标记为 `is_concurrency_safe = True`，WriteFile/EditFile 标记为 `False`。

**T（任务）**：
确保有副作用的工具不与任何工具并发执行，防止读写竞态。

**A（行动）**：

1. **分区算法保证安全**：
```python
def partition_tool_calls(tool_calls, registry):
    batches = []
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = tool.is_concurrency_safe and tool.is_enabled

        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)   # 追加到并发批
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))  # 新开一批
    return batches
```

如果 LLM 调用 `[ReadFile(a.py), EditFile(a.py), ReadFile(b.py)]`：
```
分区结果:
  Batch 0: concurrent=True,  [ReadFile(a.py)]           ← 单读操作，安全
  Batch 1: concurrent=False, [EditFile(a.py)]           ← 有副作用，串行
  Batch 2: concurrent=True,  [ReadFile(b.py)]           ← 单读操作，安全
```

EditFile 永远独自成批，前后批之间是串行的。**不会出现 ReadFile 和 EditFile 同时操作同一个文件的情况**。

2. **FileStateCache 额外保护**：即使并发执行绕过了分区，WriteFile/EditFile 的 `_state_cache.check()` 会检测文件 mtime 是否变化。如果在 ReadFile 和 WriteFile 之间文件被外部修改，WriteFile 会拒绝执行。

**R（结果）**：
分区算法 + FileStateCache 双重保护。即使分区逻辑有 bug，写保护层也会兜底。

---

## Q9: 这个项目在错误处理和兜底方面最大的不足是什么？

**面试官追问**：不要说"已经完美了"，诚实评估。如果你是面试官，你会质疑哪里？

**S（背景）**：
mewcode 是教学/复现项目，不是生产系统。在错误处理的全面性上有意做了简化。

**T（任务）**：
诚实地指出当前系统的弱点，展示你知道"更好的做法"是什么。

**A（行动）**：

**不足 1：notify_queue 纯内存，崩溃全部丢失**
```python
# 当前实现
self._notify_queue: asyncio.Queue[str] = asyncio.Queue()

# 更好的做法：持久化队列 + 消息确认
# - 任务状态写磁盘（SQLite/JSONL）
# - ACK 机制：协调者确认收到通知后才删除
```

**不足 2：Mailbox 无消息确认，读后即删**
```python
# consume() 读了就 f.unlink()
# 如果读后程序崩溃，消息永久丢失
# 更好的做法：读后标记为 "processed" + 定期清理已处理消息
```

**不足 3：没有事务性文件操作**
WriteFile/EditFile 不是原子的。如果在写入过程中程序崩溃，文件可能处于半写入状态。更好的做法是"写临时文件 + 原子重命名"：
```python
tmp_path = path.with_suffix(".tmp")
tmp_path.write_text(content)
os.replace(tmp_path, path)  # 原子操作
```

**不足 4：没有 LLM 调用的重试策略**
网络错误、Rate Limit 只被翻译成异常抛出，上层 Agent 没有自动重试逻辑（除了 max_tokens 断点续传）。

**不足 5：上下文压缩的摘要质量没有验证**
生成摘要后没有检查摘要是否包含了关键信息。如果 LLM 生成的摘要遗漏了重要文件路径，压缩后 Agent 可能完全忘记关键上下文。

**R（结果）**：
这些不足是"教学版 vs 生产版"的差距。mewcode 在教学和工程深度之间做了取舍——核心路径（Agent 循环、压缩、权限）有完善的保护，边缘路径（通信、持久化）简化了实现。面试中能坦诚指出不足并说出改进方案，比声称完美更有说服力。

---

## Q10: 如果让你重新设计这个项目中一个子系统，你会选哪个？怎么改？

**面试官追问**：不是修 bug，是重新设计架构。什么设计决策你现在觉得是错的？

**S（背景）**：
项目 15 个子系统，每个都有设计权衡。经过完整实现和测试后，有些决策可以做得更好。

**T（任务）**：
选出最有改进价值的模块，给出新的设计方案。

**A（行动）**：

**选择：团队通信系统（Mailbox + notify_queue）**

当前设计：两条通信路径（内存队列 + 文件邮箱），没有统一的消息抽象，没有消息确认。

**重新设计方案**：

```python
# 统一的消息总线
class MessageBus:
    """持久化的发布-订阅消息总线"""

    async def publish(self, topic: str, message: Message) -> str:
        # 写入 SQLite：id, topic, sender, receiver, content, status, timestamp
        # status: pending → delivered → acknowledged

    async def subscribe(self, agent_id: str, topics: list[str]):
        # 订阅感兴趣的主题

    async def consume(self, agent_id: str) -> list[Message]:
        # 返回该 agent 的 pending 消息
        # 标记为 delivered（但不删除，等 ACK）

    async def ack(self, message_id: str):
        # 确认收到 → 标记为 acknowledged
        # 定期清理 acknowledged 消息（7天）

    async def health_check(self) -> dict[str, int]:
        # 返回每条主题的积压消息数
```

**改进点**：
1. 统一抽象：不再区分"通知"和"消息"，都是 topic-based pub/sub
2. 持久化 + ACK：每条消息有生命周期，不会丢失
3. 积压监控：能看到"协调者还没读 37 条消息"

**选择这个的原因**：当前的 Mailbox + notify_queue 是唯一有数据丢失风险的子系统，且随着团队规模增大问题会放大。其他子系统（压缩、权限、工具执行）的设计已经足够完善。

**R（结果）**：
这个回答展示了"能从更高维度重新审视架构"的能力——不是改细节，而是换抽象层次。

---

## Q11: 做这个项目最大的难点是什么？怎么解决的？之前流程是什么样的，改成了什么？

**面试官追问**：不要泛泛地说"上下文压缩很难"。我要听具体的技术问题、你尝试过什么方案、为什么第一个方案不行、最终方案为什么行。要有"前后对比"。

---

**S（背景）**：

在开发 Agent 主循环时，遇到了上下文管理的最核心矛盾：**工具结果预算（Layer1）和对话摘要压缩（Layer2）会互相破坏对方的数据。**

具体来说：LLM 的 context window 有限（200K token）。长对话需要两种手段控制窗口——给工具结果"减肥"（Layer1，轻量，每轮跑）和在对话过长时"写摘要"（Layer2，重量，触发式跑）。两者都要操作 `conversation.history`，但它们的使用方式冲突。

**T（任务）**：

设计 Layer1 和 Layer2 的协作方式，确保两层各自的功能正确，且不互相破坏对方需要的数据。

---

**A（行动）—— 三个阶段的演进**：

### 阶段一：直观但错误的方案（Design A — 原地修改）

**原始思路**：最简单的做法——Layer1 每轮直接修改 `conversation.history` 中的工具结果，把超长的替换为 `<persisted-output>` 预览。Layer2 触发时，读到的对话已经是"减肥后"的版本。

```python
# Design A（伪代码）：原地修改
def apply_tool_result_budget(conversation):
    for msg in conversation.history:   # 直接改原对话
        for tr in msg.tool_results:
            if len(tr.content) > 50000:
                tr.content = persist_and_replace(tr.content)  # ← 原地替换
```

**为什么失败**：

当 Layer2 触发时，它看到的对话是这样的：
```
User: 帮我分析日志
Assistant: 好的
User(tool_result): <persisted-output> 完整日志在 /tmp/log.txt，预览: ERROR at line...
```

Layer2 要生成摘要，但它看到的是**被裁剪后的内容**——它不知道完整日志长什么样。生成的摘要变成："用户要求分析日志，日志被存到 /tmp/log.txt"。关键信息丢失了——日志里到底有什么错误、Agent 发现了什么、为什么会触发后续操作，全被 `<persisted-output>` 替代了。

**核心矛盾**：

```
Layer1 需要：裁剪工具结果（省 Token）
Layer2 需要：完整的工具结果（生成准确摘要）
两者操作的是同一个 conversation.history 对象 → 冲突
```

### 阶段二：尝试调顺序（先 Layer2 后 Layer1）

**第二个思路**：先跑 Layer2（需要完整数据）→ 再跑 Layer1（减肥）。这样 Layer2 看到的是完整对话。

```python
# 尝试调顺序
auto_compact(conversation)            # ① 先压缩（需要完整数据）
apply_tool_result_budget(conversation) # ② 再预算（减肥）
```

**为什么还是有问题**：

1. Layer2 只检查 `current_tokens() > 167K`。如果 Layer1 减肥后 Token 从 175K 降到 100K，Layer2 根本不应该触发——但在调顺序后 Layer2 先跑了，基于"减肥前的大 Token 数"做了不必要的压缩。
2. Layer1 的减肥副本 `api_conv` 是每轮发给 LLM 的，不是给 Layer2 用的。如果 Layer2 压缩了对话，`api_conv` 变成基于旧对话的过期数据。
3. 顺序依赖隐式耦合——谁先跑谁后跑变成了一个"知道协议才不犯错"的隐式约定，后来的维护者很容易搞错。

### 阶段三：最终方案（Design B — 副本模式 + 固定执行顺序）

**最终设计**：

```python
# agent.py 主循环（每轮迭代）
while True:
    # ① Layer2: 先跑（修改原始 conversation.history）
    #   如果 Token > 167K → 压缩为 [摘要消息] + [尾部原文]
    compact_result = await auto_compact(conversation, ...)

    # ② Layer1: 后跑（读 conversation.history，生成副本 api_conv）
    #   在副本上做预算控制，不修改原始 conversation
    api_conv, records = apply_tool_result_budget(
        conversation, session_dir, replacement_state
    )

    # ③ LLM 调用用副本
    llm_stream = self.client.stream(api_conv, ...)
```

**三个关键设计决策**：

**决策 1：Design B — Layer1 操作在副本上**

```python
def apply_tool_result_budget(conversation, session_dir, state):
    new_history: list[Message] = []     # ← 新列表，不修改原对话

    for msg in conversation.history:
        if not msg.tool_results:
            new_history.append(msg)     # 无工具结果的直接复制引用
            continue

        # 对工具结果做替换决策，生成新的 ToolResultBlock
        new_tool_results = [应用替换决策后的ToolResultBlock...]
        new_history.append(_copy_message_with_results(msg, new_tool_results))

    # 返回副本，原始 conversation 不变
    new_conv = ConversationManager()
    new_conv.history = new_history
    return new_conv, records
```

**效果**：
- Layer2 拿到的 `conversation.history` 永远是完整的原始数据 → 摘要质量有保障
- Layer1 生成的 `api_conv` 是减肥后的版本 → 发给 LLM 省 Token
- 两者操作不同的对象 → 零冲突

**决策 2：先 Layer2 后 Layer1（固定执行顺序）**

先压缩（可能把 50 条消息变成 6 条），再预算（只需处理 6 条消息）。如果反过来，Layer1 花了时间处理 50 条消息，然后 Layer2 把它们全压缩掉——Layer1 白干。

**决策 3：ContentReplacementState — 跨迭代的状态追踪**

```python
@dataclass
class ContentReplacementState:
    seen_ids: set[str]             # 已经处理过的 tool_use_id
    replacements: dict[str, str]   # tool_use_id → 替换文本
```

同一个工具结果在多次迭代中不应该被重复处理。状态追踪记住每个 tool_use_id 的决策——"这个结果已经被替换过了，下次看到直接用替换文本"。

为什么要这个？因为 Layer1 每轮都运行。第 3 轮时，第 1 轮的工具结果还在对话里。如果不追踪状态，Layer1 会重复判断——每次都发现"这条结果 > 50K，要替换"——但它已经被替换过了，再替换就变成 `<persisted-output>` 套 `<persisted-output>`。

---

**前后对比**：

| 维度 | Design A（原地修改） | Design B（副本模式） |
|------|---------------------|---------------------|
| Layer1 对原始对话 | 直接修改 | 不修改，操作副本 |
| Layer2 看到的工具结果 | 被裁剪的版本 → 摘要不准 | 完整原始版本 → 摘要准确 |
| 执行顺序依赖 | 隐式（先后顺序有隐含假设） | 显式（先 Layer2 后 Layer1 写死在循环里） |
| 跨迭代状态 | 无 → 重复替换 | ContentReplacementState → 一次决策持久化 |
| 可测试性 | 差（两个 Layer 互相污染） | 好（各自独立测试） |
| 数据恢复 | 原始数据丢失 | 原始数据完整，支持回放 |

---

**R（结果）**：

Design B 解决了 Layer1 和 Layer2 的数据冲突。压缩后的摘要包含完整的上下文信息（不是因为数据被裁剪而丢失关键事实）。ContentReplacementState 确保跨迭代一致性。固定执行顺序消除了隐式依赖。这个设计决策是整个 Agent 系统能稳定运行在长对话场景的基石。

**面试官，如果您问我"最大的难点是什么"，这就是我的答案——不是某个技术的实现难度，而是在两个互相冲突的需求之间，通过重新设计数据流（原地修改 → 副本模式）来解开死锁。**
