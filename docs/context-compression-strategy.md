# 上下文压缩策略详解

> 日期：2026-07-11
> 代码入口：`mewcode/context/manager.py`
> 相关实验：`docs/compact-strategy-experiment-results.md`
> 目标：解释当前项目的上下文压缩策略、设计理由、执行流程、保留边界和后续演进方向。

---

## 1. 策略总览

本项目的上下文压缩不是单一动作，而是分成三层能力：

```text
Layer 1：工具结果预算控制
  -> 处理单条或多条过大的 tool_result
  -> 目标是防止工具输出撑爆上下文

Layer 2：全对话自动摘要压缩
  -> 当 conversation 接近 context window 阈值时触发
  -> 摘要较早消息，原样保留近期尾部

Semantic Plan：语义压缩计划
  -> 对消息打标签、评分、分类
  -> 当前只生成可审计计划，不直接改变 auto_compact() 行为
```

这三层解决的问题不同：

| 层级 | 解决的问题 | 当前是否接入主流程 |
|---|---|---|
| Layer 1 工具结果预算 | 单条工具输出或累计工具输出过大 | 已接入 |
| Layer 2 自动摘要 | 整个对话历史接近上下文窗口 | 已接入 |
| 语义压缩计划 | 旧但关键的信息可能被时间窗口丢掉 | 已实现计划层，尚未接入 `auto_compact()` |

设计核心是：先用确定性规则降低上下文爆炸风险，再用摘要压缩保留长期任务连续性，最后逐步引入语义计划来保护目标、约束、错误结论和当前状态。

---

## 2. Layer 1：工具结果预算控制

### 2.1 触发对象

Layer 1 只处理工具结果，也就是 `Message.tool_results` 中的 `ToolResultBlock`。

它不压缩普通用户消息，也不压缩普通助手消息。

选择理由：

- 工具结果最容易突然变得很大，例如 `grep`、`cat`、测试日志、构建日志。
- 工具输出通常可重新读取或重跑，丢失原文的成本低于丢失用户约束。
- 先处理工具输出，可以在不影响对话语义的情况下显著降低 token 压力。

---

### 2.2 关键常量

当前常量：

```python
SINGLE_RESULT_CHAR_LIMIT = 50_000
AGGREGATE_CHAR_LIMIT = 200_000
PREVIEW_CHARS = 2_000
KEEP_RECENT_TURNS = 10
OLD_RESULT_SNIP_CHARS = 2_000
SNIPPED_TAG = "<snipped>"
PERSISTED_TAG = "<persisted-output>"
```

含义：

| 常量 | 含义 |
|---|---|
| `SINGLE_RESULT_CHAR_LIMIT` | 单条工具结果超过 50,000 字符时落盘 |
| `AGGREGATE_CHAR_LIMIT` | 多条工具结果累计超过 200,000 字符时按大到小落盘 |
| `PREVIEW_CHARS` | 落盘后在上下文里保留前 2,000 字符预览 |
| `KEEP_RECENT_TURNS` | 旧工具结果裁剪时保留最近 10 轮完整结果 |
| `OLD_RESULT_SNIP_CHARS` | 陈旧工具结果只保留 2,000 字符以内 |
| `SNIPPED_TAG` | 标记被裁剪的旧工具结果 |
| `PERSISTED_TAG` | 标记已经落盘的工具结果 |

这些常量体现了两个取舍：

- 最近工具结果更可能仍在被使用，因此优先保留。
- 超大工具结果即使最近，也不能无限占用上下文，需要改成“路径 + 预览”。

---

### 2.3 核心入口

核心函数：

```python
apply_tool_result_budget(conversation, session_dir, state)
```

它接收当前会话、session 目录和 `ContentReplacementState`，返回：

```python
tuple[ConversationManager, list[ContentReplacementRecord]]
```

也就是：

- 一个新的、已经替换过大工具结果的 `ConversationManager`。
- 一组需要持久化的替换记录。

关键设计点：

- 不直接修改原始 `conversation`。
- 替换决策记录在 `state.replacements`。
- 已处理过的工具结果记录在 `state.seen_ids`。
- 每次替换都会生成 `ContentReplacementRecord`，方便 session 恢复。

---

### 2.4 三段式处理流程

Layer 1 分三步处理工具结果。

第一步：复用已有替换决策。

```text
如果 tool_use_id 已经在 state.replacements 中
  -> 直接复用之前的 preview 或 persisted-output
```

理由：

