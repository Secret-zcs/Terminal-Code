# 自进化 Skill 扩样 10-case 真实复测

日期：2026-08-18

本次复测继续使用 SWE-bench Lite，目标是把 2026-08-18 的 4-case 正向结果扩展到更多真实样例，判断自进化 Skill 的收益是否稳定。

## 数据来源

- 数据集：`princeton-nlp/SWE-bench_Lite` test split。
- Fixture 重建：按 `base_commit` 下载 GitHub commit tarball，应用 `test_patch`，用 `FAIL_TO_PASS` 生成目标测试，用 `PASS_TO_PASS` 前 5 个测试生成回归子集。
- 有效样本条件：目标测试初始失败，回归子集初始通过。
- 运行方式：同一模型、同一任务、同一隔离仓库，baseline 不注入 Skill，evolved 注入当前默认自进化 Skill。

## 样本筛选

- 上一轮有效样本：4 个。
- 本轮新增候选：15 个历史 SWE-bench Lite case。
- 预检通过：11 个。
- 真实双跑新增：按 case id 排序取前 6 个，避免按预期结果挑样本。
- 合并有效样本：10 个，共 20 次 isolated agent run。

## 合并 10-case 结果

- baseline 成功率：50.00%（5/10）
- evolved 成功率：50.00%（5/10）
- 成功率提升：0.00pp / 0 case
- evolved 回归通过率：100.00%
- evolved 越权修改率：0.00%
- Provider / Runner / Timeout 失败率：0.00%
- LLM model calls：191 -> 187，减少 4 次（-2.09%）
- tool calls：216 -> 227，增加 11 次（+5.09%）
- tokens：232,149 -> 236,440，增加 4,291（+1.85%）
- 平均耗时：117.86s -> 119.71s，增加 1.84s（+1.56%）
- Canary Gate：失败，原因是没有正向成功率提升。
- Runtime Efficiency Gate：失败，原因是 Canary Gate 未通过，且工具调用、总 token、平均耗时增加。

## 分批结果对照

| 批次 | 样本数 | Baseline | Evolved | 成功率变化 | Runtime Efficiency Gate |
|---|---:|---:|---:|---:|---|
| valid4 | 4 | 2/4 | 3/4 | +25.00pp | 通过 |
| more-valid6 | 6 | 3/6 | 2/6 | -16.67pp | 失败 |
| combined10 | 10 | 5/10 | 5/10 | 0.00pp | 失败 |

## 结论

扩样后，默认自进化 Skill 的收益没有稳定泛化：4-case 上成功率和成本都改善，但新增 6-case 出现负迁移，合并 10-case 后成功率持平、token 和耗时小幅增加。因此当前更严谨的结论是：

1. 自进化评测闭环本身有效，能用 baseline/evolved 双跑、Canary Gate、Runtime Efficiency Gate 暴露候选 Skill 的收益与负迁移。
2. 当前默认 Skill 不是稳定可晋升版本；它在小样本上有正收益，但在扩样后不能通过 Gate。
3. 简历上应优先写“构建自进化评测与门禁体系”，不要只写“自进化稳定提升效率”。可附带说明：10-case 真实复测中回归通过率 100%、越权修改率 0%，但成功率持平，系统能识别并拦截未稳定泛化的候选。

对应产物：

```text
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-skill-effect-20260818.json
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-skill-effect-20260818.md
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-skill-effect-20260818.summary.txt
.mewcode/evolution/benchmarks/repository-real-swebench-more-valid6-skill-effect-20260818-143329.json
.mewcode/evolution/benchmarks/repository-real-swebench-more-valid6-skill-effect-20260818-143329.md
.mewcode/evolution/benchmarks/repository-real-swebench-more-valid6-skill-effect-20260818-143329.summary.txt
```
