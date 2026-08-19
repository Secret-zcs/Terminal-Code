# 自进化 Skill 真实 4-case 效果复测

日期：2026-08-18

本次复测目标是继续验证：同一批真实仓库修复任务中，**不注入自进化 Skill 的 baseline** 与 **注入当前默认 evolved Skill 的 evolved** 在任务成功率、调用成本和运行耗时上的差异。

## 数据来源与筛选

- 来源：SWE-bench Lite test split。
- 重建方式：按 `base_commit` 下载 GitHub commit tarball，应用 `test_patch`，从 `FAIL_TO_PASS` / `PASS_TO_PASS` 生成目标测试和回归测试命令。
- 预检标准：目标测试初始失败，回归子集初始通过。
- 初始候选：`requests-3362`、`sympy-12171`、`sympy-11400`、`sympy-11897`、`pytest-11143`。
- 有效样本：剔除 `pytest-11143`，因为该 case 在当前 Python 3.12 环境下目标测试初始已通过，不能作为 fail-to-pass 样本。

## 有效测试样例

```text
swebench_psf__requests-3362
swebench_sympy__sympy-11400
swebench_sympy__sympy-11897
swebench_sympy__sympy-12171
```

## 测试方式

- 对照方式：同一模型、同一任务、同一隔离仓库，baseline/evolved 分别运行。
- baseline：不注入候选 Skill。
- evolved：注入当前默认自进化 Skill。
- 最大轮次：20。
- 测试超时：180 秒。
- 运行次数：4 个 case，共 8 次 isolated agent run。

## 结果摘要

- baseline 成功率：50.00%（2/4）
- evolved 成功率：75.00%（3/4）
- 成功率提升：+25.00pp / +1 case
- evolved 回归通过率：100.00%
- evolved 越权修改率：0.00%
- Provider / Runner / Timeout 失败率：0.00%
- LLM model calls：80 -> 74，减少 6 次（-7.50%）
- tool calls：88 -> 86，减少 2 次（-2.27%）
- tokens：106,338 -> 87,991，减少 18,347（-17.25%）
- 平均耗时：179.79s -> 145.91s，减少 33.88s（-18.84%）
- Canary Gate：通过
- Runtime Efficiency Gate：通过

## 逐样本变化

| Case | Baseline | Evolved | 变化 |
|---|---:|---:|---:|
| swebench_psf__requests-3362 | 成功 | 成功 | 0 |
| swebench_sympy__sympy-11400 | 失败 | 成功 | +1 |
| swebench_sympy__sympy-11897 | 失败 | 成功 | +1 |
| swebench_sympy__sympy-12171 | 成功 | 失败 | -1 |

## 结论

这轮 4-case 真实复测比 2026-08-17 的 5-case runtime 复测更强：不仅成功率从 50.00% 提升到 75.00%，而且模型调用、工具调用、总 token 和平均耗时都下降，因此同时通过 Canary Gate 和 Runtime Efficiency Gate。需要注意的是样本数仍然偏小，且有 1 个 SymPy case 出现负迁移；后续应继续扩样，并重点分析 `sympy__sympy-12171` 的 evolved 失败原因。

对应产物：

```text
.mewcode/evolution/benchmarks/repository-real-swebench-valid4-skill-effect-20260818-140004.json
.mewcode/evolution/benchmarks/repository-real-swebench-valid4-skill-effect-20260818-140004.md
.mewcode/evolution/benchmarks/repository-real-swebench-valid4-skill-effect-20260818-140004.summary.txt
```