- 同一个工具结果在后续轮次中不应反复做不同替换。
- 保持字节级稳定有利于缓存命中，也方便恢复。

第二步：单条结果超限落盘。

```text
如果单条 tool_result.content > SINGLE_RESULT_CHAR_LIMIT
  -> 写入 .mewcode/session/tool-results/{tool_use_id}.txt
  -> 上下文中替换为 persisted-output 预览
```

理由：

- 大输出仍可通过文件路径找回。
- 模型保留足够预览，知道结果大概是什么。
- 上下文不再被长日志占满。

第三步：聚合结果超限落盘。

```text
如果多个结果累计超过 AGGREGATE_CHAR_LIMIT
  -> 按长度从大到小替换
  -> 直到总量回到预算以内
```

理由：

- 即使每条结果都不超过单条限制，多条结果叠加仍可能撑爆上下文。
- 优先替换最大的结果，能用最少替换次数回收最多空间。

---

### 2.5 陈旧工具结果裁剪

函数：

```python
_snip_stale_messages(history)
```

作用：

- 统计对话轮次。
- 对最近 `KEEP_RECENT_TURNS` 之外的旧工具结果进行裁剪。
- 已经被 `SNIPPED_TAG` 或 `PERSISTED_TAG` 标记的结果不重复处理。

设计理由：

- 很久以前的工具结果通常只需要保留线索，不需要保留全文。
- 如果后续确实需要完整内容，模型应重新读取文件或重新执行工具。
- 避免旧日志长期滞留上下文。

---

### 2.6 Layer 1 的恢复机制

落盘后的工具结果通过两类信息恢复：

1. 上下文中的 `<persisted-output>` 预览。
2. `replacement_records.jsonl` 中的替换记录。

恢复函数：

```python
load_replacement_records(session_dir)
reconstruct_replacement_state(messages, records, inherited_replacements)
```

设计理由：

- 会话恢复后，系统能知道哪些工具结果已经被替换。
- 分叉、续接或恢复 session 时，不会重新把大结果塞回上下文。
- 替换记录让压缩行为可追踪、可复用。

---

## 3. Layer 2：全对话自动摘要压缩

### 3.1 触发条件

Layer 2 的触发由两个函数控制：

```python
compute_compact_threshold(context_window, manual=False)
should_auto_compact(last_input_tokens, context_window)
```

阈值计算：

```python
effective = context_window - SUMMARY_OUTPUT_RESERVE
margin = MANUAL_COMPACT_SAFETY_MARGIN if manual else AUTO_COMPACT_SAFETY_MARGIN
threshold = effective - margin
```

相关常量：

```python
SUMMARY_OUTPUT_RESERVE = 20_000
AUTO_COMPACT_SAFETY_MARGIN = 13_000
MANUAL_COMPACT_SAFETY_MARGIN = 3_000
```

含义：

- `SUMMARY_OUTPUT_RESERVE` 给摘要输出预留空间。
- 自动压缩使用更大的安全边距，避免真正打满上下文窗口。
- 手动压缩允许更贴近上限，因为用户明确要求压缩。

---

### 3.2 token 估算基础

触发判断使用：

```python
conversation.current_tokens()
```

估算策略：

```text
如果已有真实 API 用量锚点：
  current = baseline_tokens + 仅估算锚点之后新增消息

如果没有锚点：
  current = estimate_tokens(整个 history)
```

这样设计的理由：

- 已经有 API 返回的真实 token 时，应优先信任真实数据。
- 只对新增消息做字符估算，避免把缓存命中 token 重复计算。
- 刚压缩后会清空锚点，直到下一轮 API 响应重新建立基准。

---

### 3.3 尾部原文保留窗口

核心函数：

```python
_compute_keep_start_index(messages)
```

它从尾部向前遍历消息，决定压缩后哪些近期消息原样保留。

关键常量：

```python
KEEP_RECENT_TOKENS = 10_000
MIN_KEEP_MESSAGES = 5
KEEP_MAX_TOKENS = 40_000
```

规则：

```text
从最后一条消息开始向前累计：
  - 至少保留到近期 token 达到 KEEP_RECENT_TOKENS
  - 或至少保留 MIN_KEEP_MESSAGES 条消息
  - 但累计超过 KEEP_MAX_TOKENS 时停止继续向前
```

特殊规则：

- 最后一条消息即使单独超过 `KEEP_MAX_TOKENS` 也会被保留。
- 如果保留边界落在 `tool_result` 上，会向前回退到对应的 `tool_use`，避免拆散工具调用对。

