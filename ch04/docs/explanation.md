# ch04：LLM 客户端 — `client.py`

> 文件：`mewcode/client.py`（606 行，整个项目最核心的文件）
> 依赖：ch01(StreamEvent)、ch02(ProviderConfig)、ch03(ConversationManager, serialization)
> 被依赖：agent.py

---

## 整体结构

```
client.py
├── Prompt Cache 标记      _mark_last_user_tail_for_cache(), _mark_last_tool_for_cache()
├── 异常类                  LLMError → AuthenticationError, RateLimitError, NetworkError
├── LLMClient ABC           抽象基类，定义 stream() 接口
├── AnthropicClient         跟 Claude 对话（SSE 三层事件分发）
├── OpenAIClient            跟 GPT 对话（Responses API）
├── OpenAICompatClient      跟第三方对话（Chat Completions API）
├── create_client()         工厂函数
└── resolve_context_window() 启动时自动拉取窗口大小
```

---

## 一、Prompt Cache 机制

Anthropic 提供 Prompt Cache 功能：如果请求前缀跟上次一模一样，那部分 Token 只需付 10% 费用。需要在请求中标记 `cache_control` 断点。

### 1.1 _mark_last_user_tail_for_cache()

```python
_EPHEMERAL = {"type": "ephemeral"}

def _mark_last_user_tail_for_cache(messages: list[dict[str, Any]]) -> None:
    if not messages:
        return
    for msg in reversed(messages):           # 从后往前找
        if msg.get("role") != "user":        # 找最后一条 user
            continue
        content = msg.get("content")
        if isinstance(content, str):         # 纯文本 → 升级为 block 形式
            msg["content"] = [{"type": "text", "text": content, "cache_control": _EPHEMERAL}]
        elif isinstance(content, list) and content:  # 多块 → 标记最后一块
            last = content[-1]
            if isinstance(last, dict):
                last["cache_control"] = _EPHEMERAL
        return  # 只标记一条
```

**为什么只标记最后一条 user 消息？** 对话前缀（system prompt + tools + 之前的对话）在下一次请求时不变，标记最后一条 user 消息的尾部 = 告诉 Anthropic "从开头到这里的内容，下次请求时可以缓存"。

**原地修改**：这个函数**直接修改**传入的 `messages` 列表——不返回新列表。省内存，但调用方要知道数据被改了。

### 1.2 _mark_last_tool_for_cache()

```python
def _mark_last_tool_for_cache(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not tools:
        return tools
    marked = list(tools)               # 浅拷贝
    last = dict(marked[-1])            # 深拷贝最后一个
    last["cache_control"] = _EPHEMERAL
    marked[-1] = last
    return marked
```

**为什么用拷贝而非原地修改？** `tools` 来自 ToolRegistry——是模块级单例，所有请求共享。如果原地改了，缓存标记的位置在下一次请求就变了。拷贝 = 不影响原数据。

---

## 二、异常体系

```python
class LLMError(Exception):           pass   # 祖宗
class AuthenticationError(LLMError):  pass   # API key 错了
class RateLimitError(LLMError):
    def __init__(self, message, retry_after=None):
        self.retry_after = retry_after       # 服务器说"等 X 秒"
class NetworkError(LLMError):         pass   # 网断了
```

**为什么要包装一层？** Anthropic SDK 和 OpenAI SDK 各有一套自己的异常类型。不包装的话，上层 Agent 要同时 `except anthropic.AuthenticationError` 和 `except openai.AuthenticationError`——绑死了两个 SDK。包装后上层只依赖自己的异常。

`RateLimitError.retry_after` 从 HTTP 响应头提取 `Retry-After`，上层重试时直接用。

---

## 三、AnthropicClient

### 3.1 thinking 模式的两种策略

```python
if self.thinking:
    if _supports_adaptive_thinking(self.model):
        # Claude Opus/Sonnet 4.6+ → 模型自己决定思考深度
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": 0}
    else:
        # Claude 3.5 等旧模型 → 手动分配思考空间
        kwargs["thinking"] = {"type": "enabled",
                              "budget_tokens": max(self.max_output_tokens - 1, 1024)}
```

`_supports_adaptive_thinking()` 做版本号检测：

```python
def _supports_adaptive_thinking(model: str) -> bool:
    for family in ("claude-opus-4-", "claude-sonnet-4-"):
        if model.startswith(family):
            rest = model[len(family):]
            if rest and rest[0].isdigit() and int(rest[0]) >= 6:
                return True
    return False
# "claude-opus-4-6-20250514" → True
# "claude-opus-4-5-xxx"      → False
```

### 3.2 SSE 事件循环：三层 if/elif（核心）

