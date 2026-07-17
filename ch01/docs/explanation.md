# ch01：基础类型层 — `mewcode/tools/base.py`

> 文件：`mewcode/tools/base.py`（106 行）
> 依赖：仅 Python 标准库 + Pydantic
> 被依赖：client.py、agent.py、所有工具实现

---

## 1. 为什么这是整个项目的第一个文件

这个文件是整个项目的**类型基石**。它只依赖标准库和 Pydantic，被几乎所有模块引用：

```
                    tools/base.py
                    ┌─────┼─────┐
                    ▼     ▼     ▼
              client.py  agent.py  各具体工具(Bash/ReadFile/...)
              (产出事件) (消费事件) (继承Tool ABC)
```

软件工程中有一个原则叫 **"依赖倒置"**——高层模块不应该依赖低层模块，两者都应该依赖抽象。`base.py` 就是那个抽象层。它定义了"一个工具长什么样"（`Tool` ABC）和"流式事件长什么样"（7 种 `StreamEvent`），所有模块都基于这个约定来协作，互不感知对方的内部实现。

---

## 2. 导入区：逐行分析

```python
from __future__ import annotations
```
这一行改变了 Python 类型注解的求值方式。Python 的类型注解默认在**定义时**求值，加了这行后在**运行时**变成惰性求值的字符串。两个作用：

1. **避免循环引用**：`StreamEvent = TextDelta | ... | StreamEnd` 这行引用了本文件后面定义的类。不加这行，Python 在执行 `TextDelta | ...` 时如果某个类还没定义完就会报 `NameError`。
2. **向前兼容**：`X | Y` 联合类型语法是 Python 3.10 引入的（PEP 604），加这行保证在旧版本 Python 中的兼容性。

```python
from abc import ABC, abstractmethod
```
Python 的抽象基类模块。`ABC` 是基类，`abstractmethod` 是装饰器。组合使用强制子类实现标记的方法——如果子类不实现 `execute()`，**实例化时**才会报 `TypeError`，而不是等到调用时才出错。这是一种**编译期**（确切说是装载期）的接口契约检查。

```python
from dataclasses import dataclass
```
Python 3.7 引入的数据类装饰器。自动生成 `__init__`、`__repr__`、`__eq__` 等样板方法。选择 `@dataclass` 而非 Pydantic `BaseModel` 的原因见 §3。

```python
from typing import Any, Literal
```
- `Any`：关闭类型检查。用于工具参数 `dict[str, Any]`——工具的参数结构各不相同，编译期无法枚举。
- `Literal`：将类型的取值范围限定为几个字面量。`ToolCategory = Literal["read", "write", "command"]` 意味着类型检查器会拒绝 `category = "unknown"` 这样的赋值。

```python
from pydantic import BaseModel
```
Pydantic 的数据模型基类。每个工具的 `params_model` 字段的类型是 `type[BaseModel]`——注意是**类型本身**而非实例。LLM 的每次工具调用会生成不同的参数值，但参数的**结构**（有哪些字段、什么类型）是固定的模板。`model_json_schema()` 把这个模板转成 JSON Schema 发给 LLM。

---

## 3. 常量：设计意图

```python
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".tox", ".mypy_cache"}
```
用 `set` 而非 `list`。唯一操作是 `path in SKIP_DIRS` 成员判断，set 的哈希查找是 O(1)。六个目录是开发环境中不关心的依赖/缓存目录，Glob 和 Grep 工具遍历文件时跳过它们。

```python
MAX_OUTPUT_CHARS = 10000
```
工具输出的默认截断长度。放在这里而非各个工具内部，是因为"多长算太长"是全局策略而非工具个性。Bash/ReadFile/Grep 都共享这个上限。

```python
ToolCategory = Literal["read", "write", "command"]
```
三种安全类别。`Literal` 比 `str` 更严格——编译器期检查。权限系统（ch07）根据类别做分级：`read` 类操作可能自动批准，`command` 类需要用户确认。把三类定义为全局类型别名，保证了整个项目中分类语义的一致性。

---

## 4. ToolResult：工具执行的统一返回值

```python
@dataclass
class ToolResult:
    output: str
    is_error: bool = False
```

**为什么不用 `tuple[str, bool]`？**

```python
# tuple 版本：谁记得 [0] 是内容还是 [1] 是内容？
result = ("文件内容...", False)
if result[1]:   # 是 is_error 还是 output？
    ...

# dataclass 版本：字段名即文档
result = ToolResult(output="文件内容...", is_error=False)
if result.is_error:
    ...
```

`is_error: bool = False` 默认成功，让正常路径更简洁：
```python
return ToolResult(output="Successfully wrote to file")  # is_error 自动为 False
```

