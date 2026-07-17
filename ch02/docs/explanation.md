# ch02：配置与缓存 — `validator.py` + `config.py` + `cache.py`

> 文件：`mewcode/validator.py` (246行)、`mewcode/config.py` (255行)、`mewcode/cache.py` (38行)
> 依赖：validator → 无内部依赖 | config → validator | cache → 无内部依赖
> 被依赖：几乎所有模块通过 AppConfig 获取配置

---

## 一、validator.py — 配置校验层

### 1.1 为什么不用 Pydantic 做配置校验？

Pydantic 的 `BaseSettings` 是 Python 生态中常见的配置管理方案。但 mewcode 选择手写校验函数。对比：

```python
# Pydantic 方案：YAML 中 protocol 写错
# → "1 validation error for ProviderConfig
#    protocol
#      unexpected value; permitted: 'anthropic'..."

# 手写方案：同一条错误
# → "ConfigError: Provider #2: invalid protocol 'antpropic',
#    must be one of: anthropic, openai, openai-compat"
```

**核心差异**：

| 维度 | Pydantic BaseSettings | 手写校验 |
|------|----------------------|---------|
| 错误信息 | 技术术语（"validation error", "permitted values"） | 日常语言（"第2个provider的protocol写错了"） |
| 定位精度 | 字段级（protocol） | 位置+字段（Provider #2: protocol） |
| 复杂约束 | 需自定义 validator 装饰器 | 任意 Python if |
| 启动性能 | 导入 pydantic-settings + 模型解析 ~50ms | 纯函数调用 ~0ms |

**对于终端用户手写的 YAML 配置文件，错误信息质量远比代码量重要。** 用户不会读源码，只能通过错误信息调试。

### 1.2 合法值集合

```python
VALID_PROTOCOLS = {"anthropic", "openai", "openai-compat"}
```

用 `set` 而非 `list`。后续唯一操作是 `protocol not in VALID_PROTOCOLS`——set 的 `in` 是 O(1) 哈希查找。更重要的是**语义表达**：`set` 说"这是一个集合，只做成员判断，不做索引/排序"。代码即文档。

`"openai"` vs `"openai-compat"` 的区别：
- `"openai"` → OpenAI Responses API (`/v1/responses`)，较新，功能更全
- `"openai-compat"` → OpenAI Chat Completions API (`/v1/chat/completions`)，兼容 vLLM/Ollama/LocalAI 等第三方

```python
VALID_PERMISSION_MODES = {
    "default", "acceptEdits", "plan",
    "bypassPermissions", "custom", "dontAsk",
}
```

值名**直接沿用 Claude Code 官方的模式名**，不发明新名字。兼容已有用户心智模型。ch07 权限系统的六种模式直接对应这些值。

```python
VALID_TEAMMATE_MODES = {"", "in-process"}
```

`""` 是合法值——表示"不启用团队模式"。绝大多数用户不需要多 Agent 协作，空字符串作为默认值。

### 1.3 模型窗口映射表

```python
MODEL_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    ("1m", 1_000_000),       # 最具体，放最前
    ("gpt-4.1", 1_000_000),
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("o1", 200_000),
    ("o3", 200_000),
    ("o4", 200_000),
    ("gpt-3.5", 16_385),
    ("claude", 200_000),      # 最通用，放最后
]
```

**为什么是 `list[tuple]` 而非 `dict`？** 因为匹配是**有序子串匹配**，不是精确 key 查找：

```
模型名 "claude-sonnet-4-1m"
  → 包含 "1m"（第 1 条）→ 窗口 1,000,000 ✓  正确！
  → 也包含 "claude"（第 9 条）→ 但如果用 dict 无法保证 "1m" 先匹配
```

`list-of-pairs` 明确表达"有序检查清单"语义，第一个命中即生效。`"1m"` 放在第一位确保 `claude-sonnet-4-1m` 拿到 1M 而非 200K。

