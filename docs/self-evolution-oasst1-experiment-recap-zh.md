# OASST1 自进化前后对比实验

日期：2026-08-06

## 数据与治理

- 数据源：`OpenAssistant/oasst1` validation split，Apache-2.0。
- 固定 revision：`fdf72ae0827c1cda404aff25b6603abec9e3399b`。
- 下载规模：1000 行；本地原始响应 SHA256：`b72d358bb4f9f794ac0d4536027865ee4fbb701d9a4f4bade4b3b3318dbf4726`。
- 原始数据只保留在被 Git 忽略的 `.mewcode/evolution/datasets/oasst1/`。
- 提交的派生集为 `benchmarks/oasst1_derived_cases.jsonl`，不含原始 text、user ID、message ID；每条只保留匿名哈希 case ID、任务族、语言、轮数和代码/后续反馈信号。

## 方法

筛选条件为：真实对话树中至少三轮、包含代码任务信号、且助手回复前存在一次后续用户消息。1000 行中派生出 19 个符合条件的对话树。评测以固定的 SOP 覆盖检查对比：

- Baseline：进化前通用代码处理 SOP。
- Evolved：当前自进化流程所要求的阅读相关文件、记录用户反馈、回归测试和验证报告 SOP。
- 每个派生 case 要求覆盖四个步骤，且不得包含“跳过测试”或“盲目重试”。

## 结果

| 指标 | Baseline | Evolved | 变化 |
|---|---:|---:|---:|
| Case 数 | 19 | 19 | 0 |
| Required-term recall | 0.00% | 100.00% | +100.00pp |
| 通过 case | 0/19 | 19/19 | +19 |

完整机器可读结果见 `docs/self-evolution-oasst1-eval-results.json`，Markdown 明细见 `docs/self-evolution-oasst1-eval-results-zh.md`。复现命令：

```bash
PYTHONPATH=. python3 scripts/download_self_evolution_conversations.py --row-limit 1000
PYTHONPATH=. python3 scripts/run_self_evolution_dataset_eval.py \
  --dataset benchmarks/oasst1_derived_cases.jsonl \
  --json-output docs/self-evolution-oasst1-eval-results.json \
  --md-output docs/self-evolution-oasst1-eval-results-zh.md
```

## 结论与限制

该实验验证的是自进化 Skill SOP 对真实公开多轮对话任务信号的结构性覆盖提升，不能证明 Fork Proposer 在真实模型上的任务完成率提升。候选 Skill 正文在本实验中是固定 SOP，而不是逐条调用真实 LLM 生成；也没有运行用户仓库测试或 SWE-bench patch。下一阶段应在隔离仓库中，对同一批代码任务分别运行 baseline Agent 与 Proposer 生成候选，统计 schema pass、static policy pass、3/3 execution eval、用户审批就绪率、耗时、token 和真实测试通过率。