Agent 收到 `ToolResult` 后不区分工具类型——所有工具的返回值走同一个处理流程。这就是**多态**：不同的 `execute()` 实现，统一的返回格式。

---

## 5. Tool：所有工具的抽象基类

### 5.1 类属性

```python
class Tool(ABC):
    name: str                      # 工具名，ToolRegistry 的 key
    description: str               # 给 LLM 看的功能描述
    params_model: type[BaseModel]  # 参数的 Pydantic 模型（注意是 type，不是实例）
    category: ToolCategory = "read"
    is_concurrency_safe: bool = False
    is_system_tool: bool = False
    should_defer: bool = False
```

逐字段分析：

| 字段 | 类型 | 用途 | 被谁消费 |
|------|------|------|---------|
| `name` | `str` | ToolRegistry 的注册 key，LLM 通过这个名字调用工具 | ToolRegistry, client.py(发给LLM), agent.py(匹配工具调用) |
| `description` | `str` | 送入 LLM system prompt 的工具说明 | client.py(get_schema → 发给LLM) |
| `params_model` | `type[BaseModel]` | 工具参数的 schema 模板 | agent.py(Pydantic校验LLM传的参数) |
| `category` | `ToolCategory` | read/write/command 三类 | permissions/(权限分级依据) |
| `is_concurrency_safe` | `bool` | 为 True 时可与其他工具并行执行 | agent.py(partition_tool_calls) |
| `is_system_tool` | `bool` | 为 True 时不在用户列表展示 | ToolRegistry(get_all_schemas)，skill executor(过滤器) |
| `should_defer` | `bool` | 为 True 时不发送完整 schema，LLM 通过 ToolSearch 激活 | ToolRegistry(懒加载机制) |

**`params_model: type[BaseModel]` 的类型注解值得注意。** 它是 `type[BaseModel]` 而非 `BaseModel`——表示"BaseModel 的**子类**"，不是"BaseModel 的**实例**"。每个工具的参数 schema 是一个模板（template），LLM 每次调用时填入具体值，Pydantic 自动校验。

**`is_concurrency_safe` 的决策逻辑**：

```python
# agent.py
def partition_tool_calls(tool_calls, registry):
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = tool.is_concurrency_safe and tool.is_enabled
        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)   # 追加到当前并发批（asyncio.gather）
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))  # 新开一批
```

两个 ReadFile 可以放入同一并发批（读操作无副作用），EditFile 必须单独成批（写操作有副作用且可能与前面的读操作冲突）。

**`should_defer` 的懒加载机制**：

```python
# ToolRegistry.get_all_schemas()
for name, tool in self._tools.items():
    if getattr(tool, "should_defer", False) and name not in self._discovered:
        continue   # 懒加载且未被搜索过 → 跳过，不发给 LLM
    schemas.append(tool.get_schema())
```

MCP 工具默认 `should_defer=True`——GitHub MCP 服务器有 20+ 个工具，全部 schema 发给 LLM 每轮浪费 ~2500 token。懒加载只发一句提示 "以下工具可通过 ToolSearch 搜索"，LLM 需要时才激活。

### 5.2 派生属性

```python
@property
def is_read_only(self) -> bool:
    return self.category == "read"
```

`@property` 而非直接字段——因为它是从 `category` **推导**出来的，不是独立数据。如果用字段 `is_read_only = True`，那 `category = "write"` 和 `is_read_only = True` 同时存在就是数据矛盾。单一数据源（Single Source of Truth）原则。

### 5.3 get_schema()：生成 LLM 工具定义

```python
def get_schema(self) -> dict[str, Any]:
    schema = self.params_model.model_json_schema()
    schema.pop("title", None)        # ← 关键行
    return {
        "name": self.name,
        "description": self.description,
        "input_schema": schema,
    }
```

**`schema.pop("title", None)` 做了什么？**

Pydantic 的 `model_json_schema()` 默认生成：
```json
{
  "title": "Params",
  "type": "object",
  "properties": {
    "file_path": {"type": "string", "description": "..."}
  }
}
```

`"title": "Params"` 对 LLM 是**纯噪音**——"Params" 这个类名在工具定义的语境下毫无意义，占 token。删掉。

返回的 dict 结构对应 Anthropic API 的 tool 定义格式：
```json
{
  "name": "ReadFile",
  "description": "Read a file and return its contents with line numbers.",
  "input_schema": {
    "type": "object",
    "properties": {...},
    "required": [...]
  }
}
```

这个结构直接拼到 API 请求的 `tools` 参数里。

### 5.4 execute()：唯一的抽象方法

```python
@abstractmethod
async def execute(self, params: BaseModel) -> ToolResult: ...
```