设计理由：

- 最近消息通常包含当前工作现场。
- 保留原文比摘要更可靠，尤其是最近指令、错误、代码片段。
- 工具调用和工具结果必须成对出现，否则模型会看到悬空结果。

---

### 3.4 摘要前缀保护

压缩前会判断：

```python
_prefix_too_small_to_compact(to_summarize)
```

如果待摘要前缀太小，则不压缩。

阈值：

```python
MIN_SUMMARIZE_PREFIX_TOKENS = 2_000
```

设计理由：

- 摘要本身也有固定开销。
- 如果前缀太小，压缩回收的空间可能不够抵消摘要成本。
- 避免出现“压缩后反而更长”的情况。

---

### 3.5 摘要 Prompt

`SUMMARY_PROMPT` 要求模型输出 `<summary>`，并包含 9 个部分：

1. 主要请求和意图。
2. 关键技术概念。
3. 文件和代码段。
4. 错误和修复。
5. 问题解决过程。
6. 所有用户消息。
7. 待办任务。
8. 当前工作。
9. 可能的下一步。

设计理由：

- 普通摘要容易丢掉用户原话、TODO 和错误修复。
- 固定结构能提高恢复质量。
- `<summary>` 标签让 `extract_summary()` 可以稳定抽取正式摘要。

---

### 3.6 auto_compact 执行流程

核心函数：

```python
auto_compact(conversation, client, context_window, session_dir, ...)
```

执行流程：

```text
1. 计算 compact threshold
2. 读取 conversation.current_tokens()
3. 未达到阈值则返回 None
4. 熔断器打开则返回错误提示
5. 计算 keep_start
6. 拆分 to_summarize 和 keep_tail
7. 如果前缀太小则返回 None
8. 构造 summary_conv
9. 调用 client.stream() 生成摘要
10. extract_summary()
11. build_recovery_attachment()
12. build_compact_messages()
13. new_messages = summary_message + keep_tail
14. conversation.replace_history(new_messages)
15. cleanup_tool_results(session_dir)
16. 返回 CompactEvent
```

压缩后的历史结构：

```text
一条 user 摘要消息
  + 恢复附件
  + 最近尾部原文消息
```

---

### 3.7 失败重试与熔断

摘要失败时有两类处理。

如果错误像是 prompt 太长：

```text
按 turn 分组
丢掉最早 20% 左右的组
重试摘要
最多重试 3 次
```

如果是其他错误：

```text
记录 breaker failure
返回 "摘要生成失败"
```

熔断器：

```python
CompactCircuitBreaker(max_failures=3)
```

连续失败 3 次后自动压缩停止，提示用户手动处理。

设计理由：

- 自动压缩不能反复失败并阻塞正常对话。
- prompt 太长时可以逐步缩小摘要输入。
- 其他错误需要停止扩散，让用户或上层逻辑介入。

---

## 4. 压缩后恢复附件

### 4.1 RecoveryState

`RecoveryState` 记录两类会话现场：

- 最近读取过的文件。
- 已激活的技能正文。

相关方法：

```python
record_file_read(path, content)
record_skill_invocation(name, body)
snapshot_files(limit)
snapshot_skills()
```

设计理由：

- 压缩会清空大部分原始对话。
- 如果模型刚读过关键文件，摘要不一定能完整保存代码细节。
- 把最近文件快照和技能 SOP 附加回摘要消息，可以减少压缩后断片。

---

### 4.2 build_recovery_attachment

函数：

```python
build_recovery_attachment(state, tool_schemas)
```

输出最多四类内容：

1. 最近读过的文件。
2. 已激活的技能。
3. 当前可用工具。
4. 提示模型必要时重新读取原文。

关键预算：

```python
RECOVERY_FILE_LIMIT = 5
RECOVERY_TOKENS_PER_FILE = 5_000
RECOVERY_SKILLS_BUDGET = 25_000
RECOVERY_TOKENS_PER_SKILL = 5_000
```

设计理由：

- 恢复附件不能无限增长，否则会抵消压缩收益。
- 文件快照和技能正文有明确 token 上限。
- 工具 schema 只保留名称和首行描述，提醒模型仍可调用哪些工具。

---

## 5. CompactBoundary 与 session 持久化

`auto_compact()` 返回：

```python
CompactEvent(
    before_tokens=before_tokens,
    boundary=CompactBoundary(summary=summary, keep=list(keep_tail)),
)
```

`CompactBoundary` 包含：

