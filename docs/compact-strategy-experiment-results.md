# 压缩策略前后对比实验结果

> 实验日期：2026-07-11
> 实验脚本：`scripts/compact_strategy_experiment.py`
> 实验目标：构造可复现数据集，对比旧的尾部保留压缩策略与新的语义压缩计划在任务恢复质量上的差异。

---

## 1. 实验背景

当前上下文压缩有两类策略：

1. **旧策略：尾部窗口保留**  
   使用 `_compute_keep_start_index()` 从对话尾部向前累计 token，保留最近消息，前缀交给摘要。

2. **新策略：语义压缩计划**  
   使用 `build_semantic_compact_plan()` 对每条消息打标签、评分，并分配到 `keep_verbatim`、`structure_extract`、`summarize`、`drop` 四类。

本次实验只比较计划层效果，不改变 `auto_compact()` 当前实际行为。这样可以独立验证“语义计划是否比时间尾部窗口更能保留可执行任务状态”。

---

## 2. 数据集构造

实验脚本构造了 3 个合成长会话场景，共 23 条消息、10 个关键恢复项。

| 场景 | 消息数 | 估算 token | 关键恢复项 | 目的 |
|---|---:|---:|---:|---|
| `constraint_buried_by_tail_noise` | 6 | 42,043 | 3 | 验证早期用户约束和当前 TODO 是否会被尾部超大工具噪音挤掉 |
| `file_facts_vs_recent_chatter` | 9 | 15,072 | 3 | 验证早期文件事实是否会被多条近期普通对话挤出尾部窗口 |
| `error_and_decision_recovery` | 8 | 27,040 | 4 | 验证早期错误结论和设计选择是否能被恢复 |

关键恢复项包括：

- 用户明确目标和约束，例如“必须形成 md 文档”“每一次修改都要留档”。
- 当前任务 TODO，例如“下一步计划”。
- 关键文件事实，例如 `mewcode/context/manager.py`、`build_semantic_compact_plan()`。
- 失败结论和修正策略，例如 `git status` 失败后改用 Markdown 留档。

---

## 3. 对比口径

旧策略保留集：

```python
keep_start = _compute_keep_start_index(messages)
old_retained = messages[keep_start:]
```

新策略保留集：

```python
plan = build_semantic_compact_plan(messages)
semantic_retained = plan.keep_verbatim + plan.structure_extract
```

主要指标：

| 指标 | 含义 |
|---|---|
| `required_recall` | 关键恢复项召回率 |
| `required_hits` | 被保留内容中命中的关键恢复项数量 |
| `retained_tokens` | 被原文保留或结构化保留内容的估算 token |
| `noise_messages_kept` | 被保留的超大低价值工具噪音消息数量 |

---

## 4. 运行命令

```bash
PYTHONPATH=. python3 scripts/compact_strategy_experiment.py
```

脚本输出 JSON，包含总体指标、逐场景指标和每条消息的语义标签、评分、处理归类。

---

## 5. 汇总结果

| 指标 | 旧尾部窗口策略 | 新语义压缩计划 | 变化 |
|---|---:|---:|---:|
| 关键恢复项总数 | 10 | 10 | - |
| 命中关键恢复项 | 0 | 9 | +9 |
| 关键恢复项召回率 | 0% | 90% | +90 pct |
| 保留噪音消息数 | 1 | 0 | -1 |

结论：

- 旧策略在本数据集上的关键恢复项召回为 0%，主要原因是它只看“最近”，不理解旧消息中的用户约束、文件事实和错误结论。
- 新语义计划召回 9/10 个关键恢复项，同时丢弃了最后一条超大工具噪音。
- 新策略仍有一个缺口：`error_and_decision_recovery` 场景中的“git status 失败”错误结论被放入 `summarize`，没有进入结构化保留，因此该场景召回率为 75%。

---

## 6. 逐场景结果

### 6.1 `constraint_buried_by_tail_noise`

场景说明：早期用户约束和当前 TODO 被最后一条超大工具噪音挤出旧尾部窗口。

| 指标 | 旧策略 | 新策略 |
|---|---:|---:|
| 保留消息下标 | `[5]` | `[0, 1, 2, 3, 4]` |
| 保留 token | 41,993 | 49 |
| 关键项命中 | 0/3 | 3/3 |
| 关键项召回 | 0% | 100% |
| 噪音消息保留 | 1 | 0 |

分析：

- 旧策略保留了最后一条 41,993 token 的工具噪音，丢失了早期用户约束和 TODO。
- 新策略将用户约束放入 `keep_verbatim`，将工具噪音放入 `drop`。
- 新策略在更少 token 下保留了更高价值的信息。

---

### 6.2 `file_facts_vs_recent_chatter`