```python
def lookup_model_context_window(model: str) -> int:
    m = model.lower()              # ① 全小写，无视大小写
    for substr, window in MODEL_CONTEXT_WINDOWS:
        if substr in m:            # ② 子串匹配（非精确）
            return window          # ③ 第一个命中即返回
    return 0                       # ④ 哨兵值："没找到"
```

返回 `0` 而非抛异常——`0` 在 config.py 的回退链中表示"本层无结果，继续下一层"。这是**哨兵值模式**。

### 1.4 ConfigError

```python
class ConfigError(Exception):
    pass
```

空的类体。唯一目的：让调用方写 `except ConfigError` 而非 `except Exception`。后者会误吞代码 bug（`NameError`、`AttributeError` 等），导致调试困难。

### 1.5 validate_providers() 的六层递进

**第 1 层：顶层类型检查**

```python
if not isinstance(raw_providers, list) or len(raw_providers) == 0:
    raise ConfigError("At least one provider must be configured")
```

**第 2 层：逐个校验，错误信息包含编号**

```python
for i, entry in enumerate(raw_providers):
    if not isinstance(entry, dict):
        raise ConfigError(f"Provider #{i + 1}: must be a mapping")
```

`{i + 1}` 而非 `{i}`——用户数数从 1 开始。

**第 3 层：必要字段——批量检查，一次全报**

```python
missing = [f for f in ("name", "protocol", "base_url", "model") if f not in entry]
if missing:
    raise ConfigError(f"Provider #{i + 1}: missing fields: {', '.join(missing)}")
```

**面试要点**：为什么用列表推导式批量检查而非逐个 if？

如果用户同时漏了 `name` 和 `model`：
- 逐个 if：报 "missing: name" → 用户补上 → 再跑 → 报 "missing: model" → 再补 → 过。用户修了两次。
- 批量检查：报 "missing: name, model" → 一次全补上 → 过。

两秒写代码，省用户两次重启。

**第 4 层：protocol 合法性**

```python
if protocol not in VALID_PROTOCOLS:
    raise ConfigError(
        f"Provider #{i+1}: invalid protocol '{protocol}', "
        f"must be one of: {', '.join(sorted(VALID_PROTOCOLS))}"
    )
```

`sorted()` 保证错误信息中的合法值列表每次输出顺序一致。

**第 5 层：数值字段的类型检查——Python 特有的陷阱**

```python
context_window = entry.get("context_window", 0)
if not isinstance(context_window, int) or isinstance(context_window, bool) or context_window < 0:
    raise ConfigError(...)
```

**面试要点**：为什么中间有一个 `isinstance(context_window, bool)`？

Python 中 `bool` 是 `int` 的**子类**——`isinstance(True, int)` 返回 `True`。如果 YAML 中写了 `context_window: true`：
- 不加 `bool` 检查：`True` 被当作 `1` 通过，语义完全不对（用户的本意可能是"自动检测"）。
- 加了 `bool` 检查：`isinstance(True, bool)` → True → 整个条件 → True → 报错。

这是 Python 特有的历史遗留问题，面试中能答出来说明真正写过 Python。

**第 6 层：白名单输出**

```python
providers.append({
    "name": entry["name"],
    "protocol": protocol,
    "base_url": entry["base_url"],
    "model": entry["model"],
    "api_key": entry.get("api_key", ""),
    ...
})
```

**重建了一个新 dict，只包含已知字段。** YAML 中多余的字段（如用户手滑写了 `api_keyy`）被静默丢弃。这是**白名单模式**。

### 1.6 validate_mcp_servers() 的互斥约束

```python
has_command = "command" in entry
has_url = "url" in entry
if has_command and has_url:
    raise ConfigError(f"MCP server '{name}': cannot have both 'command' and 'url'")
if not has_command and not has_url:
    raise ConfigError(f"MCP server '{name}': must have either 'command' or 'url'")
```

MCP 协议支持两种传输：stdio（通过 `command` 启动子进程）和 HTTP（通过 `url` 连接远程服务）。一个 server 只能选一种——不能同时指定（歧义），也不能都不指定（没连接方式）。

