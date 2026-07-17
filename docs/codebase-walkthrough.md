# MewCode 代码智能体项目逐模块讲解

> 目标：从项目的基础模块开始，按依赖顺序逐个模块讲解代码。
> 要求：每段代码都说明“做了什么”“为什么这样设计”“对后续模块有什么影响”。
> 留档方式：每次更新本文档，都在文末 `变更留档` 中追加记录。

---

## 0. 讲解顺序与依据

本项目是一个终端代码智能体，核心链路可以概括为：

```text
工具/事件基础类型
  -> LLM 客户端协议适配
  -> Agent 主循环
  -> 权限/上下文/记忆/Hook
  -> Textual 终端应用
  -> 命令、子智能体、团队协作、MCP、技能等扩展层
```

因此讲解不直接从 `mewcode/__main__.py` 开始，而是从更底层的公共抽象开始。理由是：

1. `__main__.py` 是运行入口，但它主要负责组装对象；如果先讲入口，会频繁遇到尚未解释的类型。
2. `mewcode/tools/base.py` 定义了工具、工具结果、流式事件，是 `client.py`、`agent.py` 和所有工具实现共同依赖的底层契约。
3. 先理解基础类型，再看 Agent 主循环，才能看懂“模型输出 -> 工具调用 -> 工具结果 -> 下一轮模型输入”的闭环。

当前采用的第一轮模块顺序如下：

| 顺序 | 模块 | 讲解重点 | 选择理由 |
|---:|---|---|---|
| 1 | `mewcode/tools/base.py` | 工具抽象、工具结果、流式事件 | 全项目的工具与事件类型基础 |
| 2 | `mewcode/tools/__init__.py` | 默认工具注册表 | 连接抽象工具与具体工具实现 |
| 3 | `mewcode/client.py` | LLM 客户端与流式事件生成 | 将模型协议转换为内部事件 |
| 4 | `mewcode/agent.py` | Agent 主循环、工具执行、上下文管理 | 项目的核心决策和执行引擎 |
| 5 | `mewcode/__main__.py` | CLI 入口与非交互模式 | 解释启动流程和对象组装 |
| 6 | `mewcode/app.py` | Textual 交互界面 | 解释交互式终端应用如何驱动 Agent |

后续会按这个顺序逐步追加，遇到依赖跳转时再补充对应模块。

---

## 1. 第一模块：`mewcode/tools/base.py`

### 1.1 模块定位

`mewcode/tools/base.py` 是项目的基础类型层。它不直接执行模型请求，也不直接操作文件系统，而是定义一组稳定契约：

- 一个工具应该具备哪些字段。
- 工具执行后应该返回什么结构。
- 模型流式输出应该被拆成哪些事件。
- Agent、Client、UI、具体工具之间应该如何传递数据。

它的价值不是“代码多复杂”，而是“后续模块都要遵守它定义的接口”。

---

### 1.2 文件头注释

源码：

```python
# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
```

这几行是项目来源与推广信息，对运行逻辑没有影响。

设计理由：

- 放在文件顶部，表示版权或来源说明。
- 不参与 Python 执行，也不会影响导入、类型检查或测试。
- 对代码审计来说，它属于元信息，不属于业务逻辑。

对后续模块的影响：

- 无直接影响。
- 如果项目未来要正式发布，建议把这类信息统一放到 `README.md` 或许可证文件中，避免每个源码文件重复。

---

### 1.3 延迟类型注解

源码：

```python
from __future__ import annotations
```

这行启用延迟类型注解。简单说，Python 不会在定义函数或类时立刻解析所有类型注解，而是把注解延后处理。

为什么这样设计：

- 本文件后面会定义联合类型 `StreamEvent = TextDelta | ... | StreamEnd`。
- 项目其他模块中也存在大量类之间相互引用的情况。
- 延迟注解可以减少循环引用和前向引用问题。

如果不这样做：

- 某些类型在定义时尚未存在，可能需要写成字符串形式，例如 `"CompactBoundary"`。
- 类型注解会更繁琐，也更容易因为导入顺序产生问题。

对后续模块的影响：

- `agent.py`、`client.py` 可以更自然地使用现代 Python 类型写法。
- 这也是项目要求 Python `>=3.11` 后较自然的选择。

---

### 1.4 标准库导入