- **`@abstractmethod`**：子类不实现 → 实例化时报 `TypeError: Can't instantiate abstract class`。接口契约是强制的。
- **`async`**：所有工具都是 I/O 操作（读文件、执行命令、网络请求），异步避免阻塞事件循环。
- **`params: BaseModel`**：Pydantic 已在 agent.py 中完成了校验和类型转换（`tool.params_model.model_validate(tc.arguments)`）。execute 拿到的 params 是**已经校验过的、类型安全的**对象。
- **`-> ToolResult`**：统一的成功/失败表示，不抛异常。工具执行的错误（文件不存在、命令执行失败）应该正常返回 `ToolResult(is_error=True)`，而非抛异常——异常应由 agent.py 在最外层统一处理。

---

## 6. StreamEvent：流式事件的七种类型

这些是 SSE 事件管道中 client.py → agent.py → app.py 之间传递的消息载体。

### 6.1 TextDelta

```python
@dataclass
class TextDelta:
    text: str
```

每一条就是一个 LLM 输出的**增量 token**（不是累计文本）。底层 Anthropic SDK 每收到一个 `content_block_delta` 事件就 yield 一个 `TextDelta`。agent.py 的 `StreamCollector` 负责拼接：

```python
# agent.py StreamCollector.consume()
if isinstance(event, TextDelta):
    self.response.text += event.text     # 累积拼接
    yield StreamText(text=event.text)    # 同时转发给 TUI 实时渲染
```

---

### 6.2 ThinkingDelta & ThinkingComplete

```python
@dataclass
class ThinkingDelta:
    text: str

@dataclass
class ThinkingComplete:
    thinking: str       # 完整思考内容
    signature: str      # 加密签名
```

Claude 的 Extended Thinking 功能。模型生成答案前有一段"内心独白"（类似人类的草稿纸）。这两条事件拆分对应 Anthropic 协议：

| SSE 事件 | 产出 |
|----------|------|
| `content_block_start(thinking)` | 设置 `in_thinking = True` |
| `content_block_delta(thinking_delta)` | `ThinkingDelta(text=delta.thinking)` |
| `content_block_delta(signature_delta)` | 累积 `thinking_signature`（不 yield） |
| `content_block_stop`（在 thinking 块中） | `ThinkingComplete(thinking=accum, signature=sig)` |

**`signature` 的用途**：Anthropic API 要求下一轮请求**原样回传**这段思考内容和签名。如果签名不匹配，API 拒绝请求——防伪造思考过程。agent.py 负责保存并回传。

**为什么 signature_delta 不 yield？** TUI 不需要显示签名（乱码），它只是内部状态。

---

### 6.3 ToolCallStart / ToolCallDelta / ToolCallComplete

```python
@dataclass
class ToolCallStart:
    tool_name: str
    tool_id: str

@dataclass
class ToolCallDelta:
    text: str

@dataclass
class ToolCallComplete:
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]
```

工具调用的**三态拆分**，对应 Anthropic SSE 协议的三阶段：

```
content_block_start(tool_use)        → ToolCallStart(name="ReadFile", id="tool_001")
content_block_delta(input_json)      → ToolCallDelta(text='{"file_path"')
content_block_delta(input_json)      → ToolCallDelta(text=': "config.py"}')
content_block_stop                   → ToolCallComplete(arguments={"file_path": "config.py"})
```

**为什么拆成三个而非一个？**

1. 流式渲染：TUI 在收到 `ToolCallStart` 时就能显示 "● ReadFile …" 的卡片，不用等参数全部到齐。
2. JSON 只在 Complete 时解析：`content_block_delta` 阶段收到的 `input_json` 是不完整的 JSON 片段（`{"file_path"`），无法解析。必须等 `content_block_stop` 后完整 JSON 到齐才能 `json.loads()`。
3. agent.py 的 StreamCollector 只关心 Complete（`ToolCallStart` 和 `ToolCallDelta` 都是 `pass`）。TUI 也主要用 Start 和 Complete，几乎不消费 Delta。

---

### 6.4 StreamEnd

```python
@dataclass
class StreamEnd:
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0      # 缓存命中（提示缓存，按 10% 计费）
    cache_creation: int = 0  # 缓存写入（OpenAI系列始终为 0）
```

一次 LLM 请求结束标记。字段含义：

| 字段 | Anthropic 来源 | OpenAI 来源 | 用途 |
|------|---------------|-------------|------|
| `stop_reason` | `final.stop_reason` | 手动设为 `"end_turn"` | Agent 判断是否需要断点续传 |
| `input_tokens` | `usage.input_tokens` | `usage.input_tokens - cache_read` | 用量统计 |
| `output_tokens` | `usage.output_tokens` | `usage.output_tokens` | 用量统计 |
| `cache_read` | `usage.cache_read_input_tokens` | `input_tokens_details.cached_tokens` | Prompt Cache 命中数 |
| `cache_creation` | `usage.cache_creation_input_tokens` | 0（OpenAI 不上报） | Prompt Cache 新写入数 |

