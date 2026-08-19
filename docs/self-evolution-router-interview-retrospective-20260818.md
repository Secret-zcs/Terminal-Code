# 自进化 Skill 路由机制复盘

日期：2026-08-18

## 一句话结论

这次自进化优化的核心，不是“让模型从一堆 Skill 里自己挑一个”，而是做了一个**经验注入控制器**：在主模型执行前，先用可审计的任务信号判断这次经验该不该用、能不能用、只能用哪一种；如果不确定，默认不注入，避免负迁移。

## 背景问题

最早的自进化方式是把失败经验和成功经验沉淀成一份全局 Skill。这里的 Skill 可以理解成一份给 Agent 的经验手册，例如：

- 先看失败测试。
- 找最小代码路径。
- 不要乱改测试、依赖文件、文档和生成文件。
- 修改后必须跑目标测试和回归测试。

这个思路本身合理，但问题在于它是“全局注入”：不管任务是 Requests、SymPy printing、SymPy matrix 还是 pytest internals，都塞同一份经验手册。

扩样到 10 个 SWE-bench Lite 有效样本后，全局 Skill 的结果是：

- baseline 成功率：50.00%（5/10）
- 注入全局 Skill 后成功率：50.00%（5/10）
- 结论：没有稳定提升，说明全局经验会在部分任务上产生负迁移。

负迁移的具体表现是：有些任务 baseline 本来能修好，但额外 SOP 会让 Agent 多搜、多跑、多犹豫，甚至偏离最短修复路径。

## 关键判断

这不是简单的“Skill 文案不够好”，而是机制设计问题。

单一全局 Skill 同时覆盖多个任务族会过粗：

- Requests streaming bug 需要关注 header/body/streaming 的窄路径。
- SymPy printing bug 需要关注 printer 类和 `_print_*` 方法。
- SymPy matrix bug 需要关注 shape、index、zero-dimension 行为。
- pytest internals bug 需要关注 assertion rewrite 或 collection 语义。

这些任务需要不同经验。如果都塞同一份泛化 SOP，收益就会被稀释，甚至反过来干扰模型。

## 路由机制是什么

路由机制的目标是：先判断任务类型，再决定是否注入对应经验。

当前不是让主模型看 Skill 清单后自己选择，而是在主模型执行前，由程序根据任务信号做确定性判断。主要看这些信息：

- Issue 描述。
- 目标测试命令。
- 目标测试文件路径。
- 允许修改的源码路径。
- 仓库名或 case id 中的项目信号。

例如：

```text
allowed_paths: sympy/printing/ccode.py
expected_tests: sympy/printing/tests/test_ccode.py
issue/test 里出现 ccode、latex、printer 等信号
```

就判断为：

```text
family = sympy_printing
action = inject
skill = sympy_printing_repair
```

然后只注入这一类任务相关的窄经验，例如：

- 先看目标 printer 类。
- 看附近 `_print_*` 方法。
- 以失败测试中的 expected string 为规格。
- 不要改全局 simplification 或 assumptions 逻辑。

如果信号不够明确，就输出：

```text
action = skip
```

也就是不注入任何自进化经验，按普通 Agent 跑。

## 它和“让模型自己挑 Skill”有什么区别

表面上它们都在解决 Skill 选择问题，但实现和风险控制不一样。

普通的模型自选 Skill 通常是：

```text
用户问题 + Skill 清单 -> 主模型自己判断用哪个 Skill
```

当前实现是：

```text
Issue / 测试路径 / allowed paths -> 程序路由器 -> inject 或 skip -> 主模型只看到最终结果
```

区别主要有四点：

1. **选择发生在主模型执行前**：主模型不是看一堆 Skill 名称自己猜，而是只接收最终注入的一份经验，或者完全不注入。
2. **不暴露完整 Skill 清单**：减少 prompt 成本，也避免模型被 Skill 名称误导。
3. **允许 skip**：不是必须挑一个最相关 Skill；不确定或无历史收益时，宁可不用。
4. **有晋升门禁**：匹配到任务族不代表一定注入，只有历史评测证明有效的 family Skill 才能自动注入。