源码：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal
```

逐行说明：

| 导入 | 用途 | 为什么需要 |
|---|---|---|
| `ABC` | 定义抽象基类 | 让 `Tool` 成为必须被继承实现的接口 |
| `abstractmethod` | 标记抽象方法 | 强制具体工具实现 `execute()` |
| `dataclass` | 快速定义数据对象 | 用于工具结果和事件类型，减少样板代码 |
| `Any` | 表示任意类型 | 工具参数来自模型输出，结构不固定 |
| `Literal` | 限定字符串字面量 | 限定工具类别只能是 `"read"`、`"write"`、`"command"` |

为什么这样设计：

- `ABC + abstractmethod` 适合表达“行为契约”。
- `dataclass` 适合表达“只承载数据的事件对象”。
- `Literal` 比普通 `str` 更严格，可以让类型检查器提前发现错误分类。

对后续模块的影响：

- 所有工具实现都必须遵守 `Tool` 的抽象接口。
- Agent 处理事件时可以通过类型判断区分 `TextDelta`、`ToolCallComplete`、`StreamEnd` 等事件。

---

### 1.5 Pydantic 导入

源码：

```python
from pydantic import BaseModel
```

`BaseModel` 是 Pydantic 的基础模型类，用来描述和校验结构化数据。

在本文件中的主要用途：

- 每个工具通过 `params_model: type[BaseModel]` 声明自己的参数模型。
- Agent 收到模型产生的工具调用参数后，可以用 Pydantic 校验。
- 工具 schema 可以通过 `model_json_schema()` 转换成模型 API 可理解的 JSON Schema。

为什么不用普通 `dict`：

- 普通 `dict` 无法表达必填字段、字段类型和字段说明。
- LLM 工具调用需要 schema 描述参数结构。
- Pydantic 同时解决了“运行时校验”和“schema 生成”两个问题。

对后续模块的影响：

- `tools/read_file.py`、`tools/bash.py`、`tools/edit_file.py` 等具体工具会定义自己的参数模型。
- `agent.py` 执行工具前会用这些模型校验模型传来的参数。

---

### 1.6 跳过目录常量

源码：

```python
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".tox", ".mypy_cache"}
```

这个集合定义了文件搜索类工具默认跳过的目录。

每个目录的意义：

| 目录 | 跳过理由 |
|---|---|
| `.git` | 版本库内部对象，文件多且不是业务源码 |
| `.venv` | Python 虚拟环境依赖，不应作为项目源码搜索 |
| `node_modules` | 前端依赖目录，体积大且噪音高 |
| `__pycache__` | Python 字节码缓存 |
| `.tox` | 测试环境缓存 |
| `.mypy_cache` | 类型检查缓存 |

为什么使用 `set`：

- 主要操作是判断目录名是否在跳过列表中。
- `set` 的成员判断平均复杂度是 O(1)。
- 相比 `list`，语义上也更接近“无序的唯一集合”。

对后续模块的影响：

- `glob`、`grep` 等遍历文件的工具可以复用这个常量。
- 这能减少无意义扫描，提高速度，并降低把依赖文件误交给模型的风险。

---

### 1.7 工具输出长度上限

源码：

```python
MAX_OUTPUT_CHARS = 10000
```

这是工具输出的默认最大字符数。

为什么需要上限：

- 工具可能读取大文件、运行长命令、搜索大量结果。
- 如果原样塞回模型上下文，会快速耗尽上下文窗口。
- 过长输出也会降低模型注意力，让关键结果被噪音淹没。

为什么放在基础模块：

- 输出截断不是某一个工具的私有策略，而是整个 Agent 工具系统的通用策略。
- 放在基础层可以让多个工具保持一致。

对后续模块的影响：

- `bash.py`、`read_file.py`、`grep.py` 等工具都可以围绕这个上限处理输出。
- Agent 对工具结果做上下文预算控制时，也能有统一的尺度。

---

### 1.8 工具类别类型

源码：

```python
ToolCategory = Literal["read", "write", "command"]
```

`ToolCategory` 定义工具安全类别，只允许三个值：

- `read`：只读工具，例如读取文件、搜索文件。
- `write`：写入工具，例如编辑文件、创建文件。
- `command`：命令工具，例如运行 shell 命令。

为什么这样划分：

- 代码智能体最重要的风险边界是“读、写、执行命令”。
- 权限系统可以根据类别做不同决策。
- UI 也可以根据类别展示不同风险提示。

为什么用 `Literal`：

- 如果写成 `category = "unknown"`，类型检查器可以提前发现。
- 相比枚举，`Literal` 更轻量，适合这里的小规模固定字符串集合。

对后续模块的影响：

- 权限检查器可以根据 `tool.category` 判断是否需要用户确认。
- 工具并发执行时，也可以结合类别判断是否安全。

---

### 1.9 `ToolResult`：工具执行结果

源码：

```python
@dataclass
class ToolResult:
    output: str
    is_error: bool = False
```

这是所有工具执行后的统一返回值。

字段解释：

| 字段 | 类型 | 含义 |
|---|---|---|
| `output` | `str` | 工具返回给 Agent 和模型的文本内容 |
| `is_error` | `bool` | 本次工具执行是否失败，默认成功 |

为什么用 `dataclass`：

- 这个类只承载数据，没有复杂行为。
- `dataclass` 自动生成初始化方法和可读的调试输出。
- 比手写 `__init__` 更简洁。

为什么不用元组：

```python
("file content", False)
```

这种写法需要记住第 1 个元素和第 2 个元素分别代表什么，长期维护容易出错。

`ToolResult(output="file content", is_error=False)` 的字段名本身就是文档。

为什么 `is_error` 默认是 `False`：

- 成功是工具执行的常规路径。
- 具体工具只需在失败时显式写 `is_error=True`。
- 能减少正常路径代码噪音。

对后续模块的影响：

- Agent 不需要理解每个工具的私有返回格式。
- 所有工具结果都可以统一转成模型下一轮输入。

---

### 1.10 `Tool` 抽象基类

源码：

```python
class Tool(ABC):
    name: str
    description: str
    params_model: type[BaseModel]
    category: ToolCategory = "read"
    is_concurrency_safe: bool = False
    is_system_tool: bool = False
    should_defer: bool = False
```

`Tool` 是所有具体工具的基类。

字段解释：

| 字段 | 含义 | 设计理由 |
|---|---|---|
| `name` | 工具名 | 模型调用工具时使用的稳定标识 |
| `description` | 工具说明 | 发送给模型，帮助模型决定何时调用 |
| `params_model` | 参数模型类 | 生成 schema，并校验模型传入参数 |
| `category` | 工具类别 | 权限系统判断风险等级 |
| `is_concurrency_safe` | 是否可并发 | 读类工具通常可以并发，写类工具通常不行 |
| `is_system_tool` | 是否系统工具 | 内部工具可隐藏或区别展示 |
| `should_defer` | 是否延迟暴露 | 大量工具场景下减少 prompt 体积 |

为什么 `Tool` 是普通类而不是 `dataclass`：

- `Tool` 不只是数据容器，它表达的是行为接口。
- 它包含抽象方法 `execute()`。
- 每个具体工具通常会有自己的执行逻辑、依赖对象和状态。

为什么默认 `category = "read"`：

- 只读是风险最低类别。
- 简单工具如果忘记设置类别，默认不会被当成写操作或命令操作。
- 但从安全角度看，具体写入或命令工具必须显式覆盖这个字段。

对后续模块的影响：

- `ToolRegistry` 会按 `name` 注册和查找工具。
- LLM 客户端会读取 `description` 和 `params_model` 生成工具列表。
- Agent 会调用 `execute()` 执行具体工具。

---

### 1.11 `is_read_only` 派生属性

源码：

```python
    @property
    def is_read_only(self) -> bool:
        return self.category == "read"