**注意事项**：三家的 input_tokens 含义不同：
- **Anthropic**：`input_tokens` 不含缓存的 token。实际 prompt 大小 = `input + cache_read + cache_creation`。
- **OpenAI Responses API**：`input_tokens` 包含缓存的 token。需要减去 `cache_read` 得到纯 prompt 大小。
- **OpenAI Chat Completions**：同上，`prompt_tokens` 包含缓存。

agent.py 的 `record_usage_anchor()` 统一处理了这个差异：
```python
def record_usage_anchor(self, input_tokens, output_tokens, cache_read, cache_creation):
    self.baseline_tokens = input_tokens + cache_read + cache_creation + output_tokens
```

---

### 6.5 StreamEvent 联合类型

```python
StreamEvent = (
    TextDelta
    | ThinkingDelta
    | ThinkingComplete
    | ToolCallStart
    | ToolCallDelta
    | ToolCallComplete
    | StreamEnd
)
```

这不是运行时逻辑，是**类型标注**。两个作用：

1. **函数返回类型**：`async def stream(...) -> AsyncIterator[StreamEvent]`
2. **穷尽性检查**：agent.py 的 `StreamCollector.consume()` 里用 `isinstance` 分支。mypy/pyright 能检查是否覆盖了所有 7 种类型：

```python
async for event in stream:
    if isinstance(event, TextDelta):       ...
    elif isinstance(event, ThinkingDelta): ...
    elif isinstance(event, ThinkingComplete): ...
    # 如果你漏了 ToolCallDelta，类型检查器会警告：
    # "Type ToolCallDelta is not handled"
```

---

## 7. 关键设计决策汇总

| 决策 | 选择 | 理由 |
|------|------|------|
| 事件用 dataclass 还是 BaseModel | `@dataclass` | 热路径每秒数千事件，dataclass 创建开销 ~0.2μs vs Pydantic ~50μs |
| Tool 用 dataclass 还是普通类 | 普通类 + ABC | Tool 有 @abstractmethod（行为契约），dataclass 是纯数据容器 |
| 事件分发用 isinstance 还是字符串 | `isinstance` | 类型安全 + IDE 自动补全 + mypy 穷尽检查 |
| 工具调用拆成 Start/Delta/Complete | 三态拆分 | 流式渲染+JSON 完整性+不同消费方各取所需 |
| 联合类型用 `X \| Y` 还是 `Union[X, Y]` | `X \| Y` | PEP 604 新语法，更简洁 |

---

## 8. 完整源码

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".tox", ".mypy_cache"}

MAX_OUTPUT_CHARS = 10000

ToolCategory = Literal["read", "write", "command"]


@dataclass
class ToolResult:
    output: str
    is_error: bool = False


class Tool(ABC):
    name: str
    description: str
    params_model: type[BaseModel]
    category: ToolCategory = "read"
    is_concurrency_safe: bool = False
    is_system_tool: bool = False
    should_defer: bool = False

    @property
    def is_read_only(self) -> bool:
        return self.category == "read"

    def get_schema(self) -> dict[str, Any]:
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    @abstractmethod
    async def execute(self, params: BaseModel) -> ToolResult: ...


# --- 流式事件 ---

@dataclass
class TextDelta:
    text: str

@dataclass
class ToolCallStart:
    tool_name: str
    tool_id: str

@dataclass
class ToolCallDelta:
    text: str

@dataclass
class ToolCallComplete:
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]

@dataclass
class ThinkingDelta:
    text: str

@dataclass
class ThinkingComplete:
    thinking: str
    signature: str

@dataclass
class StreamEnd:
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0


StreamEvent = (
    TextDelta
    | ThinkingDelta
    | ThinkingComplete
    | ToolCallStart
    | ToolCallDelta
    | ToolCallComplete
    | StreamEnd
)
```

---

## 9. 依赖关系

```
本模块依赖：
  - pydantic (BaseModel)
  - abc (ABC, abstractmethod)
  - dataclasses (@dataclass)
  - typing (Any, Literal)

被依赖：
  - client.py      → import TextDelta, ThinkingDelta, ThinkingComplete,
                      ToolCallStart, ToolCallDelta, ToolCallComplete,
                      StreamEnd, StreamEvent
  - agent.py       → import 上述所有类型 + ToolResult
  - 所有工具实现    → from mewcode.tools.base import Tool, ToolResult
  - tools/__init__.py → from mewcode.tools.base import Tool
```