所以更准确的名字不是“模型选 Skill”，而是“Skill 注入控制器”或“经验使用门禁”。

## 为什么路由后变好

全局 Skill 的问题是所有任务都吃同一套药。路由以后变成：

```text
SymPy printing -> 注入 printing 修复经验
SymPy functions -> 注入 functions 修复经验
Requests streaming -> 注入 requests 修复经验
未知任务 / 未验证任务族 -> 不注入
```

它提升的来源有两个：

- 对确实匹配的任务，给出更具体的修复方向。
- 对不确定或历史收益不稳定的任务，不强行注入经验，减少负迁移。

## 评测流程

评测采用真实仓库 A/B 双跑：

1. 从 SWE-bench Lite 取真实 Issue 和对应仓库 commit。
2. 按 `base_commit` 构造真实仓库环境。
3. 应用官方 `test_patch`，生成目标测试。
4. 用 `FAIL_TO_PASS` 作为目标测试，用 `PASS_TO_PASS` 作为回归测试。
5. 每个 case 复制两份仓库：baseline 和 evolved。
6. baseline 不注入 Skill；evolved 注入全局 Skill、路由 overlay 或门控策略。
7. 两边使用同样的 Issue、测试命令、允许修改范围和超时限制。
8. 结束后统一比较测试结果、回归结果、越权修改、调用成本和耗时。

有效样本筛选标准：

- 初始目标测试必须失败，否则不是有效修复任务。
- 初始回归测试必须通过，否则环境不干净。
- 只允许修改 `allowed_paths`。
- 不能修改 `forbidden_paths`。

当前主要复测样本是 10 个 SWE-bench Lite 有效 case：

```text
swebench_psf__requests-3362
swebench_sympy__sympy-11400
swebench_sympy__sympy-11897
swebench_sympy__sympy-12171
swebench_sympy__sympy-12419
swebench_sympy__sympy-12481
swebench_sympy__sympy-13031
swebench_sympy__sympy-13043
swebench_sympy__sympy-13146
swebench_sympy__sympy-13437
```

## 指标定义

- **任务成功率**：目标测试通过、回归测试不破坏、没有越权修改，三者同时满足才算成功。
- **成功率提升**：evolved 成功率减 baseline 成功率，例如 50.00% -> 70.00% 就是 +20.00pp。
- **回归通过率**：对应 SWE-bench 的 PASS_TO_PASS，衡量旧功能有没有被破坏。
- **越权修改率**：检查是否修改了 allowed paths 之外或 forbidden paths 中的文件。
- **模型调用次数**：近似衡量 API 调用成本。
- **工具调用次数**：衡量执行复杂度，例如读文件、搜索、编辑、跑命令。
- **Token 消耗**：包括 input tokens、output tokens 和 total tokens。
- **平均耗时**：每个 case 的平均执行时间。
- **Canary Gate**：成功率要提升，回归通过率 100%，越权修改率 0%，且没有 provider、runner、timeout 错误。
- **Runtime Efficiency Gate**：在 Canary Gate 通过基础上，还要求模型调用、工具调用、token 和耗时不增加。

## 结果

### 1. 全局 Skill

- baseline：50.00%（5/10）
- 全局 Skill：50.00%（5/10）
- 结论：没有稳定收益。

### 2. 简单压缩版 Skill

把 Skill 改短，强调少搜索、早补丁、只跑目标测试和回归测试。

- baseline：50.00%（5/10）
- optimized Skill：40.00%（4/10）
- model calls：-54
- tool calls：-43
- tokens：-38,462
- 平均耗时：-41.45s

结论：成本下降了，但成功率掉了。说明简单缩短流程不是正确方向。

### 3. 路由 Skill Overlay

修正后的策略是：保留通用安全规则，再叠加 family-specific overlay。