```

这个属性根据 `category` 动态判断工具是否只读。

为什么用 `@property`：

- `is_read_only` 是从 `category` 推导出来的，不应作为独立字段维护。
- 如果同时维护 `category` 和 `is_read_only`，可能出现矛盾状态。

例如：

```python
category = "write"
is_read_only = True
```

这种状态在语义上冲突。使用派生属性可以避免这类不一致。

设计原则：

- 单一事实来源。
- 能由已有字段推导的值，不重复存储。

对后续模块的影响：

- 权限、展示或过滤逻辑可以直接调用 `tool.is_read_only`。
- 代码可读性比到处写 `tool.category == "read"` 更好。

---

### 1.12 `get_schema()`：生成模型工具定义

源码：

```python
    def get_schema(self) -> dict[str, Any]:
        schema = self.params_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }
```

这个方法把内部工具对象转换成 LLM API 能理解的工具 schema。

逐行说明：

```python
schema = self.params_model.model_json_schema()
```

从 Pydantic 参数模型生成 JSON Schema。

选择理由：

- 不手写 schema，避免代码和 schema 不一致。
- 参数模型既能用于运行时校验，也能用于给模型描述参数结构。

```python
schema.pop("title", None)
```

删除 Pydantic 自动生成的 `title` 字段。

选择理由：

- `title` 通常只是参数模型类名，对模型理解工具没有太大帮助。
- 删除它可以减少上下文噪音。
- `None` 表示即使没有这个字段也不报错。

```python
return {
    "name": self.name,
    "description": self.description,
    "input_schema": schema,
}
```

返回标准工具定义。

字段含义：

- `name`：模型调用工具时使用。
- `description`：模型判断工具用途时使用。
- `input_schema`：模型生成工具参数时参考。

对后续模块的影响：

- `client.py` 会把这些 schema 发送给 LLM。
- `agent.py` 会根据模型返回的 `tool_name` 找回对应工具并执行。

---

### 1.13 `execute()`：工具执行接口

源码：

```python
    @abstractmethod
    async def execute(self, params: BaseModel) -> ToolResult: ...
```

这是所有具体工具必须实现的方法。

逐段说明：

| 片段 | 含义 |
|---|---|
| `@abstractmethod` | 标记为抽象方法，子类必须实现 |
| `async def` | 工具执行是异步过程 |
| `params: BaseModel` | 参数已经过 Pydantic 模型校验 |
| `-> ToolResult` | 返回统一工具结果 |
| `...` | 函数体占位，表示接口不提供默认实现 |

为什么是异步方法：

- 读文件、执行命令、访问网络、调用 MCP 都可能是 I/O 操作。
- Agent 主循环需要在等待 I/O 时保持响应能力。
- 多个并发安全工具可以通过异步机制并发执行。

为什么返回 `ToolResult` 而不是直接返回字符串：

- 字符串只能表达输出，不能表达是否失败。
- `ToolResult` 同时包含输出内容和错误标记。
- Agent 可以把成功和失败都作为结构化工具结果反馈给模型。

对后续模块的影响：

- 每个具体工具都必须实现这个接口。
- Agent 可以用统一方式执行所有工具，不需要为每种工具写特殊分支。

---

### 1.14 流式事件分区注释

源码：

```python
# --- 流式事件 ---
```

这是一个代码分区注释，表示下面开始定义模型流式输出相关事件。

为什么需要分区：

- 文件前半部分定义工具抽象。
- 文件后半部分定义 LLM 流事件。
- 分区注释能帮助读者快速定位概念边界。

对后续模块的影响：

- 无运行时影响。
- 对维护者有阅读帮助。

---

### 1.15 `TextDelta`：普通文本增量

源码：

```python
@dataclass
class TextDelta:
    text: str
```

`TextDelta` 表示模型普通回答文本的一小段增量。

为什么是“增量”而不是完整文本：

- LLM 通常以流式方式返回内容。
- UI 可以边接收边显示，用户不必等整段回答完成。
- Agent 可以同时收集完整内容并向界面转发增量。

为什么用 `dataclass`：

- 只包含一个字段 `text`。
- 没有复杂行为。
- 适合轻量数据事件。

对后续模块的影响：

- `client.py` 从模型流里产生 `TextDelta`。
- `agent.py` 收集这些增量，拼成完整回答。
- `app.py` 可以实时渲染这些文本。

---

### 1.16 `ToolCallStart`：工具调用开始

源码：

```python
@dataclass
class ToolCallStart:
    tool_name: str
    tool_id: str
```

这个事件表示模型开始发起一次工具调用。

字段含义：

| 字段 | 含义 |
|---|---|
| `tool_name` | 模型要调用的工具名 |
| `tool_id` | 本次工具调用的唯一标识 |

为什么需要开始事件：

- 流式协议中，工具调用参数可能还没完整到达。
- UI 可以先展示“模型准备调用某工具”。
- 后续的参数片段和结果可以通过 `tool_id` 关联起来。

对后续模块的影响：

- `client.py` 根据模型协议生成该事件。
- `agent.py` 当前主要在完成事件后执行工具，但事件类型保留了更丰富的 UI 能力。

---

### 1.17 `ToolCallDelta`：工具调用参数片段

源码：

```python
@dataclass
class ToolCallDelta:
    text: str