**面试要点**：这种**互斥约束**在声明式校验框架（JSON Schema、Pydantic）中需要自定义 validator，代码量不比手写 if 少，但可读性更差。不是所有校验都该用框架。

### 1.7 validate_config_structure() — 编排者模式

```python
def validate_config_structure(raw: object) -> dict:
    if not isinstance(raw, dict) or "providers" not in raw:
        raise ConfigError("Config must contain a 'providers' list")
    return {
        "providers": validate_providers(raw["providers"]),
        "permission_mode": validate_permission_mode(raw.get("permission_mode", "default")),
        ...
    }
```

**所有可选字段用 `raw.get(key, default)` 而非 `raw[key]`**——不存在的字段取默认值，不抛 `KeyError`。用户的最小配置只需写 `providers` 一个段，其余全用默认。

返回的 dict 是 config.py 和 validator.py 之间的**合约**——config.py 可以安全地按 key 取值，不需要再次检查类型。

---

## 二、config.py — 配置数据模型与加载

### 2.1 环境变量映射

```python
_ENV_KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-compat": "OPENAI_API_KEY",
}
```

**为什么 anthropic 和 openai 用不同变量名？** 很多人同时有 Anthropic 和 OpenAI 的 API key。共用变量名会导致切 provider 时拿错 key。用不同变量名让两者共存。

**为什么 `openai` 和 `openai-compat` 共用 `OPENAI_API_KEY`？** 兼容模式连接的也是 OpenAI 兼容服务，用 OpenAI 格式的 API key。

### 2.2 ProviderConfig — 核心数据模型

```python
@dataclass
class ProviderConfig:
    name: str                   # "DeepSeek"
    protocol: str               # "anthropic" | "openai" | "openai-compat"
    base_url: str               # "https://api.deepseek.com/anthropic"
    model: str                  # "deepseek-v4-pro"
    api_key: str = ""           # "" = "从环境变量找"
    thinking: bool = False
    context_window: int = 0     # ★ 哨兵值：0 = 未设置
    max_output_tokens: int = 0  # 0 = 使用默认值
    _fetched_context_window: int = field(default=0, repr=False)  # 运行时缓存
```

**`context_window: int = 0` 是整个配置系统最精巧的设计**。`0` 不等于"窗口大小为 0"——它是"用户没有显式设置"的信号。如果默认值是 200000，就无法区分"用户真的设了 200000"和"用户根本没设"。`0` 作为哨兵值让回退链的第一层有明确的"跳过"信号。

**面试要点**：这解决了经典问题——如何区分"未设置"和"设置为默认值"。三种常见方案：
1. **哨兵值**（0, -1）→ 本项目选择，简单高效
2. **`Optional[int]` 默认为 `None`** → 用 `None` 表示未设置，类型安全但下游每次要 `if x is not None`
3. **单独的 `has_set_context_window: bool` 标志** → 冗余，两个字段需保持同步

**`_fetched_context_window`**：下划线前缀 = Python 约定"私有"；`repr=False` = 不在 `print(config)` 时显示。运行时从 API 拉取的缓存值，不持久化。

#### resolve_api_key()：两步查找

```python
def resolve_api_key(self) -> str:
    if self.api_key:                          # ① 配置文件中写了 → 直接用
        return self.api_key
    env_var = _ENV_KEY_MAP.get(self.protocol, "")
    return os.environ.get(env_var, "")        # ② 环境变量 → 兜底
```

配置优先，环境变量兜底。都没找到返回空字符串而非抛异常——让 API 调用时自然报错 "API key missing"，用户能通过错误信息定位问题。

#### get_context_window()：四层回退链（面试重点）

```python
def get_context_window(self) -> int:
    # 第1层：用户显式配置（最高优先级）
    if self.context_window > 0:
        return self.context_window

    # 第2层：API 自动拉取
    if self._fetched_context_window > 0:
        return self._fetched_context_window

    # 第3层：内置映射表子串匹配
    window = lookup_model_context_window(self.model)
    if window > 0:
        return window

    # 第4层：保守默认值
    if "claude" in self.model.lower():
        return DEFAULT_CONTEXT_WINDOW  # 200_000
    return 128_000
```