- baseline：50.00%（5/10）
- routed overlay：70.00%（7/10）
- 成功率提升：+20.00pp / +2 cases
- 回归通过率：100.00%
- 越权修改率：0.00%
- model calls：-3
- tool calls：+18
- total tokens：+2,406
- 平均耗时：-9.29s
- Canary Gate：通过
- Runtime Efficiency Gate：失败，原因是工具调用和总 token 增加。

这是真实双跑结果，适合写进项目经历，但要说明成本门禁还没有完全通过。

### 4. Skip Short-circuit 与 Promoted-family Gate

进一步优化了两个策略：

- `skip short-circuit`：router 判断不注入 Skill 时，不再跑一遍无 Skill evolved，而是直接沿用 baseline policy。
- `promoted-family gate`：只有历史评测证明有效的任务族 Skill 才会注入。

在同一批真实 run 上做离线门控重算：

- baseline：50.00%（5/10）
- promoted policy：80.00%（8/10）
- 成功率提升：+30.00pp / +3 cases
- 回归通过率：100.00%
- 越权修改率：0.00%
- provider runs：从 10/20 降到 4/20
- model calls：-4
- total tokens：-12,351
- 平均耗时：-24.47s
- tool calls：+4

注意：这个 80.00% 是基于已有真实结果的 policy recompute，不是新一轮真实 provider 双跑。因此简历主口径应使用 routed overlay 的真实 70.00%，80.00% 可以作为门控策略的离线分析结果。

## 自进化效果体现在哪里

这套机制的价值不是“写一段提示词就让模型稳定变强”，而是形成了一个闭环：

```text
任务执行 -> 失败/成功经验沉淀 -> 候选 Skill -> 真实仓库 A/B 评测 -> 路由和门禁 -> 决定是否注入
```

具体效果：

- 识别出全局 Skill 无条件注入没有稳定收益。
- 通过路由把经验限定到更合适的任务族。
- 通过 skip 避免不确定任务被经验干扰。
- 通过 promoted-family gate 防止未验证 Skill 自动生效。
- 在 10 个真实 SWE-bench Lite case 上，真实双跑成功率从 50.00% 提升到 70.00%，回归通过率 100%，越权修改率 0%。

## 本轮继续优化：自动晋升名单

之前 promoted-family gate 需要手动写入允许注入的任务族，例如 `sympy_printing`、`sympy_functions`。这仍然有人工判断成分。

现在进一步把它改成可从历史 benchmark JSON 自动推导：系统会按 route family 聚合每类任务的表现，并输出一张 Route Family Impact 表。每个 family 会统计：

- 样本数和实际注入次数。
- baseline / evolved 成功率。
- 成功 case 增量。
- 是否出现 baseline 成功但 evolved 失败的回退。
- 是否破坏回归测试或产生越权修改。
- 模型调用、工具调用、token、耗时变化。

默认晋升条件是：

- 该 family 至少有指定数量的 injected case。
- task success 有正向提升。
- 没有任务回退。
- evolved 侧回归测试不失败。
- evolved 侧没有越权修改。
- 没有 provider、runner 或 timeout 错误。

如果开启严格效率条件，还会要求模型调用、工具调用、token 和耗时都不增加。

在已有 10-case routed overlay 真实结果上，自动推导出的可晋升 family 是：

```text
sympy_functions
sympy_printing
```

对应统计：

- `sympy_printing`：3 个 case，baseline 成功率 33.33%，evolved 成功率 100.00%，成功增量 +2，total tokens -16,103。
- `sympy_functions`：1 个 case，baseline 成功率 0.00%，evolved 成功率 100.00%，成功增量 +1。

这说明 promoted-family gate 不再依赖“人工感觉哪个 Skill 有用”，而是可以由历史 A/B 评测结果自动生成。后续运行时可以通过 `--task-router-promoted-families-from-json` 直接读取历史结果生成注入白名单。

2026-08-19 继续补齐了正式 policy recompute 能力：

