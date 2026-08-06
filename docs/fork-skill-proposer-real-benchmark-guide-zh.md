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

评测器已通过 Fake Client 的流程测试，能够把真实模型候选送入生产同款门禁。当前机器的配置加载结果为 `At least one provider must be configured`，因此尚未生成真实模型分数。Fake Client 结果只证明评测器正确，不作为模型效果结论。

## 解释边界

Execution eval 当前使用项目已有的 deterministic sandbox runner，验证 Candidate Skill 是否覆盖要求并产生 3/3 产物；它仍不等于真实 Agent 在代码仓库中修复 issue。后续更强评测应加入隔离仓库、真实测试命令和 baseline/evolved Agent 双跑。