**每层都是独立的 if-with-return**，不是 if-elif 链。效果等价但更灵活（便于插入日志/埋点）。设计哲学：**越靠近用户的越优先，越通用的越靠后**。

| 层 | 数据源 | 优先级 | 准确度 | 覆盖范围 |
|----|--------|--------|--------|---------|
| 1 | 用户配置 | 最高 | 取决于用户 | 用户配了就准 |
| 2 | API 自动拉取 | 高 | 最高（官方数据） | 仅 anthropic 协议 |
| 3 | 内置映射表 | 中 | 中（子串匹配） | 已知模型 |
| 4 | 保守默认值 | 低 | 低（一刀切） | 所有模型 |

**每层都判 `> 0` 因为 `0` 是统一的哨兵值**——所有层都遵守 0 = 无结果的契约。

#### set_fetched_context_window()：防御守卫

```python
def set_fetched_context_window(self, window: int) -> None:
    if window > 0:                              # ★ 守卫条件
        self._fetched_context_window = window
```

API 调用可能失败（超时、返回异常数据）。如果 API 返回 0 或负数而我们直接存进去，`get_context_window()` 的第二层会命中垃圾值——回退链被污染。**只在拿到合法值时才覆盖，失败就保持 0 让下层接管。**

#### get_max_output_tokens()

```python
def get_max_output_tokens(self) -> int:
    if self.max_output_tokens > 0:
        return self.max_output_tokens
    if self.thinking:
        return 64000       # thinking 模式的思考也占 output
    return 8192
```

thinking 模式给 64000 因为 extended thinking 的思考内容也计入 output tokens——Claude 的 thinking budget 可能占 20K-30K，加上实际回复和工具调用 JSON，64000 是合理的安全上限。

---

### 2.3 default_factory 陷阱

```python
@dataclass
class WorktreeConfig:
    symlink_directories: list[str] = field(
        default_factory=lambda: ["node_modules", ".venv", "vendor"]
    )
```

**面试要点**：为什么用 `default_factory` 而非 `= [...]`？

Python 的默认参数在**函数/类定义时**求值一次。`dataclass` 同理——如果写 `= ["node_modules"]`，所有 `WorktreeConfig` 实例共享同一个 list 对象。A 改了列表，B 也受影响。这是经典的**可变默认值陷阱**。

```python
# 错误：所有实例共享同一个列表
@dataclass
class Bad:
    items: list[str] = []       # ← 所有 Bad() 共享

a = Bad()
b = Bad()
a.items.append("x")
print(b.items)  # ["x"] ← 被污染了！

# 正确：每个实例独立
@dataclass
class Good:
    items: list[str] = field(default_factory=list)  # ← 每次都新建
```

---

### 2.4 三层配置合并

```python
def load_config(path: Path | None = None) -> AppConfig:
    candidates = [
        home / ".mewcode" / "config.yaml",       # ① 全局（跨项目共享 API key）
        cwd / ".mewcode" / "config.yaml",         # ② 项目（特定项目的 model）
        cwd / ".mewcode" / "config.local.yaml",   # ③ 本地覆盖（.gitignore）
    ]
```

合并策略因字段而异：

| 字段 | 策略 | 理由 |
|------|------|------|
| `providers` | **完全覆盖** | 只有一个正确答案（用哪个 LLM） |
| `permission_mode` | **非默认才覆盖** | 全局 permissive，项目 restrictive |
| `mcp_servers` | **按名称合并**（同名覆盖，新名追加） | 全局通用 server + 项目专用 server |
| `hooks` | **追加** | 所有层的 hooks 都执行 |
| `bool 开关` | **OR 语义** | 任一层开了就开 |