```bash
PYTHONPATH=. python3 scripts/run_repository_double_run_benchmark.py \
  --from-json .mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-overlay-skill-20260818.json \
  --recompute-task-router-policy \
  --json-output .mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-auto-policy-20260819.json \
  --md-output .mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-auto-policy-20260819.md \
  --summary-output .mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-auto-policy-20260819.summary.txt
```

这条命令不会调用模型，而是读取历史真实双跑结果，自动推导 promoted families，并生成一份标准化的 promoted policy 报告。当前正式 auto-policy 报告结果为：

- 自动晋升 family：`sympy_functions`、`sympy_printing`。
- policy recompute 成功率：50.00% -> 80.00%，+30.00pp / +3 cases。
- 回归通过率：100.00%。
- 越权修改率：0.00%。
- source evolved runs 保留 4 个，其余 6 个 route 短路到 baseline policy。
- model calls -4，total tokens -12,351，平均耗时 -24.47s。
- Runtime Efficiency Gate 仍失败，原因是 tool calls +4。

这份 80.00% 仍然应表述为“基于历史真实双跑结果的离线门控重算”，不能当作新的 provider 双跑结果；真实 provider 双跑主口径仍是 routed overlay 的 70.00%。

## 面试表达

可以这样讲：

> 我做的自进化不是微调模型，而是把 Agent 在真实任务中的成功经验和失败教训沉淀成可复用 Skill。最开始我采用全局 Skill，所有仓库修复任务都注入同一份 SOP，但在 SWE-bench Lite 扩样后发现 10 个 case 上成功率没有提升，说明无条件注入会产生负迁移。
>
> 后来我把它改成 Skill 注入控制器：在主模型执行前，根据 Issue 描述、目标测试路径和允许修改路径判断任务族，比如 SymPy printing、SymPy matrix、Requests streaming。只有高置信任务才注入对应的 family-specific Skill；未知任务或者历史评测没通过的任务族直接 skip，不注入经验。
>
> 评测上我用 SWE-bench Lite 的真实 Issue 做 baseline/evolved 隔离双跑，指标包括任务成功率、回归通过率、越权修改率、模型调用、工具调用、token 和耗时。结果是 10 个有效 case 上，routed overlay 把任务成功率从 50.00% 提升到 70.00%，回归通过率 100%，越权修改率 0%。同时我也把 Runtime Efficiency Gate 纳入门禁，避免只提高成功率但明显增加成本的 Skill 被自动晋升。

## 简历表述

推荐写法：

> 设计自进化候选 Skill 的真实仓库评测闭环，在 SWE-bench Lite 样本上执行 baseline/evolved 隔离双跑，量化任务成功率、Pass-to-Pass 回归、越权修改、模型调用、Token 与耗时；发现全局 Skill 扩样后收益不稳定后，引入任务路由、skip 短路和 promoted-family gate，使 10-case routed 真实复测成功率从 50.00% 提升至 70.00%（+20.00pp），回归通过率 100%、越权修改率 0%。

如果面试官追问“这是不是让模型自己挑 Skill”，回答：

> 不是。普通 Skill selection 是把清单交给模型自己选。我这里是在主模型执行前，用程序根据 Issue、测试路径和 allowed paths 做可审计分类，并且支持 skip 和历史晋升门禁。主模型看不到完整 Skill 清单，只会收到最终被批准注入的一份经验，或者完全不注入。

## 证据文件

真实 routed overlay 结果：

```text
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-overlay-skill-20260818.json
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-overlay-skill-20260818.md
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-overlay-skill-20260818.summary.txt
```

离线门控重算结果：

```text
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-shortcircuit-skill-20260818.json
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-promoted-policy-20260818.json
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-auto-policy-20260819.json
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-auto-policy-20260819.md
.mewcode/evolution/benchmarks/repository-real-swebench-combined10-routed-auto-policy-20260819.summary.txt
```

代码入口：

```text
mewcode/evolution/repository_benchmark.py
scripts/run_repository_double_run_benchmark.py
tests/test_repository_double_run_benchmark.py
```
