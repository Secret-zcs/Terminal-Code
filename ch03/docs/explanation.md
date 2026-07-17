# ch03：对话存储与序列化 — `conversation.py` + `serialization.py`

> 文件：`mewcode/conversation.py` (202行)、`mewcode/serialization.py` (133行)
> 依赖：conversation → 无内部依赖 | serialization → conversation.Message
> 被依赖：client.py、agent.py、context/manager.py、memory/session.py

---

## 一、conversation.py — 对话仓库管理员

### 1.1 最小积木块：三种 Block

```python
@dataclass
class ToolUseBlock:
    tool_use_id: str       # 工具调用唯一编号，如 "tool_001"
    tool_name: str         # 工具名，如 "ReadFile"
    arguments: dict[str, Any]  # 参数，如 {"file_path": "config.py"}

@dataclass
class ToolResultBlock:
    tool_use_id: str       # 对应哪个工具调用
    content: str           # 返回内容
    is_error: bool = False

@dataclass
class ThinkingBlock:
    thinking: str          # 模型的思考过程
    signature: str         # 加密签名（下次请求原样回传）
```

**为什么工具调用和结果是分开的 Block 而非合并？** 因为一条 assistant 消息可能同时包含"文本回复"和"工具调用"。在 Anthropic 协议中，它们是一起返回的。分开存储让序列化层能灵活拼装三种 API 的不同格式。

### 1.2 Message — 把积木拼成一条消息

```python
@dataclass
class Message:
    role: str              # "user"（你）或 "assistant"（AI）
    content: str           # 文本内容
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    tool_results: list[ToolResultBlock] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
```

一条 AI 消息的完整形态：

```
Message(
    role="assistant",
    content="我来帮你",
    thinking_blocks=[ThinkingBlock("用户要读 config.py...")],
    tool_uses=[ToolUseBlock(name="ReadFile", ...)]
)
```

**三种 list 都用 `field(default_factory=list)`**——和 ch02 讲的 mutable default 陷阱同理。如果写 `= []`，所有 Message 实例共享同一个空列表。

### 1.3 Token 估算：锚定法（面试重点）

#### 问题背景

每次跟 AI 对话，API 按 Token 计费。但在调用 API **之前**就需要知道"现在对话大约占了多少 Token"——否则可能超过窗口限制。全量字符估算法（"1000 字符 ≈ 300 Token"）在长对话中越来越不准确。

#### 锚定法原理

```python
@dataclass
class ConversationManager:
    baseline_tokens: int = 0    # 锚点：上次 API 返回的真实 Token 数
    anchor_count: int = 0       # 锚点时的消息条数
```

核心逻辑：

```python
def current_tokens(self) -> int:
    if self.baseline_tokens <= 0:
        return estimate_tokens(self.history)     # 冷启动：全量字符估算
    tail = self.history[self.anchor_count:]      # 热运行：只估算新增部分
    return self.baseline_tokens + estimate_tokens(tail)
```

**举例**：

```
上次 API 调用后：
  history = [msg1, msg2, msg3, msg4]    ← 4 条消息
  真实 Token = 1500
  → baseline_tokens = 1500, anchor_count = 4

新增一条消息后：
  history = [msg1, msg2, msg3, msg4, msg5]  ← 5 条消息

估算 = baseline_tokens + estimate_tokens([msg5])
     = 1500           + 200
     = 1700
```

**类比**：上次称体重是 70kg（锚点），后来换了件外套。不需要重新称整个人，只需要称外套的重量（增量估算），加上 70。

#### 为什么不全量估算？

全量字符估算随对话增长越估越偏（±10% 误差 × 50 条消息 ≈ 可能偏 5000 token）。真实 API 数据是精确的——能用精确数据的地方就用精确数据，只对无法避免的部分做估算。这是**混合精度策略**。

#### 锚点何时归零

```python
def replace_history(self, new_messages: list[Message]) -> None:
    self.history = new_messages
    self.env_injected = False
    self.ltm_injected = False
    self.baseline_tokens = 0     # ← 归零
    self.anchor_count = 0        # ← 归零
```