场景说明：早期文件事实被多条近期普通对话推远，旧尾部窗口只保留最近聊天。

| 指标 | 旧策略 | 新策略 |
|---|---:|---:|
| 保留消息下标 | `[5, 6, 7, 8]` | `[0, 2, 3, 7]` |
| 保留 token | 12,000 | 3,052 |
| 关键项命中 | 0/3 | 3/3 |
| 关键项召回 | 0% | 100% |
| 噪音消息保留 | 0 | 0 |

分析：

- 旧策略保留了最近 4 条普通进展消息，但没有保留目标文档路径、核心文件路径和关键函数名。
- 新策略识别了 `constraint`、`todo`、`code_fact`，保留了结果文档路径、`mewcode/context/manager.py` 和 `build_semantic_compact_plan()`。
- 这说明语义计划能把“旧但关键”的事实从时间前缀中提升出来。

---

### 6.3 `error_and_decision_recovery`

场景说明：早期错误结论和设计选择对恢复任务有价值，但不一定落在旧策略尾部。

| 指标 | 旧策略 | 新策略 |
|---|---:|---:|
| 保留消息下标 | `[5, 6, 7]` | `[0, 2, 3]` |
| 保留 token | 12,000 | 31 |
| 关键项命中 | 0/4 | 3/4 |
| 关键项召回 | 0% | 75% |
| 噪音消息保留 | 0 | 0 |

分析：

- 旧策略保留了 3 条近期普通消息，丢失了目标、失败结论和设计选择。
- 新策略保留了“不能只写单元测试”“Markdown 变更留档”“scripts/”等关键项。
- 未命中的关键项是“git status 失败”。原因是该消息被标记为 `error`，但当前分数只有 40，低于 `structure_extract` 阈值，因此进入 `summarize`。

---

## 7. 结论

本次实验支持以下结论：

1. **语义计划显著提升任务恢复质量。** 在 10 个关键恢复项中，新策略命中 9 个，旧策略命中 0 个。
2. **时间尾部窗口容易被低价值近期消息污染。** 当尾部是超大工具输出或普通进展消息时，旧策略会保留噪音并丢失旧约束。
3. **语义计划能更有效控制保留 token。** 第一个场景中，新策略用 49 token 保留所有关键项，旧策略用 41,993 token 只保留噪音。
4. **错误类信息评分需要加强。** 当前 `error` 标签如果没有同时命中 `constraint`、`decision` 或 `current_work`，可能被放入摘要候选，导致关键错误结论不被结构化保留。

---

## 8. 建议修改方向

### 8.1 提升错误信息保留优先级

当前规则：

```python
if "error" in tags:
    importance += 15
```

实验暴露的问题：

- 单独错误消息基础分 20 + error 15 + chat 5 = 40。
- 低于 `SEMANTIC_STRUCTURE_SCORE = 60`，因此不会进入 `structure_extract`。

建议：

- 将 `error` 加分从 15 提高到 35；或
- 对 `error` 标签单独进入 `structure_extract`，不要求达到 60 分；或
- 对包含“原因”“修复”“失败后改用”等词的错误消息额外加权。

推荐第一版采用更保守的规则：

```python
elif "error" in meta.tags:
    structure_extract.append(meta.index)
```

理由：错误结论通常比普通聊天更影响后续执行，结构化保留成本低，丢失成本高。

### 8.2 把实验脚本纳入回归测试

当前实验脚本是可复现的手动实验。后续可以将核心断言加入 `tests/test_context.py`：

- `semantic_required_recall > old_required_recall`
- `semantic_noise_messages_kept <= old_noise_messages_kept`
- `semantic_required_recall >= 0.9`

理由：避免后续调整语义标签或评分时无意降低恢复质量。

### 8.3 输出压缩 trace

建议在后续 Phase 3 中记录：

- 每条消息的标签。
- importance 分数。
- 进入 `keep_verbatim` / `structure_extract` / `summarize` / `drop` 的原因。
- 压缩前后 token 变化。

理由：语义压缩是启发式系统，必须可解释、可回放、可调参。

---

## 9. 变更留档

### 2026-07-11

- 新增脚本：`scripts/compact_strategy_experiment.py`。
- 新增文档：`docs/compact-strategy-experiment-results.md`。
- 本次实验：构造 3 个合成长会话场景，对比旧尾部窗口策略与新语义压缩计划。
- 运行命令：`PYTHONPATH=. python3 scripts/compact_strategy_experiment.py`。
- 实验结果：旧策略关键恢复项召回率 0%，新语义计划召回率 90%；旧策略保留 1 条噪音消息，新策略保留 0 条。
- 发现问题：错误类消息“git status 失败”被放入摘要候选，说明 `error` 标签的结构化保留优先级需要提高。
