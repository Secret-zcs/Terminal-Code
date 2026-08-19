# 自进化 Skill 运行时效果复测

日期：2026-08-17

本次复测目标不是验证候选评测成本，而是回答：**失败经验沉淀成 Skill 后，后续同类任务用 Skill 和不用 Skill 的执行效果是否有差异**。

## 测试方法

- 数据集：SWE-bench Lite 风格真实 Issue fixture，选取 5 个代表性 case。
- 对照方式：同一模型、同一任务、同一隔离仓库，分别运行 baseline 与 evolved。
- baseline：不注入候选 Skill。
- evolved：注入当前自进化沉淀出的候选 Skill。
- 统计指标：任务成功率、目标测试、回归测试、越权修改、Provider/Runner/Timeout 失败、LLM model calls、tool calls、tokens、耗时。

## 测试样例

```text
swebench_psf__requests-3362
swebench_sympy__sympy-12171
swebench_sympy__sympy-11400
swebench_sympy__sympy-11897
swebench_pytest-dev__pytest-11143
```

## 结果摘要

- baseline 成功率：60.00%（3/5）
- evolved 成功率：100.00%（5/5）
- 成功率提升：+40.00pp / +2 cases
- evolved 回归通过率：100.00%
- evolved 越权修改率：0.00%
- Provider/Runner/Timeout 失败率：0.00%
- LLM model calls：90 -> 87，减少 3 次（-3.33%）
- tool calls：104 -> 103，减少 1 次（-0.96%）
- 平均耗时：111.51s -> 102.47s，降低 9.05s（-8.11%）
- tokens：111,704 -> 118,969，增加 7,265（+6.50%）
- Runtime Efficiency Gate：失败，原因是总 token 增加。

## 结论

本次 5-case 复测说明，注入自进化 Skill 后，对同类真实修复任务的**成功率有明显提升**，同时运行时 LLM 调用次数和平均耗时小幅下降；但 token 总量增加，因此不能宣传为“运行时成本降低”。当前候选只通过成功率/安全 Canary Gate，不通过 Runtime Efficiency Gate。下一步需要生成更短、更强约束的效率版 Skill，并在恢复真实 fixture 后复测。

对应产物：

```text
.mewcode/evolution/benchmarks/repository-real-swebench-skill-runtime-cost5-20260817-221854.json
.mewcode/evolution/benchmarks/repository-real-swebench-skill-runtime-cost5-20260817-221854.md
.mewcode/evolution/benchmarks/repository-real-swebench-skill-runtime-cost5-20260817-221854.summary.txt
```