```python
async for event in stream:
    if event.type == "content_block_start":
        block = event.content_block
        if block.type == "thinking":
            in_thinking = True
            thinking_accum = ""
            thinking_signature = ""
        elif block.type == "tool_use":
            current_tool_name = block.name
            current_tool_id = block.id
            json_accum = ""
            yield ToolCallStart(tool_name=..., tool_id=...)

    elif event.type == "content_block_delta":
        delta = event.delta
        if delta.type == "text_delta":
            yield TextDelta(text=delta.text)
        elif delta.type == "thinking_delta":
            thinking_accum += delta.thinking
            yield ThinkingDelta(text=delta.thinking)
        elif delta.type == "signature_delta":
            thinking_signature = delta.signature    # 仅累积，不 yield
        elif delta.type == "input_json_delta":
            json_accum += delta.partial_json
            yield ToolCallDelta(text=delta.partial_json)

    elif event.type == "content_block_stop":
        if in_thinking:
            yield ThinkingComplete(thinking=thinking_accum, signature=thinking_signature)
            in_thinking = False
        if current_tool_name:
            args = json.loads(json_accum) if json_accum else {}
            yield ToolCallComplete(tool_id=..., tool_name=..., arguments=args)
            current_tool_name = ""
            current_tool_id = ""
            json_accum = ""

    elif event.type == "message_stop":
        pass

# 循环结束后
final = await stream.get_final_message()
yield StreamEnd(stop_reason=final.stop_reason, input_tokens=...)
```

**关键状态变量**：`in_thinking`, `current_tool_name`, `current_tool_id`, `json_accum`, `thinking_accum`, `thinking_signature`。这些变量**跨 `async for` 循环的多次暂停保持状态**——Python 生成器会保存整个栈帧。

**顶层是 `event.type` 分支（4类），第二层是 `block.type`（2类）或 `delta.type`（4类）分支。** 这就是前面详细讲过的三层 if/elif 结构。

### 3.3 fetch_model_context_window()

```python
async def fetch_model_context_window(self) -> int | None:
    try:
        info = await self._client.models.retrieve(self.model, timeout=3.0)
        window = getattr(info, "max_input_tokens", None)
        if isinstance(window, int) and window > 0:
            return window
        return None
    except Exception:
        return None
```

**完全尽力而为。** 任何错误（网络、超时、模型不存在）都静默返回 `None`。3 秒超时防止启动卡住。返回 `None` 后让 `get_context_window()` 回退链的下层接管。

---

## 四、OpenAIClient（Responses API）

和 AnthropicClient 结构类似，差异点：

1. **扁平事件名**：`response.output_text.delta`、`response.function_call_arguments.delta`——一层 if/elif 够了
2. **工具调用开始有两处触发**：`output_item.added` 和 `function_call_arguments.delta` 首次到达都可能需要 yield `ToolCallStart`
3. **Token 统计不同**：Anthropic 的 `input_tokens` 不含缓存；OpenAI 的 `input_tokens` 包含缓存，需要 `max(input_tokens - cache_read, 0)` 对齐

---

## 五、OpenAICompatClient（Chat Completions API）

Chat Completions 的流式格式和前两者完全不同：工具调用的增量按**数组索引（index）**下发：

```python
active_calls: dict[int, dict[str, str]] = {}   # {0: {id, name, args}, 1: {...}}

for tc in delta.tool_calls:
    idx = tc.index
    if idx not in active_calls:
        active_calls[idx] = {"id": "", "name": "", "args": ""}
    call = active_calls[idx]
    if tc.function and tc.function.arguments:
        call["args"] += tc.function.arguments  # 增量拼接 JSON 字符串
```

**工具格式转换**：Chat Completions 要求 `{"function": {"name": ..., "parameters": ...}}` 的嵌套结构，而 Responses API 的工具定义是扁平结构。`_convert_tools()` 做这个转换。

---

## 六、create_client() — 工厂函数

```python
def create_client(config: ProviderConfig) -> LLMClient:
    if config.protocol == "anthropic":     return AnthropicClient(config)
    elif config.protocol == "openai":      return OpenAIClient(config)
    elif config.protocol == "openai-compat": return OpenAICompatClient(config)
    raise ValueError(f"Unknown protocol: {config.protocol}")
```

**策略模式 + 工厂模式**。上层 Agent 不关心用的是哪个 LLM——它只调 `LLMClient.stream()`。

---

## 七、resolve_context_window() — 启动时摸底

```python
async def resolve_context_window(config: ProviderConfig) -> None:
    if config.context_window > 0 or config._fetched_context_window > 0:
        return                                 # 已有数据，不重复拉
    if config.protocol != "anthropic":
        return                                 # 只有 Anthropic 支持此接口
    client = create_client(config)
    window = await client.fetch_model_context_window()
    if window:
        config.set_fetched_context_window(window)
```

这是回退链第 2 层的实现。**完全尽力而为**——任何一步失败都静默退出，不影响主流程。OpenAI 协议直接跳过（API 不支持此查询），靠第 3 层和第 4 层兜底。
