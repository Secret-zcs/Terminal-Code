# 语义型上下文压缩方案

> 目标：把当前“按 token 阈值触发、按时间前缀摘要”的压缩策略，演进为“保留可执行任务状态”的语义压缩系统。
> 实施原则：先新增可观测、可测试的语义计划层，再逐步接入 `auto_compact()` 的摘要重建流程，避免一次性改动破坏现有稳定路径。

---

## 1. 背景

当前项目已有两层上下文管理：

- **Layer 1：工具结果预算**。对超大工具结果做落盘、预览和旧结果裁剪。
- **Layer 2：自动摘要压缩**。当上下文接近阈值时，摘要较早消息，保留近期尾部原文。

现有 Layer 2 的核心判断仍然是 token 数和时间位置：

```text
current_tokens >= threshold
  -> 计算尾部保留窗口
  -> 摘要前缀
  -> 重建为 摘要 + 尾部原文
```

这个策略简单可靠，但会遇到一个长期任务中的核心问题：**旧信息不一定不重要，新信息也不一定重要**。

例如：

- 早期用户说的“不要修改文件”“每次修改要留档”可能比最近的大段 `grep` 输出更重要。
- 已解决错误的最终结论比完整失败日志更重要。
- 当前任务的下一步计划比旧探索过程更重要。
- 工具输出里大量重复路径或日志应被降权，而不是因为“最近”就完整保留。

因此需要引入语义型上下文压缩。

---

## 2. 设计目标

语义型上下文压缩的目标不是“把聊天记录缩短”，而是：

```text
把完整历史迁移成更小但可继续执行的任务状态。
```

压缩后必须保留：

- 用户目标。
- 用户约束。
- 当前工作状态。
- 关键代码事实。
- 已做设计决策及理由。
- 失败、错误和修复结论。
- 未完成 TODO。
- 当前相关文件和工具能力。

压缩时应弱化：

- 大量重复工具输出。
- 无命中搜索结果。
- 已被后续结论覆盖的探索路径。
- 长日志中的低价值片段。
- 非任务相关闲聊。

---

## 3. 总体架构

建议把语义压缩拆成五个阶段：

```text
1. 语义标注
2. 重要性评分
3. 压缩计划生成
4. 结构化摘要生成
5. 压缩后校验
```

第一版先实现前 3 步：

```text
conversation.history
  -> SemanticMessageMeta[]
  -> SemanticCompactPlan
```

暂不改变现有 `auto_compact()` 的输出结果，只把语义计划作为内部能力和测试对象沉淀下来。

这样做的理由：

- 低风险，不影响现有压缩稳定性。
- 便于通过测试校验标签和评分规则。
- 后续可以把计划接入摘要 prompt、恢复附件和 trace。

---

## 4. 语义标签

第一版标签采用规则启发式，不额外调用 LLM。

| 标签 | 含义 | 压缩策略 |
|---|---|---|
| `user_goal` | 用户直接提出的目标或任务 | 强保留或结构化保留 |
| `constraint` | 用户约束、禁止事项、输出格式要求 | 强保留 |
| `current_work` | 最近工作现场 | 强保留 |
| `code_fact` | 文件路径、代码事实、配置事实 | 结构化保留 |
| `decision` | 设计选择及理由 | 结构化保留 |
| `error` | 报错、失败结果、异常信息 | 结构化保留 |
| `todo` | 下一步、待办、计划 | 强保留 |
| `tool_noise` | 大量工具输出或低价值工具结果 | 摘要或丢弃 |
| `chat` | 普通对话 | 摘要或低优先级保留 |

为什么不用第一版就做 LLM 标注：

- LLM 标注会增加成本和失败面。
- 当前已有大量结构信息：role、tool_uses、tool_results、is_error、文件路径。
- 规则层可测试、可解释，是更稳妥的第一步。

---

## 5. 重要性评分

每条消息计算一个 0-100 的重要性分数。

建议规则：

```text
用户目标 / 约束 / TODO / 当前工作      高分
代码事实 / 决策 / 错误                中高分
普通助手解释                          中等
大型工具输出                          降权
无内容或重复工具结果                  低分
```

评分不是为了绝对准确，而是为了让压缩计划可解释：

```text
为什么这条消息原文保留？
为什么这条消息只摘要？
为什么这条工具结果可以丢弃？
```

---

## 6. 压缩计划

