# ch07：权限系统 — `permissions/`

> 文件：`modes.py`, `dangerous.py`, `sandbox.py`, `rules.py`, `checker.py`
> 依赖：ch01(ToolCategory)
> 被依赖：agent.py(_execute_tool)

---

## 一、五层安检门（checker.py）

```python
class PermissionChecker:
    def check(self, tool: Tool, arguments: dict) -> Decision:
        content = extract_content(tool.name, arguments)

        # Layer 0: Plan 模式例外
        if self.mode == PermissionMode.PLAN:
            if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
                return Decision(effect="allow")

        # Layer 1: 安全命令白名单
        if tool.category == "command" and is_safe_command(content):
            return Decision(effect="allow")

        # Layer 1b: 危险命令黑名单（← 任何模式都绕不过！）
        if tool.category == "command":
            hit, reason = self.detector.detect(content)
            if hit:
                return Decision(effect="deny", reason=f"危险命令拦截: {reason}")

        # Layer 2: 路径沙箱
        if tool.category in ("read", "write") and content:
            ok, reason = self.sandbox.check(content)
            if not ok:
                return Decision(effect="deny")

        # Layer 3: 用户规则
        rule_result = self.rule_engine.evaluate(tool.name, content)
        if rule_result in ("allow", "deny"):
            return Decision(effect=rule_result)

        # Layer 4: 模式矩阵
        effect = mode_decide(self.mode, tool.category)
        if effect in ("allow", "deny"):
            return Decision(effect=effect)

        # Layer 5: 弹窗问用户 (HITL)
        return Decision(effect="ask")
```

**设计原则**：便宜的检查放前面。安全命令白名单（O(1)纯内存匹配）→ 黑名单（正则扫描）→ 路径沙箱（文件系统）→ 用户规则（读YAML文件）→ 弹窗（等人操作）。

---

## 二、dangerous.py — 命令安全检查

```python
# 白名单：只读命令直接放行
_SAFE_COMMANDS = frozenset({
    "ls", "cat", "head", "git status", "git diff", ...
})

def is_safe_command(command: str) -> bool:
    # 含危险字符 → 不安全
    for ch in ("|", ";", "&&", ">", "$(", "`"):
        if ch in command: return False
    # 命令本身在白名单 → 安全
    for safe in _SAFE_COMMANDS:
        if command == safe or command.startswith(safe + " "):
            return True
    return False

# 黑名单：正则匹配危险模式
_DANGEROUS_PATTERNS = [
    (re.compile(r"rm\s+-[a-z]*rf\s+/"), "递归强制删除根目录"),
    (re.compile(r"mkfs\."),              "格式化磁盘"),
    (re.compile(r"curl\s+.*\|\s*(ba)?sh"), "管道执行远程脚本"),
    ...
]
```

**白名单+黑名单双重过滤**：`ls` 不进黑名单（正则不匹配），`rm -rf /` 不进白名单（`rm` 不在列表）。`npm test` 两个都不命中 → 继续走后续安检。

---

## 三、sandbox.py — 路径防逃逸

```python
class PathSandbox:
    def __init__(self, project_root):
        self._allowed_roots = [Path(project_root).resolve(), Path(tempfile.gettempdir()).resolve()]

    def check(self, path):
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.project_root / p
        real_path = p.resolve(strict=False)   # ★ resolve 展开所有符号链接
        for root in self._allowed_roots:
            real_path.relative_to(root)        # 检查在不在允许根目录下
            return (True, "")
        return (False, f"路径 {path} 超出沙箱范围")
```

**`resolve()` 是关键**：`"~/.ssh/../../../etc/passwd"` 在 resolve 后变成 `"/etc/passwd"` → `relative_to(project_root)` 抛 `ValueError` → 拦截。

---

## 四、modes.py — 六种模式矩阵

```python
_MODE_MATRIX = {
    DEFAULT:       {"read": "allow", "write": "ask",  "command": "ask"},
    ACCEPT_EDITS:  {"read": "allow", "write": "allow","command": "ask"},
    PLAN:          {"read": "allow", "write": "ask",  "command": "ask"},
    BYPASS:        {"read": "allow", "write": "allow","command": "allow"},
    CUSTOM:        {"read": "ask",   "write": "ask",  "command": "ask"},
    DONT_ASK:      {"read": "allow", "write": "allow","command": "allow"},
}
```

**注意**：BYPASS 模式下矩阵返回 allow，但黑名单（Layer 1b）排在矩阵前面——`rm -rf /` 任何模式都拦得住。