```

这个事件表示工具调用参数 JSON 的一个片段。

为什么参数会被拆成片段：

- 流式 API 返回的是增量。
- 工具参数本质上是一段 JSON，但中间状态可能不是合法 JSON。

例如模型可能先返回：

```json
{"file_path"
```

再返回：

```json
: "mewcode/config.py"}
```

只有拼接完成后才能解析。

为什么不在这里解析：

- 单个 delta 可能不是完整 JSON。
- 解析应该等到工具调用完成事件。

对后续模块的影响：

- `client.py` 负责收集参数片段。
- `ToolCallComplete` 才携带解析后的完整参数。

---

### 1.18 `ToolCallComplete`：工具调用完成

源码：

```python
@dataclass
class ToolCallComplete:
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]
```

这个事件表示一次工具调用已经完整生成，可以执行。

字段含义：

| 字段 | 含义 |
|---|---|
| `tool_id` | 本次调用 ID，用于和结果对应 |
| `tool_name` | 要执行的工具名 |
| `arguments` | 已解析完成的工具参数 |

为什么 `arguments` 是 `dict[str, Any]`：

- 不同工具的参数结构不同。
- 在基础事件层无法提前知道每个工具的具体字段。
- 真正执行前，Agent 会根据工具的 `params_model` 做更严格校验。

对后续模块的影响：

- `agent.py` 会收集 `ToolCallComplete`。
- 然后根据 `tool_name` 找到工具。
- 再用工具自己的参数模型校验 `arguments`。
- 最后调用 `tool.execute(params)`。

---

### 1.19 `ThinkingDelta`：思考文本增量

源码：

```python
@dataclass
class ThinkingDelta:
    text: str
```

这个事件表示模型思考内容的一小段增量。

为什么和 `TextDelta` 分开：

- 普通回答文本是直接展示给用户的最终内容。
- 思考内容属于模型中间过程，处理方式和展示策略不同。
- 分开建模可以让 UI 或 Agent 选择是否显示、记录或忽略思考内容。

对后续模块的影响：

- `agent.py` 可以把思考增量转成自己的 `ThinkingText` 事件。
- UI 可以单独处理“思考中”的展示状态。

---

### 1.20 `ThinkingComplete`：完整思考块

源码：

```python
@dataclass
class ThinkingComplete:
    thinking: str
    signature: str
```

这个事件表示一个完整思考块结束。

字段含义：

| 字段 | 含义 |
|---|---|
| `thinking` | 完整思考内容 |
| `signature` | 与思考内容关联的签名 |

为什么需要完整事件：

- 增量事件适合实时展示。
- 完整事件适合持久化、回传或后续上下文管理。

为什么有 `signature`：

- 某些模型协议要求后续请求保留思考块的签名。
- 签名可用于证明思考内容没有被篡改。

对后续模块的影响：

- `agent.py` 会把完整思考块收集到响应对象中。
- 会话管理模块可能需要保存它，供下一轮请求使用。

---

### 1.21 `StreamEnd`：一次模型流结束

源码：

```python
@dataclass
class StreamEnd:
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    # API 返回的 prompt cache 用量。Anthropic 把缓存前缀 token 分为
    # "read"（cache 命中，按 10% 计费）和 "creation"（cache 写入）。
    # input_tokens 已排除这两部分，因此实际 prompt 大小 =
    # input + cache_read + cache_creation。OpenAI 系列只暴露
    # cache_read（通过 *_tokens_details.cached_tokens），没有 creation
    # 计数，所以 cache_creation 在那边始终为 0。
    cache_read: int = 0
    cache_creation: int = 0
```

这个事件表示一次 LLM 流式响应结束。

字段解释：

| 字段 | 含义 |
|---|---|
| `stop_reason` | 模型停止原因，例如自然结束或需要工具 |
| `input_tokens` | 本次请求输入 token |
| `output_tokens` | 本次请求输出 token |
| `cache_read` | prompt cache 命中的 token 数 |
| `cache_creation` | prompt cache 新写入的 token 数 |

为什么 token 字段默认是 `0`：

- 不同模型提供商返回的用量字段不完全一致。
- 某些测试或模拟流可能不提供 token 统计。
- 默认值保证事件对象仍可构造，调用方再根据实际情况处理。

为什么注释特别解释缓存：

- Anthropic 和 OpenAI 对缓存 token 的统计口径不同。
- 如果不统一理解，后续上下文预算、费用估算和压缩逻辑都可能算错。

对后续模块的影响：

- `agent.py` 会根据 `StreamEnd` 更新 token 使用量。
- 上下文管理模块会根据 token 预算决定是否压缩对话。
- UI 可以展示输入、输出和缓存用量。

---

### 1.22 `StreamEvent`：流式事件联合类型

源码：

```python
StreamEvent = TextDelta | ThinkingDelta | ThinkingComplete | ToolCallStart | ToolCallDelta | ToolCallComplete | StreamEnd
```

这是一个联合类型，表示模型流中可能出现的所有事件。

为什么这样定义：

- `client.py` 的流式方法可以声明返回 `AsyncIterator[StreamEvent]`。
- `agent.py` 消费事件时可以用 `isinstance` 分支处理不同事件。
- 类型检查器和 IDE 可以知道事件有哪些可能形态。

为什么不是基类继承：

- 这些事件都只是简单数据结构。
- 没有共享行为需要抽象到父类。
- 联合类型更直接，也更轻量。

对后续模块的影响：

- Client 层负责生产这些事件。
- Agent 层负责消费这些事件。
- UI 层看到的是 Agent 再转换后的应用事件。

---

## 2. 第一模块设计总结

`mewcode/tools/base.py` 的核心设计可以总结为三句话：

1. 用 `Tool` 抽象基类统一所有工具的形状。
2. 用 `ToolResult` 统一所有工具的执行结果。
3. 用 `StreamEvent` 统一模型流式输出在系统内部的表达。

这些设计让后续模块可以低耦合协作：

- 具体工具只关心如何实现 `execute()`。
- Client 只关心如何把不同模型协议转成统一事件。
- Agent 只关心如何消费事件、执行工具并推进循环。
- UI 只关心如何展示 Agent 转发出的状态。

---

## 3. 第二模块：`mewcode/tools/__init__.py`

### 3.1 模块定位

`mewcode/tools/__init__.py` 是工具系统的“注册中心”。第一模块 `base.py` 定义了单个工具应该长什么样，而本模块负责管理一组工具：

- 注册工具。
- 按名称查找工具。
- 启用或禁用工具。
- 管理延迟暴露的工具。
- 根据模型协议输出不同格式的工具 schema。
- 创建项目默认工具集合。

它位于基础抽象和具体工具之间：

```text
mewcode/tools/base.py
  -> mewcode/tools/__init__.py
    -> ReadFile / WriteFile / EditFile / Bash / Glob / Grep
      -> agent.py 调用 registry 获取工具并执行
      -> client.py 调用 registry 获取 schema 并发送给模型
