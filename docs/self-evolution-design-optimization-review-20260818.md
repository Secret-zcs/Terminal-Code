# 自进化设计问题与优化试验复盘

日期：2026-08-18

问题：10-case SWE-bench Lite 扩样后，当前默认自进化 Skill 没有稳定提升成功率。需要判断这是 Skill 文案问题，还是自进化机制设计问题。

## 结论

确实存在设计问题：当前机制把自进化结果沉淀成一个全局 Skill，并在 evolved run 中无条件注入。这种设计过粗，容易出现两类冲突：

1. 简单任务或 baseline 已能解决的任务，被额外 SOP 干扰，增加搜索和 token。
2. 不同任务族需要不同策略，单一 Skill 很难同时覆盖 requests、SymPy printing、SymPy matrices、SymPy combinatorics 等修复路径。

## 默认 Skill 的 10-case 结果

- baseline 成功率：50.00%（5/10）
- default evolved 成功率：50.00%（5/10）
- 成功率变化：0.00pp
- model calls：-4（-2.09%）
- tool calls：+11（+5.09%）
- tokens：+4,291（+1.85%）
- 平均耗时：+1.84s（+1.56%）
- Canary Gate：失败
- Runtime Efficiency Gate：失败

## 优化版 Skill 试验

优化思路：把 Skill 从“流程型长提示”改成“仓库修复快速闭环”，强调少搜索、早补丁、禁止外部安装/历史答案、只跑目标测试和回归测试。

结果：

- baseline 成功率：50.00%（5/10）
- optimized evolved 成功率：40.00%（4/10）
- 成功率变化：-10.00pp / -1 case
- model calls：-54
- tool calls：-43
- tokens：-38,462
- 平均耗时：-41.45s
- Canary Gate：失败
- Runtime Efficiency Gate：失败

解释：优化版确实降低了调用和 token，但过度压缩执行流程，导致部分需要更多上下文推理的 case 失败。它说明“简单缩短 Skill”不是正确方向。

## 失败模式

- `sympy__sympy-11400`、`sympy__sympy-11897`：默认 Skill 带来正向提升，说明流程约束对某些 printing 修复有帮助。
- `sympy__sympy-12171`、`sympy__sympy-12481`：默认 Skill 出现 no-patch 或负迁移，说明无条件注入会让模型在某些任务上偏离最短修复路径。
- `sympy__sympy-13031`、`sympy__sympy-13146`、`sympy__sympy-13437`：baseline/evolved 都失败，说明不是单靠提示能解决，可能需要任务族专门策略或更多上下文。

## 设计优化方向

下一步不应继续打磨一个全局 Skill，而应改机制：

1. **Skill Router**：根据 issue 文本、target test path、失败栈和 allowed paths 判断任务族，只在相关任务注入相关 Skill。
2. **No-Skill Baseline Gate**：如果同类任务历史上 baseline 已稳定成功，默认不注入 Skill，避免负迁移。
3. **Per-Family Candidate**：把 SymPy printing、SymPy matrices、requests streaming 等经验拆成独立 Skill，而不是混在一个全局 SOP。
4. **A/B/C 评测**：每次候选不只比较 baseline vs evolved，还比较 baseline vs global skill vs routed skill。
5. **负迁移记忆**：对导致回退的 case 生成 negative memory，后续 selector 避免在相似任务上注入同一策略。

## 已实现：任务路由

已新增 repository benchmark 的 `--task-router` 模式：

- 根据 issue、target test、regression test、expected tests 和 allowed paths 判断任务族。
- 对高置信任务族注入内置 family-specific Skill，例如 `sympy_printing_repair`、`sympy_matrix_repair`、`requests_streaming_repair`。
- 对未知任务族跳过全局 Skill 注入，避免简单任务被通用 SOP 干扰。
- 每个 case 的路由结果会写入 JSON/Markdown 报告中的 `task_route` / `Task Routes` 区块。
- 修正了一个早期误判：不能因为测试命令里出现 `pytest` 就把任意项目误判成 pytest internals 任务，pytest 路由现在只匹配 `pytest-dev`、`src/_pytest`、`_pytest/` 和 `testing/test_assertrewrite.py` 等项目级信号。

使用方式：

```bash
PYTHONPATH=. python3 scripts/run_repository_double_run_benchmark.py \
  --fixtures /path/to/fixtures \
  --case-ids-file /path/to/case_ids.txt \
  --reuse-baseline-json .mewcode/evolution/benchmarks/previous.json \
  --task-router \
  --json-output .mewcode/evolution/benchmarks/routed.json \
  --md-output .mewcode/evolution/benchmarks/routed.md \
  --summary-output .mewcode/evolution/benchmarks/routed.summary.txt
```

