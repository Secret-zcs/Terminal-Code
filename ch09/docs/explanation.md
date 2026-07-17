# ch09：记忆系统 — `memory/`

> 文件：`auto_memory.py`(242行)、`recall.py`(358行)、`instructions.py`(73行)、`session.py`(572行)

---

## 一、instructions.py — 项目规则书

```python
def load_instructions(project_root: str) -> str:
    paths = [
        root / "MEWCODE.md",              # ① 项目根
        root / ".mewcode" / "MEWCODE.md",  # ② .mewcode内
        home / ".mewcode" / "MEWCODE.md",  # ③ 全局 ~/.mewcode/
    ]
    for path in paths:
        if path.exists():
            content = path.read_text()
            processed = process_includes(content, path.parent, root)
            sections.append(processed)
    return "\n---\n".join(sections)
```

支持 `@include docs/style.md` 引用子文件，最多嵌套 5 层。安全检查：`relative_to(project_root)` 防路径穿越 `@include ../../../etc/passwd`。

---

## 二、auto_memory.py — AI 自动记笔记

**触发**：agent.py 每 5 轮调用 `memory_manager.extract()`，后台异步执行（`asyncio.ensure_future`），不阻塞主循环。

**提取流程**：

```python
async def extract(self, client, conversation, protocol):
    # ① 增量：只取上次提取之后的新消息
    recent = conversation.history[self._last_extraction_msg_count:]

    # ② 拼 prompt：当前 memories.md + 最近对话
    prompt = f"{MEMORY_EXTRACTION_PROMPT}\n## 当前memories.md\n{current}\n## 最近对话\n{dialogue}"

    # ③ 调 LLM 输出完整 memories.md（非追加，LLM 可自行去重/合并）
    collected = await client.stream(extract_conv)

    # ④ 解析 LLM 输出，按 ### 标题拆四个分类
    self._write_memories(collected)
```

**四个分类**：用户偏好、纠正反馈（存 `~/.mewcode/memories.md`）→ 项目知识、参考资料（存 `<project>/.mewcode/memories.md`）。

**为什么 LLM 输出完整文件而非追加条目？** LLM 可自行去重、合并、删旧——"用户偏好 tab 缩进"已经在就不用再加一遍。

---

## 三、recall.py — LLM 驱动的记忆搜索

**选择流程**：

```python
async def find_relevant_memories(query, user_mem_dir, project_mem_dir, ...):
    # ① 扫描两个目录下所有 .md 文件（只读前30行拿frontmatter）
    all_headers = scan_memory_files(user_mem_dir, "user")
    all_headers += scan_memory_files(project_mem_dir, "project")

    # ② 过滤已展示的（不重复提醒）
    candidates = [m for m in all_headers if m.file_path not in surfaced]

    # ③ 格式化成目录发给 LLM 选择最多 5 条
    selected = await _select_relevant_memories(query, candidates, selector)

    # ④ 读完整文件内容 → 包装 system-reminder
```

**五层安全防御**：
1. 文件名白名单（`valid_filenames`—LLM 胡编的被丢弃）
2. 静默降级（LLM 调用失败 → 返回空，不动主对话）
3. 超时保护（`asyncio.wait_for(..., timeout=3.0)`）
4. 去重（`already_surfaced` 集合追踪）
5. 时效性警告（>1 天标注"可能过时"）

**为什么用 LLM 而非向量搜索？** 零额外依赖。记忆 ≤200，LLM 看文件名+描述足够判断。核心态度：记忆是锦上添花，任何失败静默降级。

---

## 四、session.py — 会话持久化

JSONL 格式存 `.mewcode/sessions/`。恢复时找到最后一个 `COMPACT_BOUNDARY` 记录，只重放其后的内容。`validate_message_chain()` 裁掉不完整的工具调用对。30 天自动清理。