语义计划分为四类：

| 类别 | 含义 |
|---|---|
| `keep_verbatim` | 原文保留 |
| `structure_extract` | 抽取到结构化状态 |
| `summarize` | 交给摘要模型压缩 |
| `drop` | 丢弃或只保留极简记录 |

第一版计划规则：

- 最近尾部消息默认原文保留。
- `constraint`、`todo`、`current_work` 高优先级原文保留。
- `code_fact`、`decision`、`error` 进入结构化抽取。
- `tool_noise` 且低分进入丢弃候选。
- 其余消息进入摘要候选。

---

## 7. 后续接入 `auto_compact()`

第一阶段完成后，第二阶段可把 `SemanticCompactPlan` 接入现有压缩流程：

```text
auto_compact()
  -> build_semantic_compact_plan()
  -> keep_verbatim 原样保留
  -> structure_extract 渲染为结构化状态
  -> summarize 交给 LLM 摘要
  -> drop 不进入摘要上下文
  -> verify must_keep_facts
  -> replace_history()
```

这样压缩后不再只是：

```text
摘要 + 最近尾部
```

而是：

```text
结构化任务状态 + 语义摘要 + 关键原文 + 最近尾部
```

---

## 8. 校验策略

后续应新增 `CompactVerifier`，至少检查：

- 用户目标是否仍存在。
- 用户约束是否仍存在。
- 当前文件路径是否仍存在。
- 下一步计划是否仍存在。
- 未解决错误是否仍存在。
- 最近工具能力列表是否仍存在。

如果缺失，就把对应内容追加到 recovery attachment。

---

## 9. 实施路线

### Phase 1：语义计划骨架

- 新增 `SemanticMessageMeta`。
- 新增 `SemanticCompactPlan`。
- 新增 `semantic_tag_message()` / `build_semantic_compact_plan()`。
- 新增单元测试。
- 不改变 `auto_compact()` 行为。

### Phase 2：摘要 Prompt 升级

- 将语义计划渲染到摘要 prompt。
- 让模型按“任务状态”而不是普通聊天摘要输出。
- 将 `must_keep_facts` 注入 recovery attachment。

### Phase 3：压缩 Trace

- 输出 `before_tokens`、`after_tokens`、保留/摘要/丢弃数量。
- 记录每条消息的标签、评分和处理原因。

### Phase 4：质量评估

- 构造长任务回放测试。
- 断言压缩后目标、约束、当前文件、TODO 不丢。
- 对比原策略和语义策略的压缩后任务恢复质量。

---

## 10. 变更留档

### 2026-07-11

- 新增本文档：`docs/semantic-context-compression.md`。
- 本次实施范围：形成语义型上下文压缩设计方案，并开始 Phase 1 的代码骨架。
- 设计理由：现有压缩按 token 和时间切分，缺少对用户约束、当前任务状态和关键事实的显式保护。

### 2026-07-11（二）

- 更新代码：`mewcode/context/manager.py`。
- 新增能力：`SemanticMessageMeta`、`SemanticCompactPlan`、`semantic_tag_message()`、`build_semantic_compact_plan()`。
- 更新导出：`mewcode/context/__init__.py` 暴露语义压缩计划 API。
- 更新测试：`tests/test_context.py` 新增语义压缩计划测试，并修正 `build_compact_messages()` 的现有行为断言。
- 验证结果：`PYTHONPATH=. pytest tests/test_context.py -q` 通过，51 个测试全部成功。
- 实施边界：Phase 1 只生成可审计的语义压缩计划，不改变现有 `auto_compact()` 的压缩输出行为。

### 2026-07-11（三）

- 更新测试：`tests/test_context.py` 新增前后效果对比用例。
- 对比口径：旧策略使用 `_compute_keep_start_index()` 的尾部保留窗口，语义策略使用 `build_semantic_compact_plan()`。
- 对比结论：当最后一条消息是超大工具噪音时，旧尾部窗口会保留该噪音并漏掉早期用户约束和当前 TODO；语义计划会把早期约束和 TODO 放入 `keep_verbatim`，并把超大工具结果放入 `drop`。
- 验证结果：`PYTHONPATH=. pytest tests/test_context.py -q` 通过，52 个测试全部成功。
- 实施边界：本次仍只验证前后效果差异，不改变 `auto_compact()` 的实际压缩输出。
