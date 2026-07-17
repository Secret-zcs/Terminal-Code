# ch05：工具实现 — `tools/__init__.py` + 核心工具

> 文件：`tools/__init__.py` (163行，ToolRegistry) + `bash.py`(57行) + `read_file.py`(68行) + `write_file.py`(63行) + `edit_file.py`(81行)
> 依赖：ch01(Tool ABC)、ch02(FileCache)
> 被依赖：agent.py

---

## 一、ToolRegistry — 工具花名册

### 1.1 三个容器

```python
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}       # 花名册：{工具名: 工具对象}
        self._disabled: set[str] = set()         # 黑名单
        self._discovered: set[str] = set()       # 懒加载"已激活"标记
```

### 1.2 懒加载机制

有些工具（MCP 工具、大型工具）的 schema 很大。如果每轮都发给 LLM，浪费 token。`should_defer=True` 的工具只在需要时激活：

```python
def get_all_schemas(self, protocol="anthropic") -> list[dict[str, Any]]:
    for name, tool in self._tools.items():
        if name in self._disabled:
            continue
        if getattr(tool, "should_defer", False) and name not in self._discovered:
            continue   # 懒加载且未发现 → 跳过
        schemas.append(tool.get_schema())
    return schemas
```

**流程**：第1轮只发一句提示 → LLM 需要时调 ToolSearch → `mark_discovered()` → 第2轮开始发完整 schema。

### 1.3 search_deferred() — 搜索激活

```python
def search_deferred(self, query, max_results, protocol):
    query_lower = query.lower()
    scored = []
    for name, tool in self._tools.items():
        if not getattr(tool, "should_defer", False):
            continue
        score = 0
        if query_lower in name.lower():      score += 10
        if query_lower in (desc or "").lower(): score += 5
        for word in query_lower.split():
            if word in name.lower():          score += 3
            if word in (desc or "").lower():  score += 1
        if score > 0:
            scored.append((score, name, tool))
    scored.sort(reverse=True)   # 高分排前面
    return [tool.get_schema() for _, _, tool in scored[:max_results]]
```

一个简单的打分排序——不是向量搜索，就是字符串匹配。够用，零依赖。

### 1.4 create_default_registry()

```python
def create_default_registry(file_cache=None, file_history=None):
    file_state_cache = FileStateCache()   # ★ 三个文件工具共享同一个实例
    registry = ToolRegistry()
    registry.register(ReadFile(file_cache=file_cache, file_state_cache=file_state_cache))
    registry.register(WriteFile(file_cache=file_cache, file_history=file_history, file_state_cache=file_state_cache))
    registry.register(EditFile(file_cache=file_cache, file_history=file_history, file_state_cache=file_state_cache))
    registry.register(Bash())
    registry.register(Glob())
    registry.register(Grep())
    return registry
```

**关键设计**：`file_state_cache` 是一个实例，ReadFile/WriteFile/EditFile 共享。ReadFile 记录的状态（mtime），WriteFile 和 EditFile 能检查到——同一个"读书笔记"。

---

## 二、Bash — 命令执行（57行）

### Params 定义

```python
class Params(BaseModel):
    command: str = Field(description="Shell command to execute")
    timeout: int = Field(default=120, description="Timeout in seconds (max 600)")
```

Pydantic 的 `Field(description=...)` 会成为发给 LLM 的 JSON Schema 中的描述——LLM 靠这个理解参数含义。

### execute() 实现

```python
async def execute(self, params: Params) -> ToolResult:
    timeout = min(params.timeout, MAX_TIMEOUT)  # 最多 600 秒

    proc = await asyncio.create_subprocess_shell(
        params.command, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
```

**`asyncio.wait_for` + `proc.kill()`** 是 Python 异步超时处理的标准模式。不能只 `wait_for` 不 `kill`——进程会变成孤儿进程继续跑。

---

## 三、ReadFile — 读文件（68行）

### 读流程：Cache → Disk → Cache

```python
async def execute(self, params: Params) -> ToolResult:
    # ① 先翻 ch02 的 FileCache
    text = self._cache.get(resolved) if self._cache else None
    if text is None:
        text = path.read_text(encoding="utf-8")   # ② 缓存未命中 → 读磁盘
        if self._cache:
            self._cache.put(resolved, text)        # ③ 写入缓存

    # ④ 记录状态（写保护用）
    if self._state_cache:
        self._state_cache.record(resolved, text, path.stat().st_mtime_ns)

    # ⑤ 截取指定行范围，加行号
    lines = text.splitlines()
    selected = lines[offset : offset + limit]
    numbered = [f"{i + offset + 1}\t{line}" for i, line in enumerate(selected)]
    return ToolResult(output="\n".join(numbered))
```

**行号从 1 开始**：`i + offset + 1` 而非 `i + offset`。LLM 编辑文件时说"修改第 42 行"，这个行号来自 ReadFile 的输出。

### is_concurrency_safe = True

两个 ReadFile 可以同时跑——读操作无副作用。

---

## 四、WriteFile — 写文件（63行）

### 写前检查：读过没？被改过没？

```python
if self._state_cache and path.exists():
    resolved = str(path.resolve())
    ok, err_msg = self._state_cache.check(resolved)
    if not ok:
        return ToolResult(output=err_msg, is_error=True)
```

FileStateCache 的 `check()` 验证：① 这个文件之前被 ReadFile 读过（记录在案）② 文件的 mtime 没变（没被外部修改）。

### 写后清理

```python
path.write_text(params.content, encoding="utf-8")
if self._cache:
    self._cache.invalidate(str(path.resolve()))   # 清缓存：文件变了
if self._state_cache:
    self._state_cache.update(str(path.resolve()))  # 更新状态：新 mtime
```

---

## 五、EditFile — 精准替换（81行）

### 唯一性约束

```python
count = content.count(params.old_string)
if count == 0:
    return ToolResult(output="Error: old_string not found", is_error=True)
if count > 1:
    return ToolResult(output=f"Error: old_string found {count} times, must be unique", is_error=True)

new_content = content.replace(params.old_string, params.new_string, 1)
```

**为什么要求恰好出现一次？** 这是 Claude Code 的设计选择——避免歧义。`old_string` 出现 3 次时 LLM 想改哪一个？可能改错。要求唯一性强制 LLM 提供足够的上下文来唯一标识修改位置。

`replace(..., 1)` 的第三个参数 `1` = 只替换第一次出现。即使前面检查了 `count > 1` 会报错，这里还是设了 `1`——防御性编程。

---

## 六、工具的统一模板

```python
class Params(BaseModel):              # ① Pydantic 参数定义（自动校验）
    file_path: str = Field(description="...")

class XxxTool(Tool):                  # ② 继承 Tool ABC
    name = "XxxTool"                  # ③ 类属性（name/description/params_model/category）
    description = "..."
    params_model = Params
    category = "read"

    async def execute(self, params):  # ④ 唯一需实现的抽象方法
        # params 已被 Pydantic 校验过，类型安全
        return ToolResult(output="...")
```

所有 20 个工具都遵循这个模板。看懂一个，全通。
