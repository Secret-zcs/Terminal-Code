# ch06 答疑：Layer1/Layer2 三个关键问题

> 日期：2026-06-19

---

## Q1: 为什么先 Layer2 再 Layer1？

**核心原因**：Layer2 会修改 `conversation.history`，Layer1 依赖它的最新状态。

```
先 Layer2 再 Layer1（正确）:
  ① Layer2: 发现超阈值 → 压缩 → conversation.history 从 50 条变成 6 条
  ② Layer1: 读 6 条 → 快速生成 api_conv → 发给 LLM

先 Layer1 再 Layer2（错误）:
  ① Layer1: 读 50 条 → 花时间做预算 → 生成 api_conv（50 条版本）
  ② Layer2: 压缩 → conversation.history 变成 6 条
  ③ api_conv 是基于旧 50 条生成的 → 废了 → 要重做
```

类比：先收拾房间（Layer2）再扫地（Layer1），而不是先扫地再收拾。

---

## Q2: 167,000 怎么来的？有数据支撑吗？

```python
context_window             200,000  ← 模型上下文窗口上限
SUMMARY_OUTPUT_RESERVE    - 20,000  ← 压缩时 LLM 生成摘要需要的输出空间
AUTO_COMPACT_SAFETY_MARGIN - 13,000  ← 安全余量（来自 Claude Code 源码）
──────────────────────────────────
触发阈值                   167,000
```

**13,000 安全余量的构成**（工程经验值，非精确计算）：

| 消耗项 | 大约 |
|--------|------|
| System prompt | ~3,000 |
| 工具定义列表 | ~2,000 |
| 摘要 prompt | ~1,000 |
| Token 估算误差（字符法 ±5%） | ~5,000 |
| 环境信息、记忆等 | ~2,000 |

来自 Claude Code 原版 `compact.ts`，在生产环境中验证过。

**手动压缩为什么是 177,000？**

```python
MANUAL_COMPACT_SAFETY_MARGIN = 3,000  # 用户主动触发，容忍度更高
→ threshold = 200,000 - 20,000 - 3,000 = 177,000
```

---

## Q3: tool_result 的上一条不是 tool_use 怎么办？

**正常情况不会出现**。Agent 代码保证了两者紧贴：

```python
conversation.add_assistant_message(text, tool_uses, ...)  # 第 N 条
# （中间没有其他 add_xxx 操作）
conversation.add_tool_results_message(tool_results)        # 第 N+1 条
```

**极端边缘情况**（`_align_keep_start_to_tool_pair` 防御的场景）：

1. **程序崩溃**：assistant(tool_use) 写入了 JSONL，但 tool_results 没来得及写 → 会话恢复时 `validate_message_chain()` 会裁掉悬空消息，但如果恢复逻辑有 bug 或文件被手动编辑，可能残留孤立的 tool_result

2. **未来代码改动**：有人在两行 add 之间插入了 `add_system_reminder()` → tool_result 的上一条就不再是 tool_use

**处理方式**：如果找不到配对（前一条不是 tool_use assistant）→ 不做对齐，孤立的 tool_result 原样保留在尾部。宁可保留一条位置奇怪的数据，也不强行把无关消息当成一对塞进来。这是**防御性设计**。