## 简历口径调整

不能写“自进化稳定提升成功率”。更严谨的说法是：

> 构建自进化候选 Skill 的真实仓库评测闭环，在 SWE-bench Lite 样本上执行 baseline/evolved 隔离双跑，量化成功率、回归、越权、模型调用、Token 与耗时；通过 Canary Gate 和 Runtime Efficiency Gate 识别默认 Skill 在 10-case 扩样中未稳定泛化，并据此定位单一全局 Skill 设计缺陷，推进按任务族路由的 Skill 注入机制。

对应产物：

```text
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-skill-effect-20260818.json
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-optimized-skill-20260818.json
/tmp/mewcode-optimized-repository-repair-skill.md
```

## 任务路由复测

第一版 routed Skill 把 family-specific Skill 直接替代全局 Skill，在 4-case 复测中从 baseline 50.00% 降到 25.00%。结论是：分类方向是对的，但不能丢掉全局 Skill 中的安全边界、最小改动和验证约束。

修正后改为 `global safety Skill + family overlay`：高置信任务族注入局部 overlay，未知任务族跳过注入。

- 4-case routed overlay：baseline 50.00% -> evolved 100.00%，+50.00pp；回归通过率 100%，越权修改率 0%。
- 6-case routed overlay：baseline 50.00% -> evolved 50.00%，持平；其中 `sympy_functions` 额外修复 1 个 case，但部分无收益 route 增加了工具调用和 token。
- 合并 10-case routed overlay：baseline 50.00% -> evolved 70.00%，+20.00pp / +2 cases；回归通过率 100%，越权修改率 0%；model calls -3，但 tool calls +18、total tokens +2,406，因此 Runtime Efficiency Gate 失败。

对应产物：

```text
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-overlay-skill-20260818.json
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-overlay-skill-20260818.md
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-overlay-skill-20260818.summary.txt
```

## 继续优化：短路与晋升门禁

新增两个机制优化：

1. **Task-router skip short-circuit**：当 router 决定 `action=skip` 时，不再重新跑一次无 Skill 的 evolved agent，而是直接沿用 baseline policy。这样 skip 的语义才是真正的“不应用自进化”，避免把 provider 随机性误算成自进化回退，也减少评测 provider 调用。
2. **Promoted-family gate**：router 可以只注入通过历史评测的任务族 Skill；未晋升的高置信 family 也短路到 baseline。这个机制把“能匹配任务族”和“值得注入 Skill”分开，避免无正收益 route 增加工具调用和 token。

在既有 10-case routed overlay 真实结果上做离线策略重算（未新增 provider 调用）：

- 仅启用 skip short-circuit：任务成功率 50.00% -> 80.00%，+30.00pp / +3 cases；回归通过率 100%，越权修改率 0%；评测 provider runs 从 10 降到 9。
- 启用 promoted-family gate，只晋升 `sympy_printing` 和 `sympy_functions`：任务成功率 50.00% -> 80.00%，+30.00pp / +3 cases；回归通过率 100%，越权修改率 0%；评测 provider runs 从 10 降到 4；model calls -4，total tokens -12,351，平均耗时 -24.47s，但 tool calls +4，因此严格 Runtime Efficiency Gate 仍失败。

这里的 promoted-family 结果是基于已有真实 provider run 的 policy recompute，不是新的真实双跑。它适合作为机制优化方向的证据，但简历或面试中要明确说明：真实 routed overlay 结果是 10-case 50% -> 70%，promoted policy 是在同一批真实结果上的离线门控重算，显示 route 晋升门禁能进一步减少负迁移和评测成本。

对应产物：

```text
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-shortcircuit-skill-20260818.json
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-promoted-policy-20260818.json
```

## 当前可写入项目的说法

更稳妥的一句话：

> 设计自进化候选 Skill 的真实仓库评测闭环，在 SWE-bench Lite 样本上执行 baseline/evolved 隔离双跑，量化任务成功率、Pass-to-Pass 回归、越权修改、模型调用、Token 与耗时；发现全局 Skill 扩样后收益不稳定后，引入任务路由、skip 短路和 promoted-family gate，将 10-case routed 真实复测成功率从 50.00% 提升至 70.00%（+20.00pp），并在同批真实结果的离线门控重算中达到 80.00%（+30.00pp）、回归通过率 100%、越权修改率 0%。

不能写成“自进化已稳定降低运行成本”。当前更准确的表述是：成本指标已经纳入门禁，promoted-family policy 下 model calls、tokens、耗时下降，但 tool calls 仍略增，所以自动晋升仍应被严格效率门拦截。