**为什么不同字段不同策略？** 领域语义不同。providers 是"用哪个模型"——只有一个答案。mcp_servers 是"用哪些服务"——多多益善。hooks 是"触发时做什么"——都要做。一刀切的合并策略会导致用户写两遍配置才能用。

#### _merge_config() 中的索引优化

```python
by_name = {s.name: i for i, s in enumerate(base.mcp_servers)}  # O(n) 建索引
for s in override.mcp_servers:
    if s.name in by_name:                    # O(1) 查找
        base.mcp_servers[by_name[s.name]] = s
    else:
        base.mcp_servers.append(s)
```

建 `{name: index}` 字典，把嵌套循环从 O(n²) 降为 O(n)。mcp_servers 通常只有个位数，性能差异可忽略——但**代码清晰表达了"我在做按名查找"的意图**。

---

### 2.5 环境变量解析

```python
_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")

def resolve_env_vars(value: str) -> str:
    return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
```

拆解这行代码：
```
输入: "token=${MY_TOKEN}"
正则 \$\{([^}]+)\} 匹配 "${MY_TOKEN}"
  → m.group(0) = "${MY_TOKEN}"  （整个匹配）
  → m.group(1) = "MY_TOKEN"     （捕获组，变量名）

lambda m: os.environ.get("MY_TOKEN", "${MY_TOKEN}")
  → 环境变量中存在 → 返回 "sk-abc123"
  → 不存在 → 返回原样 "${MY_TOKEN}"（不报错，让用户知道拼错了）
```

---

## 三、cache.py — 线程安全文件缓存

```python
class FileCache:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}   # path → content
        self._lock = threading.Lock()

    def get(self, path: str) -> str | None:
        with self._lock:
            return self._store.get(path)

    def put(self, path: str, content: str) -> None:
        with self._lock:
            self._store[path] = content

    def invalidate(self, path: str) -> None:
        with self._lock:
            self._store.pop(path, None)    # key 不存在不报错

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
```

### 为什么用 threading.Lock 而非 asyncio.Lock？

FileCache 被 ReadFile 工具使用。工具执行可能在**线程池**中（某些并发调度场景）。`asyncio.Lock` **不是线程安全的**——多个线程同时获取 asyncio.Lock 会出问题。`threading.Lock` 在任何并发模型下都安全。

### 为什么不用 functools.lru_cache？

`lru_cache` 只能缓存纯函数的参数→返回值映射，无法**精确失效**某条缓存：

```python
# 使用场景：
# 1. ReadFile 读 config.py → 缓存
# 2. EditFile 改 config.py → 必须让缓存失效
# 3. ReadFile 再读 config.py → 必须拿到新内容

# lru_cache 做不到 invalidate("config.py")
# FileCache 的 invalidate() 精确提供了这个能力
```

### 为什么 `pop(path, None)` 而非 `pop(path)`？

`dict.pop(key)` 在 key 不存在时抛 `KeyError`。`dict.pop(key, None)` 返回 `None` 而不报错。缓存失效是**幂等操作**——失效一个不存在的 key 应该是 no-op，而非异常。

### 为什么 get() 也加锁？

多线程中，get 时另一个线程可能在 put 或 clear。不加锁会读到脏数据。FileCache 每秒顶多几十次调用，锁的开销几乎为零。一致性 > 性能。

---

## 四、三个文件的关系图

```
        ┌──────────────┐
        │  config.yaml  │
        └──────┬───────┘
               │ yaml.safe_load()
               ▼
        ┌──────────────┐
        │  validator.py │  ← 校验+清洗（纯函数，零依赖）
        └──────┬───────┘
               │ 干净的 dict（合约）
               ▼
        ┌──────────────┐
        │  config.py    │  ← 建模+加载（ProviderConfig, 四层回退链, 三层合并）
        └──────┬───────┘
               │ AppConfig 实例
               ▼
        系统其他模块

        ┌──────────────┐
        │  cache.py     │  ← 独立模块（线程安全文件缓存）
        └──────────────┘
        被 ReadFile 工具使用
```
