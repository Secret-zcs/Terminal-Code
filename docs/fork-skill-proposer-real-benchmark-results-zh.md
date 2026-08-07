# Fork Skill Proposer 真实模型评测结果

日期：2026-08-07

## 实验配置

- Provider：DeepSeek Anthropic 兼容协议
- Model：`deepseek-v4-flash`
- 数据集：19 个 OASST1 脱敏派生 case
- 门禁：schema -> static policy -> eval -> 3/3 execution eval
- 重试：仅 `invalid-json` 和普通 schema 错误最多重试 1 次
- 隔离：候选只写临时 sandbox，不进入正式 Skill 或审批队列

## 原始完整运行

首次连续运行 19 个 case，结果为 `5/19 approval-ready`。其中 6 个通过 schema/static，5 个通过 eval 和 execution；后半段出现 1 次 timeout 和 11 次 `NetworkError`。因此该数字混合了模型质量和 provider 可用性，不能直接作为候选生成成功率。

## 断点重跑

使用 `case_offset=7`、`max_cases=12`、`inter_case_delay=2` 重跑后 12 个 case：

- schema/static：`12/12`
- eval/execution/approval-ready：`11/12`
- provider failure：`0/12`
- 尝试次数：9 个一次成功，3 个第二次成功
- 重试原因：`invalid-json=3`

按“原始前 7 个 + 稳定重跑后 12 个”合并，有效结果为：

| 状态 | 数量 |
|---|---:|
| approval-ready | 16/19 |
| schema-failed | 1/19 |
| eval-failed | 2/19 |
| baseline passed | 0/19 |

两次命令累计输入 token `2224`、输出 token `27523`，case 耗时合计约 `302.38s`。这是实际实验成本，不是单次无故障运行成本。

## 失败分析

schema 失败样本在两次尝试后仍生成了非法 Skill 名称。两个 eval 失败样本使用“禁止跳过测试/禁止盲目重试”表达安全约束，却被精确子串 `must_not_contain` 判失败；这是 deterministic eval 的潜在语义 false positive，不应通过放宽全局安全 gate 隐藏。

2026-08-07 后续修复增加了显式 `forbidden_match_mode=non_negated`，仅供自然语言 benchmark 区分安全否定表达；生产 eval case 默认仍为严格 `literal`。本页 `16/19` 是修复前的历史结果，没有被追溯改写，采用新模式的结果必须通过重新运行产生。

## 结论与边界

有限结构化重试明显改善了 DeepSeek 输出的可解析性；加入样本间隔后，provider 网络失败从 `11/12` 降为 `0/12`。合并有效通过率为 `84.21%`，但 19 个派生 case 使用相同任务族和关键词，execution runner 也是 deterministic sandbox，因此结果不等于真实代码修复成功率。下一阶段应增加多任务族、隔离真实仓库测试和 baseline/evolved Agent 双跑。
