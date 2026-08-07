# Fork Skill Proposer 真实模型评测指南

日期：2026-08-06

## 目标

`scripts/run_fork_skill_proposer_benchmark.py` 用真实 provider 调用独立 Fork Proposer。每个脱敏 OASST1 case 生成一个 Candidate Skill，并在临时项目中执行：

```text
schema -> static policy -> eval -> 3/3 execution eval -> approval-ready
```

正式项目 Skill、审批队列和 `.mewcode/skills/` 不会被修改。

## 运行

先在 `~/.mewcode/config.yaml` 或项目 `.mewcode/config.local.yaml` 配置 provider 和 API key，然后执行：

```bash
PYTHONPATH=. python3 scripts/run_fork_skill_proposer_benchmark.py \
  --dataset benchmarks/oasst1_derived_cases.jsonl \
  --max-cases 3 \
  --json-output .mewcode/evolution/benchmarks/proposer-real.json \
  --md-output .mewcode/evolution/benchmarks/proposer-real.md
```

建议先跑 1 至 3 个 case，确认费用和模型输出稳定后再扩大到 19 个。结果包含 schema、static policy、eval、execution eval、approval-ready、baseline、token 和每 case 耗时。

## 当前状态

评测器已通过 Fake Client 的流程测试，能够把真实模型候选送入生产同款门禁。当前项目已经可以加载 Anthropic 协议的 `deepseek-v4-flash` provider；真实模型分数仍需执行本指南中的命令后产生。Fake Client 结果只证明评测器正确，不作为模型效果结论。

2026-08-06 单 case 真实调用已到达模型响应阶段。正式 benchmark 多次出现 `schema-failed`，而独立诊断调用曾成功返回完整候选，说明该模型输出格式存在随机性；解析器已增加 JSON 对象提取兼容逻辑，但没有放宽 schema gate。当前仍不能据此宣称真实候选通过。

增加最多 2 次结构化尝试后，单 case 重跑结果为 `approval-ready 1/1`、`attempts=1`、`execution=3/3`。该结果只代表 1 个样本，不能外推到完整数据集。

随后扩大到 3 个样本：schema/static/eval/execution 均为 `3/3`，approval-ready 为 `3/3`；三个 case 的尝试次数分别为 `2、2、1`，累计输入 token `468`、输出 token `4266`。结果文件为 `.mewcode/evolution/benchmarks/proposer-real-3.{json,md}`，仍未写正式 Skill 或审批队列。

完整 19 个样本及断点重跑结果见 `docs/fork-skill-proposer-real-benchmark-results-zh.md`。Runner 支持 `--case-offset` 和 `--inter-case-delay`，用于跳过已完成样本并降低连续调用导致的 provider 连接失败。

自然语言 benchmark 使用 `forbidden_match_mode=non_negated`，允许“禁止跳过测试”这类安全否定表达，但仍拒绝“可以跳过测试”。该模式会写入 execution eval 的 task、input、result 和报告；生产与用户手工 eval case 默认保持 `literal`，不会自动放宽。

## 解释边界

Execution eval 当前使用项目已有的 deterministic sandbox runner，验证 Candidate Skill 是否覆盖要求并产生 3/3 产物；它仍不等于真实 Agent 在代码仓库中修复 issue。后续更强评测应加入隔离仓库、真实测试命令和 baseline/evolved Agent 双跑。