| 字段 | 含义 |
|---|---|
| `summary` | 被摘要前缀的结构化摘要 |
| `keep` | 原样保留的近期尾部消息 |

设计理由：

- `auto_compact()` 不直接写 session 文件，保持纯编排逻辑。
- session 层可以把 `summary + keep` 持久化为 `compact_boundary`。
- resume 时可以从最近一次 boundary 重建压缩后的会话状态。

---

## 6. 语义压缩计划

### 6.1 当前定位

语义压缩计划是 Phase 1 能力。

它当前已经实现：

```python
SemanticMessageMeta
SemanticCompactPlan
semantic_tag_message()
build_semantic_compact_plan()
```

但尚未接入 `auto_compact()` 的最终重建流程。

当前边界：

- 可以做实验和审计。
- 可以输出每条消息的标签、分数、分类。
- 不改变实际自动压缩结果。

---

### 6.2 标签体系

当前标签：

| 标签 | 含义 |
|---|---|
| `user_goal` | 用户提出的目标或任务 |
| `constraint` | 用户约束、禁止事项、输出格式要求 |
| `current_work` | 最近工作现场 |
| `code_fact` | 文件路径、代码事实、代码块 |
| `decision` | 设计选择或理由 |
| `error` | 报错、失败结果、异常信息 |
| `todo` | 下一步、待办、计划 |
| `tool_noise` | 大型工具结果或低价值工具输出 |
| `chat` | 普通助手文本 |

标签由规则启发式产生，不调用 LLM。

选择理由：

- 可解释。
- 可测试。
- 无网络和模型成本。
- 不会因为 LLM 标注波动导致压缩计划不稳定。

---

### 6.3 评分规则

基础分：

```python
importance = 20
```

加减分：

| 标签 | 分数变化 |
|---|---:|
| `user_goal` | +35 |
| `constraint` | +35 |
| `todo` | +25 |
| `current_work` | +20 |
| `code_fact` | +15 |
| `decision` | +15 |
| `error` | +15 |
| `chat` | +5 |
| `tool_noise` | -35 |
| 空内容 | -10 |

最终分数限制在 0 到 100。

设计理由：

- 用户目标、约束和 TODO 最影响任务能否继续执行。
- 文件事实、决策和错误对恢复有价值，但通常可以结构化保留。
- 大型工具噪音需要降权，避免“最近但无价值”的输出污染上下文。

---

### 6.4 分类规则

`build_semantic_compact_plan()` 把消息分为四类：

```text
keep_verbatim      原文保留
structure_extract  结构化抽取
summarize          进入摘要
drop               丢弃或极简记录
```

规则：

```python
if "constraint" in tags or "todo" in tags or importance >= 80:
    keep_verbatim
elif {"code_fact", "decision", "error"} & tags and importance >= 60:
    structure_extract
elif "tool_noise" in tags and importance < 60:
    drop
else:
    summarize
```

设计理由：

- 约束和 TODO 不适合完全依赖摘要，优先原文保留。
- 代码事实、设计决策和错误可以结构化抽取。
- 大型工具噪音优先丢弃。
- 其余普通聊天交给摘要模型压缩。

---

### 6.5 must_keep_facts

语义计划还会生成：

```python
must_keep_facts: list[str]
```

当前包括：

- 约束事实，例如 `constraint@0: constraint, user_goal`。
- TODO 事实，例如 `todo@4: chat, current_work, todo`。
- 文件引用，例如 `file@2: mewcode/context/manager.py`。

设计理由：

- 这些事实后续可以作为压缩校验项。
- 如果摘要或重建结果丢失这些内容，可以自动补回 recovery attachment。
- 这为 Phase 2 / Phase 3 的质量验证提供基础。

---

## 7. 当前策略的实验结果

已完成一次可复现实验：

```bash
PYTHONPATH=. python3 scripts/compact_strategy_experiment.py
```

数据集：

- 3 个合成长会话场景。
- 23 条消息。
- 10 个关键恢复项。

结果：

| 指标 | 旧尾部窗口策略 | 新语义压缩计划 |
|---|---:|---:|
| 关键恢复项命中 | 0/10 | 9/10 |
| 关键恢复项召回率 | 0% | 90% |
| 保留噪音消息数 | 1 | 0 |

实验说明：

- 旧策略容易保留最近噪音，丢失早期用户约束和关键文件事实。
- 语义计划能把旧但关键的信息提升出来。
- 当前仍有一个缺口：`error` 标签权重偏低，导致“git status 失败”这类错误结论进入 `summarize`，没有进入 `structure_extract`。