```

为什么第二个讲它：

- `Tool` 只是单个工具的抽象。
- Agent 实际运行时需要的是“工具集合”。
- `ToolRegistry` 正好解释了工具如何从类定义进入 Agent 执行链路。

---

### 3.2 文件头注释

源码：

```python
# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
```

这与第一模块相同，是来源说明，不影响运行逻辑。

设计影响：

- 无运行时影响。
- 若项目后续工程化，建议统一移到项目级文档或许可证说明中，减少源码重复噪音。

---

### 3.3 延迟类型注解

源码：

```python
from __future__ import annotations
```

作用与第一模块一致：延迟解析类型注解。

在本模块中的价值：

- `create_default_registry(file_cache: FileCache | None = None, ...)` 使用了 `FileCache` 类型。
- `FileCache` 只在类型检查时导入，运行时不直接导入。
- 延迟注解让这种“仅类型依赖”更自然。

为什么这样设计：

- 工具注册表是底层模块，不应该因为类型注解过早导入缓存模块。
- 避免不必要的运行时依赖和潜在循环导入。

---

### 3.4 类型导入

源码：

```python
from typing import TYPE_CHECKING, Any
```

逐项说明：

| 导入 | 用途 | 选择理由 |
|---|---|---|
| `TYPE_CHECKING` | 区分类型检查期和运行期 | 避免运行时导入只用于注解的模块 |
| `Any` | 表示任意类型 | `file_history` 未固定接口，暂时用宽类型 |

为什么需要 `TYPE_CHECKING`：

- Python 运行时会执行普通 import。
- 如果某个导入只为类型注解服务，运行时导入就是额外成本。
- 在复杂项目中，额外导入还可能引起循环依赖。

为什么 `file_history` 用 `Any`：

- 当前 `create_default_registry()` 只把它透传给 `WriteFile` 和 `EditFile`。
- 本模块不关心它的具体方法，只负责组装。
- 更严格的设计可以后续引入 `Protocol`，表达它至少需要 `track_edit()` 方法。

---

### 3.5 导入工具抽象

源码：

```python
from mewcode.tools.base import Tool
```

本模块只从 `base.py` 导入 `Tool`。

为什么只导入 `Tool`：

- 注册表只管理工具对象，不直接执行工具结果，也不处理流式事件。
- `ToolResult`、`StreamEvent` 等类型属于 Agent 或 Client 执行阶段，不属于注册阶段。

对后续模块的影响：

- `ToolRegistry` 的内部字典可以声明为 `dict[str, Tool]`。
- 注册表不绑定任何具体工具实现，保持通用。

---

### 3.6 类型检查期导入 `FileCache`

源码：

```python
if TYPE_CHECKING:
    from mewcode.cache import FileCache
```

这段代码只在类型检查器运行时生效，程序正常执行时不会导入 `mewcode.cache`。

为什么这样设计：

- `FileCache` 只用于函数参数类型注解。
- 运行时导入会增加模块加载成本。
- 如果 `cache.py` 未来反向依赖工具模块，普通导入可能造成循环导入。

对后续模块的影响：

- `create_default_registry()` 可以保留清晰类型签名。
- 同时避免注册表模块和缓存模块在运行期强耦合。

---

### 3.7 `ToolRegistry` 类定义

源码：

```python
class ToolRegistry:
```

`ToolRegistry` 是工具注册表，负责维护工具集合状态。

为什么需要注册表：

- Agent 不能硬编码每个工具类，否则新增工具要改 Agent。
- Client 也不能硬编码工具 schema，否则工具说明会分散在多个地方。
- 注册表提供统一入口：注册、查找、列出、生成 schema。

设计原则：

- 工具实现与 Agent 主循环解耦。
- 工具启用状态与工具对象本身解耦。
- 工具发现状态与工具注册状态解耦。

---

### 3.8 初始化内部状态

源码：

```python
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()
```

三个内部集合分别表示三类状态。

字段说明：

| 字段 | 类型 | 含义 |
|---|---|---|
| `_tools` | `dict[str, Tool]` | 已注册工具，key 是工具名 |
| `_disabled` | `set[str]` | 已禁用工具名 |
| `_discovered` | `set[str]` | 延迟工具中已经被发现的工具名 |

为什么 `_tools` 用字典：

- Agent 根据模型返回的 `tool_name` 查找工具。
- 字典按名称查找平均 O(1)。
- `tool.name` 天然适合作为 key。

为什么禁用状态单独放 `_disabled`：

- 禁用工具不等于删除工具。
- 之后可以通过 `enable()` 恢复。
- 保留工具对象也能保留其 schema、描述和内部状态。

为什么发现状态单独放 `_discovered`：

- 延迟工具已经注册，但默认不暴露给模型。
- 被搜索或点名后，才进入可暴露状态。
- 这能降低每轮请求的工具 schema token 成本。

---

### 3.9 注册工具

源码：

```python
    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
```

这段代码把工具对象放入 `_tools` 字典。

为什么按 `tool.name` 注册：

- 模型工具调用返回的是工具名字符串。
- Agent 可以用这个字符串直接找到工具对象。
- 工具名是工具协议层的稳定标识。

注意点：

- 如果重复注册同名工具，后注册的会覆盖先注册的。
- 当前代码没有显式报错，这是简单实现。
- 更严格的设计可以在重复注册时报错，避免误覆盖。

对后续模块的影响：

- `create_default_registry()` 会连续调用 `register()` 注册默认工具。
- MCP 或插件工具也可以通过同一个接口加入注册表。

---

### 3.10 获取工具

源码：

```python
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)
```

按名称查找工具。

为什么返回 `Tool | None`：

- 模型可能返回未知工具名。
- 工具可能尚未注册。
- 返回 `None` 让调用方决定如何处理错误。

为什么不在这里检查是否禁用：

- `get()` 表达的是“是否存在这个工具”。
- 是否允许使用由 `is_enabled()` 表达。
- 存在性和可用性分开，语义更清晰。

对后续模块的影响：

- `agent.py` 可以先 `get()` 找工具，再结合启用状态或其他策略执行。

---

### 3.11 判断工具是否启用

源码：

```python
    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled
```

判断工具是否既已注册又未禁用。

为什么同时检查两个条件：

- 未注册工具不能被视为启用。
- 已注册但在 `_disabled` 中的工具也不能使用。

对后续模块的影响：

- Agent 做工具执行前可以检查可用性。
- UI 或命令系统可以展示当前可用工具列表。

---

### 3.12 启用工具

源码：

```python
    def enable(self, name: str) -> None:
        self._disabled.discard(name)
```

从禁用集合中移除工具名。

为什么用 `discard()` 而不是 `remove()`：

- `discard()` 在元素不存在时不会报错。
- 启用一个本来就没禁用的工具应当是无害操作。

设计意义：

- 这个方法是幂等的。
- 重复调用不会造成异常。

---

### 3.13 禁用工具

源码：

```python
    def disable(self, name: str) -> None:
        if name in self._tools:
            self._disabled.add(name)
```

禁用已注册工具。

为什么先检查 `name in self._tools`：

- 避免把不存在的工具名放进 `_disabled`。
- 让 `_disabled` 始终只记录真实工具的状态。

为什么不是直接删除工具：

- 禁用是临时状态。
- 工具对象仍可保留。
- 之后 `enable()` 可以恢复。

对后续模块的影响：

- 权限、技能、模式切换等功能可以通过禁用工具限制模型能力。

---

### 3.14 启用全部工具

源码：

```python
    def enable_all(self) -> None:
        self._disabled.clear()
```

清空禁用集合。

为什么这样实现：

- 禁用状态集中存储在 `_disabled`。
- 清空集合即可恢复所有已注册工具。

对后续模块的影响：

- 会话重置、模式恢复或命令处理可以快速恢复默认工具能力。

---

### 3.15 标记延迟工具已发现

源码：

```python
    def mark_discovered(self, name: str) -> None:
        self._discovered.add(name)
```

把工具名加入已发现集合。

为什么需要“发现”状态：

- 有些工具数量多或 schema 很大，不适合每轮都发给模型。
- 模型先通过搜索工具找到它。
- 找到后再把完整 schema 暴露出来。

当前方法不检查工具是否存在：

- 简化实现。
- 但也意味着可以把未知名字加入 `_discovered`。
- 更严格的实现可以只允许已注册工具被标记。

---

### 3.16 判断工具是否已发现

源码：

```python
    def is_discovered(self, name: str) -> bool:
        return name in self._discovered
```

判断工具是否处于已发现状态。

用途：

- ToolSearch 类工具可以搜索后标记。
- `get_all_schemas()` 可以根据发现状态决定是否暴露延迟工具。

设计理由：

- 将发现状态封装在注册表中，外部模块不需要直接访问 `_discovered`。

---

### 3.17 获取尚未发现的延迟工具名

源码：

```python
    def get_deferred_tool_names(self) -> list[str]:
        return [
            name
            for name, tool in self._tools.items()
            if getattr(tool, "should_defer", False)
            and name not in self._discovered
            and name not in self._disabled
        ]