对话被压缩后（Layer2 auto_compact），整个 history 被替换成摘要+尾部。旧的锚点描述的是压缩前的消息——现在已无意义。归零后 `current_tokens()` 退化为全量字符估算，等下次 API 响应后再重新建立锚点。

#### estimate_tokens() 的实现

```python
_CHARS_PER_TOKEN = 3.5    # 经验值：1 Token ≈ 3.5 个英文字符

def _message_chars(m: Message) -> int:
    n = len(m.content)
    for tb in m.thinking_blocks:    n += len(tb.thinking)
    for tu in m.tool_uses:          n += len(tu.tool_name) + len(json.dumps(tu.arguments))
    for tr in m.tool_results:       n += len(tr.content)
    return n

def estimate_tokens(messages: list[Message]) -> int:
    total = sum(_message_chars(m) for m in messages)
    return int(total / _CHARS_PER_TOKEN)
```

注释里明确写："**刻意做得粗略**——它只覆盖那些尚未锚定到真实 API 用量数值的消息，这部分的精确度本就无关紧要。"

### 1.4 添加消息的四个方法

```python
def add_user_message(self, content):
    self.history.append(Message(role="user", content=content))

def add_assistant_message(self, content, tool_uses=None, thinking_blocks=None):
    self.history.append(Message(
        role="assistant", content=content,
        tool_uses=tool_uses or [],
        thinking_blocks=thinking_blocks or [],
    ))

def add_tool_results_message(self, tool_results):
    self.history.append(Message(role="user", content="", tool_results=tool_results))

def add_system_reminder(self, content):
    self.history.append(Message(
        role="user",
        content=f"<system-reminder>\n{content}\n</system-reminder>",
    ))
```

**注意**：system_reminder 的 `role` 是 `"user"`，不是 `"system"`。因为 Anthropic API 的 system 角色不能出现在 messages 中——system prompt 是单独的请求参数。所以系统提醒只能伪装成 user 消息，用 `<system-reminder>` XML 标签区分。

**对话在 history 中的真实排列**：

```
history[0] = Message(role="user", content="读 config.py")
history[1] = Message(role="user", content="<system-reminder>今天的日期是2026-06-23</system-reminder>")
history[2] = Message(role="assistant", content="好的", tool_uses=[ReadFile])
history[3] = Message(role="user", content="", tool_results=[ReadFile结果])
history[4] = Message(role="assistant", content="这个文件有 255 行...")
```

### 1.5 注入环境信息和长期记忆

```python
def inject_environment(self, context: str) -> None:
    if not self.env_injected:              # 只注一次
        self.history.insert(0, Message(role="user", content=context))
        self.env_injected = True

def inject_long_term_memory(self, instructions, memories) -> None:
    if self.ltm_injected:                  # 也只注一次
        return
    # 包装成 system-reminder
    pos = 1 if self.env_injected else 0    # 环境信息之后，或最开头
    self.history.insert(pos, wrapped)
    self.ltm_injected = True
```

两个标记位 `env_injected` 和 `ltm_injected` 确保环境和记忆**只注入一次**。对话压缩后 `replace_history()` 将它们重置为 `False`——新对话需要重新注入。

---

## 二、serialization.py — 适配器层

### 2.1 为什么需要翻译？

内部用统一的 `Message` 格式，但三家 AI 厂商期望的格式不同：

```
内部 Message    → build_anthropic_messages()       → 发给 Claude
                → build_openai_input()             → 发给 GPT (Responses API)
                → build_chat_completion_messages() → 发给 vLLM/Ollama (Chat API)
```

这是**适配器模式**。内部不变，只换适配器。

### 2.2 Anthropic 格式：多块 content 数组

```python
def build_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for m in messages:
        if m.tool_uses or m.thinking_blocks:
            # assistant 消息包含工具调用/思考 → 用多块 content
            content = []
            for tb in m.thinking_blocks:
                content.append({"type": "thinking", "thinking": tb.thinking, "signature": tb.signature})
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tu in m.tool_uses:
                content.append({"type": "tool_use", "id": tu.tool_use_id, "name": tu.tool_name, "input": tu.arguments})
            result.append({"role": "assistant", "content": content})

        elif m.tool_results:
            # 工具结果 → content 列表，每项一个 tool_result
            content = [{"type": "tool_result", "tool_use_id": tr.tool_use_id, "content": tr.content, "is_error": tr.is_error}
                       for tr in m.tool_results]
            result.append({"role": "user", "content": content})

        else:
            # 纯文本 → 合并连续的 user 消息
            if m.role == "user" and result and result[-1]["role"] == "user" and isinstance(result[-1]["content"], str):
                result[-1]["content"] = result[-1]["content"] + "\n" + m.content
            else:
                result.append({"role": m.role, "content": m.content})
    return result
```

