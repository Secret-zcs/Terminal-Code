# ch08：TUI 界面层 — `app.py` + `driver.py` + `__main__.py`

> 文件：`mewcode/app.py`(1920行)、`mewcode/driver.py`(41行)、`mewcode/__main__.py`(220行)
> 依赖：ch01~ch07 全部
> 框架：Textual（Python TUI框架）

---

## 一、driver.py — NoAltScreenDriver（41行）

```python
class NoAltScreenDriver(_BaseDriver):
    def start_application_mode(self):
        rows = os.get_terminal_size().lines
        sys.stdout.write("\n" * rows)    # 打印空行推旧内容入 scrollback
        super().start_application_mode()

    def write(self, data):
        # 拦截并删除 alt screen 切换码
        if "\x1b[?1049h" in data:    # 进入备用屏幕
            data = data.replace("\x1b[?1049h", "")
        if "\x1b[?1049l" in data:    # 退出备用屏幕
            data = data.replace("\x1b[?1049l", "")
        if data:
            super().write(data)
```

**设计意图**：正常 TUI 程序退出后内容消失（切到 alt screen 又切回来）。mewcode 模仿 Claude Code——退出后对话内容保留在主终端，用户可以往上翻看完整记录。

`\x1b[?1049h` 和 `\x1b[?1049l` 是 ANSI 转义码（xterm alternate screen）。Driver 是 Textual 框架最底层的适配器，负责"怎么画到终端"。

---

## 二、__main__.py — 入口（220行）

```python
def main():
    # ① 解析命令行参数（-p 非交互模式，--mode 权限模式）
    # ② load_config() + load_hooks()
    # ③ 非交互模式 → _run_prompt()（agent.run_to_completion）
    # ④ 交互模式 → MewCodeApp(..., driver_class=NoAltScreenDriver).run()
```

---

## 三、app.py 核心骨架（1920行）

### 3.1 ChatInput — 自定义输入框

继承 Textual 的 `TextArea`，8 个快捷键绑定。输入历史持久化到 `.mewcode/history`。`@` 文件补全扫描当前目录，`/` 命令补全查 CommandRegistry。

### 3.2 ToolCallBlock — 可折叠工具卡片

```
● Read config.py …          执行中
✓ Read config.py (0.3s)     成功（点击展开看详情）
✗ Bash: rm -rf / (0.1s)     失败（红色）
```

多 ReadFile/Glob/Grep 自动收成折叠组 `● Done (3 tool uses · 1.2s)`。

### 3.3 Spinner — 思考动画

105 个随机动词 + braille 动画 80ms/帧，与 Claude Code Go 版本一致。

### 3.4 _send_message() — 核心消息循环

连接 TUI 和 Agent 的桥梁：

```
① 展开 @ 引用（expand_at_refs）
② 预取相关记忆（3s超时）
③ conversation.add_user_message(text)
④ 启动 spinner
⑤ async for event in agent.run(conversation):
     StreamText → 实时刷新屏幕文字
     ToolUseEvent → 创建工具卡片
     PermissionRequest → 弹出确认框
     ToolResultEvent → 更新卡片状态
     CompactNotification → 显示+持久化
     LoopComplete → 持久化会话
⑥ 收尾：格式化文本，清理 spinner
```

### 3.5 _select_provider() — 组装所有子系统

150+ 行的初始化函数：创建 client → 权限检查器 → 记忆管理器 → 会话管理器 → ToolRegistry → Agent → 技能加载器 → WorktreeManager → 子Agent/TaskManager → TeamManager → MCPManager。所有子系统在 `agent.run()` 开始前全部就位。