```

这个方法返回“应该延迟暴露、尚未发现、没有禁用”的工具名。

逐项条件说明：

| 条件 | 含义 |
|---|---|
| `getattr(tool, "should_defer", False)` | 工具声明自己要延迟暴露 |
| `name not in self._discovered` | 尚未被搜索或点名发现 |
| `name not in self._disabled` | 当前没有被禁用 |

为什么用 `getattr()`：

- 不是所有工具都一定显式声明 `should_defer`。
- 基类虽然提供默认字段，但 `getattr()` 让代码对外部工具更宽容。

为什么返回名字而不是工具对象：

- 调用方可能只需要提示模型“还有哪些工具可搜索”。
- 名字比完整 schema 更轻量。

对后续模块的影响：

- `ToolSearchTool` 可以基于这些名字或注册表状态实现工具发现。

---

### 3.18 搜索延迟工具

源码：

```python
    def search_deferred(
        self, query: str, max_results: int, protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
```

这个方法根据查询词搜索延迟工具，并返回匹配工具的 schema。

为什么只搜索延迟工具：

- 非延迟工具默认已经暴露给模型，不需要搜索。
- 搜索的目的正是按需激活那些暂不暴露的工具。

参数含义：

| 参数 | 含义 |
|---|---|
| `query` | 搜索关键词 |
| `max_results` | 最多返回几个工具 |
| `protocol` | 输出哪种模型协议格式 |

---

### 3.19 搜索词归一化

源码：

```python
        query_lower = query.lower()
        scored: list[tuple[int, str, Tool]] = []
```

`query_lower` 用于大小写无关匹配。

`scored` 存储搜索结果三元组：

```text
(分数, 工具名, 工具对象)
```

为什么需要打分：

- 多个工具可能同时匹配查询。
- 需要按相关性排序。
- 名称命中通常比描述命中更重要。

---

### 3.20 遍历候选工具

源码：

```python
        for name, tool in self._tools.items():
            if not getattr(tool, "should_defer", False):
                continue
            if name in self._disabled:
                continue
```

这段代码过滤候选工具。

过滤逻辑：

- 不是延迟工具：跳过。
- 已禁用工具：跳过。

为什么禁用工具不能搜索出来：

- 禁用代表当前模式下不允许使用。
- 即使匹配查询，也不应把 schema 暴露给模型。

---

### 3.21 相关性打分

源码：

```python
            score = 0
            name_lower = name.lower()
            desc_lower = (tool.description or "").lower()
            if query_lower in name_lower:
                score += 10
            if query_lower in desc_lower:
                score += 5
            for word in query_lower.split():
                if word in name_lower:
                    score += 3
                if word in desc_lower:
                    score += 1
            if score > 0:
                scored.append((score, name, tool))
```

打分规则：

| 匹配方式 | 加分 | 设计理由 |
|---|---:|---|
| 完整查询命中工具名 | 10 | 工具名最能代表意图 |
| 完整查询命中描述 | 5 | 描述命中也有较高相关性 |
| 单词命中工具名 | 3 | 部分名称匹配有价值 |
| 单词命中描述 | 1 | 描述中单词命中权重最低 |

为什么这是启发式搜索：

- 实现简单，无需额外依赖。
- 适合工具数量中小规模的场景。
- 对 MCP 大量工具也能提供基本检索能力。

局限性：

- 不支持 BM25、向量搜索或模糊拼写。
- 中文分词和同义词效果有限。
- 如果工具数量非常多，后续可以替换为更专业的检索算法。

---

### 3.22 排序并转换 schema

源码：

```python
        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for _, _name, tool in scored[:max_results]:
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results
```

逐段说明：

- `scored.sort(...)`：按分数从高到低排序。
- `scored[:max_results]`：只取前 N 个，控制返回规模。
- `tool.get_schema()`：先拿到内部统一 schema。
- `protocol` 判断：根据模型协议转换输出格式。

为什么区分协议：

- Anthropic 工具 schema 使用 `input_schema`。
- OpenAI function/tool 格式通常使用 `parameters`，并带有 `"type": "function"`。
- 注册表统一处理格式差异，Client 层就不需要重复写转换逻辑。

对后续模块的影响：

- `client.py` 可以根据 provider protocol 获取对应工具定义。
- 同一套工具可以适配不同模型提供商。

---

### 3.23 按名称查找延迟工具

源码：

```python
    def find_deferred_by_names(
        self, names: list[str], protocol: str = "anthropic"
    ) -> list[dict[str, Any]]:
```

这个方法不是按关键词搜索，而是按明确工具名返回延迟工具 schema。

适用场景：

- 模型或用户已经知道工具名。
- 需要直接激活指定工具。
- 避免搜索打分带来的不确定性。

---

### 3.24 名称查找逻辑

源码：

```python
        results: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            if not getattr(tool, "should_defer", False):
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                results.append(base)
        return results
```

逐段说明：

- 初始化 `results` 收集返回 schema。
- 遍历调用方提供的工具名。
- 不存在的工具直接跳过。
- 非延迟工具直接跳过。
- 对符合条件的工具生成 schema。
- 按协议转换 schema 格式。

为什么跳过不存在工具而不是报错：

- 工具名可能来自模型输出或用户输入。
- 宽容跳过能避免整个流程因一个错误名称中断。
- 调用方可以根据返回列表为空判断没有有效结果。

设计上的小问题：

- 这里没有过滤 `_disabled`。
- 与 `search_deferred()` 相比，禁用工具如果被点名仍可能返回 schema。
- 如果禁用语义应当严格一致，后续可以补一个 `if name in self._disabled: continue`。

---

### 3.25 列出工具对象

源码：

```python
    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())