**连续 user 消息合并**：`system-reminder` 是一条 user 消息，用户输入又是另一条。Anthropic 不允许连续两条同角色消息——所以把它们用换行符拼成一条。

```python
# 举例
history = [
    Message(role="user", content="<system-reminder>日期是今天</system-reminder>"),
    Message(role="user", content="帮我读 config.py"),
]

# 合并后
result = [
    {"role": "user", "content": "<system-reminder>日期是今天</system-reminder>\n帮我读 config.py"}
]
```

### 2.3 OpenAI Responses API 格式：扁平消息

```python
def build_openai_input(messages):
    for m in messages:
        if m.tool_uses:
            # 文本和工具调用是两条独立消息
            if m.content:
                result.append({"role": "assistant", "content": m.content})
            for tu in m.tool_uses:
                result.append({"type": "function_call", "name": tu.tool_name, "call_id": tu.tool_use_id,
                               "arguments": json.dumps(tu.arguments)})  # ← JSON 字符串，不是 dict
        elif m.tool_results:
            for tr in m.tool_results:
                result.append({"type": "function_call_output", "call_id": tr.tool_use_id, "output": tr.content})
        else:
            result.append({"role": m.role, "content": m.content})
```

**注意**：OpenAI 的 `arguments` 是 JSON **字符串**（`json.dumps()`），而 Anthropic 的 `input` 是 **dict**。这是 API 设计差异。

### 2.4 OpenAI Chat Completions 格式：嵌套工具调用

```python
def build_chat_completion_messages(messages):
    for m in messages:
        if m.tool_uses:
            # 工具调用嵌套在 assistant 消息的 tool_calls 字段里
            tool_calls = [{"id": tu.tool_use_id, "type": "function",
                           "function": {"name": tu.tool_name, "arguments": json.dumps(tu.arguments)}}
                          for tu in m.tool_uses]
            result.append({"role": "assistant", "content": m.content or None, "tool_calls": tool_calls})
        elif m.tool_results:
            # 每个工具结果是独立的 "tool" 角色消息
            for tr in m.tool_results:
                result.append({"role": "tool", "tool_call_id": tr.tool_use_id, "content": tr.content})
        else:
            result.append({"role": m.role, "content": m.content})
```

三种格式的关键差异一览：

| 特性 | Anthropic | OpenAI Responses | OpenAI Chat |
|------|-----------|-----------------|-------------|
| 工具调用 | content 数组中的 tool_use 块 | 独立 function_call 消息 | assistant 消息的 tool_calls 字段 |
| 工具参数 | `"input": {dict}` | `"arguments": "JSON字符串"` | `"arguments": "JSON字符串"` |
| 工具结果 | content 数组中的 tool_result 块 | 独立 function_call_output 消息 | 独立 tool 角色消息 |
| 思考块 | content 数组中的 thinking 块 | 不支持 | 跳过 |

### 2.5 调度器

```python
def build_messages(messages: list[Message], protocol: str = "anthropic") -> list[dict[str, Any]]:
    if protocol == "openai":
        return build_openai_input(messages)
    if protocol == "openai-compat":
        return build_chat_completion_messages(messages)
    return build_anthropic_messages(messages)
```

默认 Anthropic。如果协议类型不匹配也不报错——降级为 Anthropic 格式。这是一种**宽容的兜底策略**。

---

## 三、数据流总览

```
用户输入 → ConversationManager.add_user_message()
             ↓
        LLM 客户端调用 build_messages() → 翻译成 API 格式 → 发给 AI
             ↓
        AI 返回 → 解析 → ConversationManager.add_assistant_message()
             ↓
        工具执行 → ConversationManager.add_tool_results_message()
             ↓
        循环...
```