详细结果见：

```text
docs/compact-strategy-experiment-results.md
```

---

## 8. 为什么不直接替换 auto_compact

当前没有把语义计划直接接入 `auto_compact()`，原因是风险控制。

`auto_compact()` 是主流程能力，一旦接入错误，会影响所有长会话。

当前做法是：

```text
先实现语义计划
  -> 用测试和实验验证标签、评分、分类
  -> 找出缺口
  -> 再逐步接入摘要 prompt 和重建流程
```

这样可以避免一次性改动导致：

- 用户约束被误删。
- 工具调用对被拆散。
- 压缩后上下文不可恢复。
- 摘要输出格式不稳定。

---

## 9. 策略优点

当前策略的主要优点：

1. **分层清晰。** 工具结果压缩和全对话摘要分开处理。
2. **风险可控。** 语义策略先作为计划层存在，不直接破坏主路径。
3. **可恢复。** 大工具结果落盘，压缩边界持久化，恢复附件补充文件和技能现场。
4. **可解释。** 语义计划能说明每条消息为什么保留、摘要或丢弃。
5. **可测试。** 标签、评分、保留窗口和实验指标都能通过单元测试或脚本验证。

---

## 10. 当前限制

当前仍有以下限制：

| 限制 | 影响 |
|---|---|
| `auto_compact()` 仍按时间尾部窗口重建 | 旧但关键的信息仍可能只进入摘要 |
| `error` 权重偏低 | 某些错误结论会进入 `summarize` 而不是结构化保留 |
| 语义标签是启发式 | 对同义表达、隐含约束、复杂中文表达识别有限 |
| `must_keep_facts` 尚未用于校验 | 目前只是计划输出，没有强制补回 |
| 没有压缩 trace 文件 | 调试时需要手动运行实验或查看计划对象 |

---

## 11. 后续演进方向

### 11.1 提升错误类信息优先级

建议把 `error` 单独纳入结构化保留：

```python
elif "error" in meta.tags:
    structure_extract.append(meta.index)
```

理由：

- 错误结论通常对后续执行有直接影响。
- 结构化保留错误信息成本低。
- 实验已经证明当前错误召回存在缺口。

---

### 11.2 将语义计划接入 auto_compact

目标压缩结构：

```text
结构化任务状态
  + 语义摘要
  + 必须保留的用户约束
  + 近期尾部原文
  + 恢复附件
```

接入方式：

1. 在 `auto_compact()` 中生成 `SemanticCompactPlan`。
2. `keep_verbatim` 直接进入压缩后历史。
3. `structure_extract` 渲染成结构化状态块。
4. `summarize` 交给摘要模型。
5. `drop` 不进入摘要上下文。
6. 用 `must_keep_facts` 校验压缩后内容。

---

### 11.3 增加压缩 Trace

建议输出：

```json
{
  "before_tokens": 180000,
  "after_tokens": 42000,
  "messages": [
    {
      "index": 0,
      "tags": ["constraint", "user_goal"],
      "importance": 90,
      "decision": "keep_verbatim",
      "reason": "constraint must survive compaction"
    }
  ]
}
```

理由：

- 压缩是有损操作，必须能解释损失发生在哪里。
- 后续调参需要真实 trace。
- 用户要求留档时，trace 可以作为压缩行为审计材料。

---

### 11.4 将实验脚本转为回归测试

建议把核心断言加入 `tests/test_context.py`：

```python
assert semantic_required_recall > old_required_recall
assert semantic_noise_messages_kept <= old_noise_messages_kept
assert semantic_required_recall >= 0.9
```

理由：

- 防止后续修改评分规则时退化。
- 把“任务恢复质量”纳入测试体系。
- 保持压缩策略演进可验证。

---

## 12. 变更留档

### 2026-07-11

- 新增文档：`docs/context-compression-strategy.md`。
- 本次覆盖：详细解释当前上下文压缩策略，包括 Layer 1 工具结果预算、Layer 2 自动摘要压缩、恢复附件、`CompactBoundary`、语义压缩计划、实验结论和后续演进方向。
- 修改理由：用户要求详细解释上下文压缩策略，并形成 Markdown 文档。
- 关键结论：当前主流程仍是“工具结果预算 + 时间尾部摘要压缩”，语义压缩计划已实现并通过实验验证有效，但尚未接入 `auto_compact()` 重建流程。