```

返回所有已注册工具对象。

为什么返回 list：

- 避免调用方直接操作内部 `_tools.values()` 视图。
- 调用方拿到的是快照式列表。

为什么不默认过滤禁用工具：

- 方法名是 `list_tools`，语义是“列出已注册工具”。
- 启用状态是另一个维度。
- 如果需要只列可用工具，可以额外调用 `is_enabled()`。

对后续模块的影响：

- 命令系统、调试输出或 UI 可以用它展示工具清单。

---

### 3.26 获取全部可暴露 schema

源码：

```python
    def get_all_schemas(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            if getattr(tool, "should_defer", False) and name not in self._discovered:
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                schemas.append({
                    "type": "function",
                    "name": base["name"],
                    "description": base["description"],
                    "parameters": base["input_schema"],
                })
            else:
                schemas.append(base)
        return schemas
```

这个方法返回当前应该发给模型的工具 schema。

过滤规则：

| 规则 | 原因 |
|---|---|
| 禁用工具不返回 | 当前不可用 |
| 未发现的延迟工具不返回 | 控制 prompt 体积 |
| 普通工具直接返回 | 默认可用 |
| 已发现延迟工具返回 | 搜索后允许使用 |

为什么这个方法很关键：

- 每一轮模型请求前，Client 都需要知道可用工具。
- schema 数量直接影响 prompt 成本。
- 这里集中处理工具可见性，避免 Client 和 Agent 分散判断。

协议转换逻辑与前面一致：

- Anthropic 使用内部 `input_schema` 格式。
- OpenAI / OpenAI-compatible 使用 function 风格格式。

---

### 3.27 创建默认注册表

源码：

```python
def create_default_registry(file_cache: FileCache | None = None, file_history: Any = None) -> ToolRegistry:
```

这是项目创建默认工具集合的工厂函数。

参数说明：

| 参数 | 含义 |
|---|---|
| `file_cache` | 文件内容缓存，用于减少重复读文件 |
| `file_history` | 文件编辑历史记录器，用于记录写入/编辑行为 |

为什么用工厂函数：

- 调用方不需要知道要注册哪些默认工具。
- 默认工具集合集中管理。
- 后续新增默认工具时，只改这里即可。

---

### 3.28 函数内部导入具体工具

源码：

```python
    from mewcode.tools.bash import Bash
    from mewcode.tools.edit_file import EditFile
    from mewcode.tools.file_state_cache import FileStateCache
    from mewcode.tools.glob import Glob
    from mewcode.tools.grep import Grep
    from mewcode.tools.read_file import ReadFile
    from mewcode.tools.write_file import WriteFile
```

这些导入放在函数内部，而不是模块顶部。

为什么这样设计：

- 避免导入 `mewcode.tools` 时立刻加载所有具体工具。
- 降低循环导入风险。
- 让 `ToolRegistry` 类本身保持轻量。

每个工具的角色：

| 工具 | 类别 | 作用 |
|---|---|---|
| `ReadFile` | read | 读取文件并带行号返回 |
| `WriteFile` | write | 写入完整文件内容 |
| `EditFile` | write | 精确替换文件中的唯一字符串 |
| `Bash` | command | 执行 shell 命令 |
| `Glob` | read | 按 glob 模式查找文件 |
| `Grep` | read | 按正则搜索文件内容 |
| `FileStateCache` | 辅助对象 | 记录读文件状态，防止未读先改 |

---

### 3.29 创建文件状态缓存

源码：

```python
    file_state_cache = FileStateCache()
```

`FileStateCache` 记录文件是否被读过，以及读取时的修改时间。

为什么需要这个对象：

- `WriteFile` 和 `EditFile` 的描述都要求：修改已有文件前必须先读。
- 这个规则不能只写在 prompt 里，还需要代码层强制。
- 文件读过之后，如果外部又改了文件，也应该阻止基于旧内容继续编辑。

工作机制：

- `ReadFile` 成功后调用 `record()`。
- `WriteFile` / `EditFile` 执行前调用 `check()`。
- 写入成功后调用 `update()` 刷新缓存。

设计意义：

- 减少模型误覆盖用户未查看内容的风险。
- 避免基于过期文件内容做替换。
- 更接近 Claude Code 类工具的“读后改”安全标准。

---

### 3.30 注册默认工具

源码：

```python
    registry = ToolRegistry()
    registry.register(ReadFile(file_cache=file_cache, file_state_cache=file_state_cache))
    registry.register(WriteFile(file_cache=file_cache, file_history=file_history, file_state_cache=file_state_cache))
    registry.register(EditFile(file_cache=file_cache, file_history=file_history, file_state_cache=file_state_cache))
    registry.register(Bash())
    registry.register(Glob())
    registry.register(Grep())
    return registry
```

这段代码创建注册表并注册六个默认工具。

逐行说明：

- `registry = ToolRegistry()`：创建空注册表。
- 注册 `ReadFile`：传入文件缓存和文件状态缓存。
- 注册 `WriteFile`：传入文件缓存、编辑历史、文件状态缓存。
- 注册 `EditFile`：传入文件缓存、编辑历史、文件状态缓存。
- 注册 `Bash`：命令执行工具，无额外依赖。
- 注册 `Glob`：文件名搜索工具。
- 注册 `Grep`：文件内容搜索工具。
- `return registry`：返回完整默认工具集合。

为什么 `ReadFile`、`WriteFile`、`EditFile` 共享同一个 `FileStateCache`：

- 读、写、编辑必须围绕同一份文件状态判断。
- 如果每个工具各自创建缓存，`WriteFile` 无法知道 `ReadFile` 是否读过文件。
- 共享缓存把“读后改”的安全链路串起来。

为什么默认注册这些工具：

- 代码智能体最基本能力是读文件、找文件、搜内容、编辑文件、运行命令。
- 六个工具正好覆盖最小可用闭环：

```text
Glob/Grep 找目标
  -> ReadFile 理解上下文
  -> EditFile/WriteFile 修改
  -> Bash 验证测试或运行命令
```

---

## 4. 第二模块设计总结

`mewcode/tools/__init__.py` 的核心是 `ToolRegistry`。

它解决了三个问题：

1. 工具如何被集中注册和查找。
2. 工具如何根据模式启用、禁用或延迟暴露。
3. 同一套工具如何适配 Anthropic 与 OpenAI 风格 schema。

这个模块看起来像基础设施，但它决定了 Agent 的能力边界。模型能看到哪些工具、能调用哪些工具、能否按需发现更多工具，都由这里控制。

---

## 5. 第二模块审计观察

本模块整体结构清晰，但有几个后续可改进点：

| 观察 | 影响 | 建议 |
|---|---|---|
| `register()` 重名覆盖无提示 | 可能误覆盖同名工具 | 重复注册时抛错或记录日志 |
| `find_deferred_by_names()` 未过滤 `_disabled` | 禁用工具被点名时仍可能返回 schema | 与 `search_deferred()` 一样加入禁用过滤 |
| `file_history: Any` 类型较宽 | 接口契约不明显 | 用 `Protocol` 声明 `track_edit()` |
| OpenAI schema 转换逻辑重复 | 后续维护成本增加 | 抽出 `_format_schema_for_protocol()` 私有方法 |
| 搜索打分较简单 | 工具很多时相关性有限 | 后续可替换为 BM25 或更强检索 |

这些不是阻断性问题，但属于后续工程化优化方向。

---

## 6. 下一步讲解计划

下一篇建议进入 `mewcode/client.py`。

选择理由：

- `ToolRegistry` 已经解释了工具 schema 如何产生。
- `client.py` 会解释这些 schema 如何被发送给不同模型提供商。
- 它也是把外部模型流式协议转换为内部 `StreamEvent` 的关键模块。

---

## 7. 变更留档

### 2026-07-09

- 新增文档：`docs/codebase-walkthrough.md`。
- 本次覆盖：讲解顺序设计、第一模块 `mewcode/tools/base.py` 的逐段代码说明。
- 修改理由：用户要求从头开始按模块细致讲解，并形成 Markdown 文档。
- 留档策略：后续每次继续讲解或修改文档，都在本节追加日期、修改范围、修改理由。

### 2026-07-09（二）

- 更新文档：`docs/codebase-walkthrough.md`。
- 本次覆盖：第二模块 `mewcode/tools/__init__.py` 的逐段代码说明。
- 补充内容：解释 `ToolRegistry` 的注册、启用禁用、延迟发现、schema 适配和默认工具集合构建逻辑。
- 修改理由：用户要求继续从第二个模块开始讲解，并对每段代码说明选择理由。
- 审计记录：补充了重复注册、禁用过滤、宽类型、schema 转换重复和搜索打分等后续优化方向。
