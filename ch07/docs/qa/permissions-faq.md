# ch07 答疑：权限系统理解

> 日期：2026-06-19

---

## Q1: 第一关的安全检测是为了快速筛选那些安全的指令能直接执行吗？

**答：对，完全正确。**

第一关的本质是一个**快速的"免检通道"**。如果命令同时满足两个条件就直接放行：

1. **命令在白名单里**（ls, cat, head, git status, git diff...——全是只读操作）
2. **不含危险字符**（`| ; && > $` 等 6 种字符）

```python
# Layer 1: 安全的只读命令（自动放行）
if tool.category == "command" and is_safe_command(content or ""):
    return Decision(effect="allow", reason="Safe read-only command")
```

实际效果：

```
Bash("ls -la")         → 白名单命中 + 无危险字符 → allow ✅
Bash("git status")      → 白名单命中 + 无危险字符 → allow ✅
Bash("git push")        → 不在白名单 → 继续下一关
Bash("cat file | bash") → 有 cat 但含 | → 继续下一关
Bash("rm file")         → rm 不在白名单 → 继续下一关
ReadFile("a.py")        → category 不是 command → 直接跳过此关
```

---

## Q2: 六种模式的区别

同样三条操作，在不同模式下的表现：

```
ReadFile(config.py)  DEFAULT:
  安全(跳过) → 黑名单(跳过) → 沙箱(通过) → 规则(没命中) → 矩阵(allow) → 执行 ✅

Bash("rm -rf /")     DEFAULT:
  安全(不通过) → 黑名单(命中！deny) → 后面不走了 → 拒绝 ❌

EditFile(config.py)  DEFAULT:
  安全(跳过) → 黑名单(跳过) → 沙箱(通过) → 规则(没命中) → 矩阵(ask) → 弹窗 👆
```

**关键细节**：即使 BYPASS 模式，`rm -rf /` 仍然被拦截。因为黑名单（Layer 1b）排在矩阵（Layer 4）前面——黑名单是硬拦截，任何模式都绕不过。

---

## Q3: 权限系统核心流程

```
工具调用进入
    │
    ▼
Layer 0: Plan 模式例外
    │
    ▼
Layer 1: 安全命令白名单 → allow / 黑名单 → deny
    │
    ▼
Layer 2: 路径沙箱 → 在项目内/临时目录内通过，否则 deny
    │
    ▼
Layer 3: 用户自定义规则 → 命中 allow/deny 按规则执行
    │
    ▼
Layer 4: 权限模式矩阵 → 查表决定 allow/deny/ask
    │
    ▼
Layer 5: 弹窗确认 (HITL) → ask 才走到这里
```

**核心设计原则**：便宜的检查放前面，费时的放后面。一个只读操作通常在 Layer 4 就被放行，根本不会弹窗。用户规则（Layer 3）优先级高于模式矩阵（Layer 4）——"你明确说的"优先于"模式默认的"。
