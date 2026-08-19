# 自进化机制模拟面试与复盘

> 场景：后端 / AI Agent 工程岗二面或项目深挖
> 项目：TermAgent / MewCode 终端 AI 编程助手
> 核心考察：自进化机制是否只是 prompt engineering，是否有真实评测、门禁、安全边界和量化收益

---

## 一、3 分钟项目讲法

**面试官**：你简历里写了“安全自进化闭环”，这个机制具体是什么？

**候选人回答**：

我这里的自进化不是让 Agent 直接改自己并上线，而是把 Agent 行为优化做成一个受控的候选发布流程。

具体来说，系统会把失败反馈、用户纠正和复杂任务经验沉淀成候选 Skill 或 SOP。候选不会直接生效，而是进入一个隔离评测流程：同一个真实仓库 issue 会被复制成 baseline 和 evolved 两份仓库，baseline 不注入候选 Skill，evolved 注入候选 Skill，然后两个 Agent 执行同一个修复任务。

任务里会明确注入目标测试命令、回归测试命令、允许修改路径和禁止修改路径。最后我不只看目标测试是否通过，还会同时检查回归测试、越权修改、provider failure、runner failure、timeout、token、耗时和 patch size。

只有 evolved 相比 baseline 有正向成功率提升，并且回归通过率 100%、越权修改率 0%、provider/runner/timeout failure 都为 0，候选才算通过 canary gate，后面再进入审批或晋升流程。

在 11 个 SWE-bench Lite 真实 GitHub issue 上，这套机制评测到 evolved 成功率从 63.64% 提升到 90.91%，提升 +27.27pp / +3 cases，同时回归通过率 100%、越权修改率 0%。但在 20-case 扩样上没有继续提升，gate 会阻止它晋升，所以它不是无条件自动上线，而是能拦截负迁移。

**回答要点**：

- 先澄清“不是自动自改上线”，而是候选 Skill 发布流程。
- 讲清 baseline/evolved 双跑。
- 讲清 canary gate。
- 给出真实数据：11-case 正向，20-case 拦截。

---

## 二、机制细节追问

### Q1：你说候选 Skill 是怎么来的？

**面试官**：失败反馈、用户纠正怎么变成 Skill？是人工写的吗，还是模型生成？

**候选人回答**：

目前机制里候选 Skill 可以来自几类来源：

第一类是用户显式纠正，比如用户指出“这个任务应该先跑测试再改代码”。这类反馈会被归纳成候选 SOP。

第二类是失败任务的复盘，比如某些 case 失败是因为 Agent 长时间查历史、不落补丁，或者改了测试而不是改源码。这些会被归类到 failure taxonomy，再转成下一轮候选策略。

第三类是 benchmark 里成功 case 的经验蒸馏，比如 printer 类任务要先看 `_print_*` dispatch，matrix 类任务要关注 shape/index invariant。

但候选生成后不会直接进入默认行为，它只会作为 candidate skill 注入到 evolved 侧做评测。换句话说，生成候选和启用候选是两件事，中间隔着评测和审批。

**标准答案关键词**：

- 用户纠正
- 失败归因
- 成功路径蒸馏
- candidate skill
- 不直接启用

---

### Q2：baseline/evolved 双跑怎么保证公平？

**面试官**：两个 Agent 都是 LLM，本身有随机性，你怎么保证对比公平？

**候选人回答**：

我主要从环境和判定两方面控制公平。

环境上，同一个 fixture 会被复制成两份隔离仓库，baseline 和 evolved 的初始代码、测试、issue 描述完全一致。它们使用同一套 target test、regression test、allowed paths、forbidden paths。

判定上，成功条件不是模型自己说“我完成了”，而是 runner 执行测试和 snapshot diff。一个 run 必须同时满足：Agent 正常完成、目标测试通过、expected test 文件存在、没有 forbidden changes、没有 out-of-scope changes。回归测试虽然单独统计，但 gate 要求 evolved 回归通过率 100%。

另外，为了降低评测成本，我后面实现了 baseline reuse。因为 baseline 和候选 Skill 无关，同一批 fixture 迭代候选时可以复用历史 baseline JSON，只重跑 evolved 侧。但 delta、metrics 和 gate 仍然重新计算，不会跳过判定。

**标准答案关键词**：

- 隔离复制仓库
- 同一 target/regression/scope 约束
- 测试与 diff 判定，不相信模型口头结论
- baseline reuse 只省成本，不改判定

---

### Q3：Canary Gate 具体怎么设计？

**面试官**：你说 gate，具体规则是什么？为什么这么设计？

**候选人回答**：

当前 canary gate 是一个偏保守的晋升门槛。它要求：

第一，evolved 的 task success case 数必须比 baseline 多，也就是必须有正向 lift。

第二，evolved regression-free rate 必须是 100%，避免修一个问题引入回归。

第三，evolved out-of-scope case rate 必须是 0%，避免通过改测试、改依赖、改环境 shim 这种方式刷过测试。

第四，provider failure、runner failure、test timeout 都必须是 0%，否则说明评测本身不稳定，不适合晋升。

这个设计的目的不是追求最高分，而是控制自进化风险。自进化最怕小样本偶然收益或负迁移上线，所以 gate 宁愿保守，也不能让坏策略进入默认路径。

**标准答案关键词**：

- 正向 lift
- 回归 100%
- 越权 0%
- provider/runner/timeout 0%
- 保守晋升

---

## 三、测试数据与指标追问

### Q4：你的测试样本是什么？为什么可信？

**面试官**：你用了什么样本？是不是自己造的？

**候选人回答**：

我分两类样本。

第一类是本地 smoke fixture，比如 calculator、slugify，这类是我自己构造的小仓库，只用于验证 runner、snapshot、scope 检查和报告生成，不用于证明模型能力提升。

第二类是真实 SWE-bench Lite case，来自真实 GitHub issue。我筛样本时有预检：target test 初始必须失败，regression test 初始必须通过，test id 必须能稳定映射和执行，allowed/forbidden paths 必须明确。环境不稳定或测试节点不可靠的 case 不计入效果指标。

目前核心结果有两组：11-case constrained canary 和 20-case expanded canary。11-case 用来展示正向效果，20-case 用来验证泛化和 gate 是否能拦截无收益候选。

**标准答案关键词**：

- smoke fixture 只验证框架
- SWE-bench Lite 真实 issue 才作为效果指标
- 初始 target fail / regression pass
- 不稳定样本不计入

---

### Q5：指标都是什么意思？

**面试官**：你这些指标有点多，讲几个最核心的。

**候选人回答**：

我会分三类讲。

第一类是能力指标：

- `baseline_success_rate`：不注入候选 Skill 的成功率。
- `evolved_success_rate`：注入候选 Skill 的成功率。
- `success_rate_lift`：两者差值，用百分点 pp 表示。
- `task_success_lift_count`：多成功了几个真实 case。

第二类是安全和稳定性指标：

- `evolved_regression_free_rate`：evolved 修完后回归测试仍通过的比例。
- `evolved_out_of_scope_case_rate`：有没有改不该改的文件。
- `provider_failure_run_rate`、`runner_failure_run_rate`、`test_timeout_run_rate`：分别衡量模型调用、评测 runner、测试执行是否稳定。

第三类是成本指标：

- token delta、elapsed delta、patch-size delta。
- 后面我还加了 `provider_run_reduction_rate` 和 `full_provider_run_reduction_rate`，衡量 baseline 复用和定向复测省了多少 provider 调用。

**标准答案关键词**：

- 能力：成功率和 case lift
- 安全：回归、scope、failure
- 成本：token、耗时、provider run

---

### Q6：真实效果怎么样？

**面试官**：最后效果到底如何？别只说机制。

**候选人回答**：

最强正向结果是在 11 个 SWE-bench Lite 真实仓库任务上：baseline 成功率 63.64%，evolved 成功率 90.91%，提升 +27.27pp，也就是多成功 3 个 case。同时 evolved 回归通过率 100%，越权修改率 0%，provider/runner/timeout failure 都是 0，所以这轮 canary gate 是 pass。

但是我也做了 20-case 扩样，结果是 baseline 75%，evolved 75%，没有正向 lift，所以 canary gate fail。这个结果我不会包装成成功率提升，它的价值是证明 gate 能拦截无收益候选，避免小样本收益被直接晋升。

后续我又做了 failure taxonomy，发现 20-case 里 outcome 是 both_success 14 个、lift 1 个、regression 1 个、both_failed 4 个；evolved 失败集中在 tests_failed_no_patch 这个类型。这说明当前瓶颈不是 scope 或回归，而是部分失败 case 没有形成有效源码补丁。

**标准答案关键词**：

- 11-case：63.64% -> 90.91%，+27.27pp
- 回归 100%，越权 0%
- 20-case：75% -> 75%，gate fail
- 不包装 20-case，强调 gate 拦截
- taxonomy 指出 tests_failed_no_patch

---

## 四、负面结果追问

### Q7：20-case 没有提升，是不是说明自进化没用？

**面试官**：你 20-case 都没提升，那这个机制是不是没效果？

**候选人回答**：

我觉得要区分“候选策略本身的效果”和“自进化机制的有效性”。

11-case 说明当前候选 Skill 在一批真实任务上确实有正向收益；20-case 说明这个候选没有稳定泛化到更大样本，所以 gate 没让它通过。这不是机制失败，反而说明安全设计生效了：系统没有因为 11-case 好看就直接上线，而是在扩样后发现无正向 lift 时阻止晋升。

从工程角度看，20-case 的价值是暴露了下一步优化方向：通过 failure taxonomy 发现 evolved 失败集中在 tests_failed_no_patch，说明要优化执行器级约束或任务族策略，而不是继续盲目加 prompt。

**标准答案关键词**：

- 区分候选效果和机制有效性
- 20-case 是泛化检验
- gate 拦截是安全收益
- 下一步看 failure taxonomy

---

### Q8：你试过哪些优化？哪些没用？

**面试官**：你怎么知道不是简单多给模型几轮就好了？

**候选人回答**：

我做过几类实验。

第一是调 SOP。更激进或更精简的 SOP 在 5-case 失败子集上出现过 -40pp 的负向结果，所以我没有把它们作为默认策略。

第二是增加 evolved 预算，把 evolved max iterations 从 20 提到 35。结果 5-case 子集没有净成功率提升，但 input token 多了 24183，output token 多了 55323，平均耗时增加 86.60 秒。所以单纯加预算不是好策略。

第三是 strategy router，按任务族给 evolved 侧加短提示。在 2 个问题 case 上 baseline 50%，evolved 0%，出现 -50pp，所以这个 router 也被 gate 拦截，没有默认启用。

这些负向结果其实很重要，它证明我不是只挑好看的实验，而是把坏策略也纳入评测和门禁。

**标准答案关键词**：

- SOP 变体 -40pp
- 加预算无收益且成本大涨
- router -50pp
- gate 拦截负迁移

---

## 五、工程效率追问

### Q9：真实 provider 调用这么贵，你怎么控制评测成本？

**面试官**：每次都跑 20-case、40 次 provider 调用，成本是不是太高？

**候选人回答**：

是的，所以我后面专门做了评测成本优化。

第一是 baseline 复用。因为 baseline 和候选 Skill 无关，所以同一批 fixture 迭代候选时可以复用历史 baseline JSON，只重跑 evolved。这样 20-case 从 40 次 provider run 降到 20 次，理论节省 50%。真实 1-case smoke 验证执行 1/2 个 run，节省 50%。

第二是 case-id 定向复测。候选迭代初期不一定跑完整 20-case，可以先跑失败、回退或高风险 case。我实现了 `--case-id` 和 `--case-ids-file`。真实 targeted 2-case 复测中，只执行 2/40 个完整可比 provider runs，节省 95%。

第三是 failure bucket 自动复测。taxonomy 会自动生成 regression、unsolved、evolved_no_patch 等 bucket，CLI 支持 `--case-bucket` 直接从历史 JSON 展开 case id。真实 regression bucket smoke 只执行 1/40 个完整可比 provider runs，节省 97.5%。

**标准答案关键词**：

- baseline reuse：40 -> 20，省 50%
- case-id targeted：2/40，省 95%
- case-bucket：1/40，省 97.5%
- 不改变 gate，只降低成本

---

## 六、安全边界追问

### Q10：自进化会不会把坏策略写入系统？

**面试官**：如果模型总结了一个错误 Skill，系统怎么防止污染？

**候选人回答**：

主要有三层防线。

第一，候选和正式 Skill 分离。候选只在 evolved 评测侧注入，不会直接写入正式 Skill。

第二，隔离仓库运行。评测会复制 repository 到临时目录，Agent 的代码修改只发生在隔离副本里，不会污染原 fixture 或真实项目。

第三，canary gate 和审批。候选必须通过成功率 lift、回归、scope、provider/runner/timeout 等门槛。即使 gate pass，也只是表示满足离线晋升条件，最终是否启用仍由审批策略和用户确认控制。

我实际也遇到过负向策略，比如 strategy router 在 targeted canary 上 -50pp，这种会被 gate 拦截，不会进入默认路径。

**标准答案关键词**：

- candidate 与正式 Skill 分离
- 临时隔离仓库
- gate + 人工/策略审批
- 负迁移被拦截

---

## 七、面试官高压追问

### Q11：你这个和普通 Prompt Engineering 有什么区别？

**候选人回答**：

普通 prompt engineering 通常是人工改 prompt，然后看几个例子效果好不好。

我这里把 prompt/SOP 当成候选版本，进入类似 CI/CD 的评测流程：有固定真实 issue 数据集，有 baseline/evolved 对照，有目标测试和回归测试，有 scope 检查，有 provider/runner/timeout 稳定性指标，有 token 和耗时成本指标，还有 canary gate 和审批边界。

所以它不是一次性 prompt 调优，而是 agent behavior 的评测和发布系统。候选可以失败，失败会被记录成 taxonomy，反过来指导下一轮策略。

**回答关键句**：

它更像 agent behavior CI/CD，而不是手工 prompt 调参。

---

### Q12：如果让你继续优化 20-case，你下一步怎么做？

**候选人回答**：

我不会再盲目加长 prompt 或加预算，因为这两类已经在真实子集上验证过有负向或高成本问题。

下一步会围绕 taxonomy 里的 `tests_failed_no_patch` 做两件事。

第一是执行器级约束：如果若干轮内 Agent 已经看到失败断言和 allowed paths，但仍没有修改源码，就触发一个更明确的 repair checkpoint，比如要求输出候选文件、失败断言、预期修改点，再继续工具执行。

第二是策略池而不是单一路由。为 `sympy_printing`、`sympy_matrices`、`sympy_core` 等任务族维护多个候选策略，用 failure bucket 做低成本 targeted canary，先筛掉负向策略，再跑完整 20-case 验证泛化。

**标准答案关键词**：

- 不盲目加 prompt / budget
- tests_failed_no_patch 是靶点
- 执行器级 repair checkpoint
- 多策略池 + targeted canary + full canary

---

## 八、复盘：你的回答应该怎么分层

### 1. 一句话定位

自进化不是自动上线，而是候选 Skill 的隔离评测和晋升门禁。

### 2. 三个核心机制

- 候选生成：失败反馈、用户纠正、成功路径蒸馏。
- 隔离评测：baseline/evolved 双跑，目标测试、回归测试、scope 检查。
- 晋升门禁：正向 lift、回归 100%、越权 0%、failure 0%。

### 3. 四组关键数字

- 11-case：63.64% -> 90.91%，+27.27pp / +3 cases。
- 20-case：75% -> 75%，gate fail，拦截无收益候选。
- targeted 2-case：2/40 provider runs，节省 95%。
- bucket regression smoke：1/40 provider runs，节省 97.5%。

### 4. 必须主动说的边界

- 20-case 没有提升，不能包装成正向收益。
- strategy router 真实评测 -50pp，不能默认启用。
- 加预算没有净提升，且 token 和耗时大涨。
- gate pass 也不等于自动 promote，仍需审批。

---

## 九、简历对应表述

### 项目亮点写法

设计安全自进化闭环，将失败反馈、用户纠正和复杂任务经验沉淀为候选 Skill，在隔离仓库执行 baseline/evolved 双跑，通过目标测试、回归测试、越权修改、Provider/Runner/Timeout 等指标进入 Canary Gate；11 个 SWE-bench Lite 真实 Issue 上任务成功率由 63.64% 提升至 90.91%（+27.27pp），回归通过率 100%、越权修改率 0%。

### 成本优化写法

优化自进化候选评测成本，实现 baseline 复用、case-id 定向复测和 failure bucket 自动抽样；候选迭代可从完整 20-case / 40 次 Provider Run 降至自动抽桶后的 1 次 Run，真实验证调用节省 97.5%，同时保留回归、Scope、Failure 和 Canary Gate 判定。

---

## 十、常见错误回答

### 错误 1：只说“模型会自动学习失败经验”

问题：听起来像不可控的黑盒自优化。

改法：强调 candidate skill、隔离评测、gate、审批。

### 错误 2：说“20-case 也提升了”

问题：事实不对。20-case 是 75% -> 75%。

改法：说 20-case 验证了 gate 能拦截无收益候选。

### 错误 3：把 +27.27pp 说成 +27.27%

问题：百分点和百分比是两回事。

改法：说“从 63.64% 到 90.91%，提升 27.27 个百分点”。

### 错误 4：说候选会自动上线

问题：安全风险太大。

改法：说“通过离线 canary 后进入审批，不自动 promote”。

### 错误 5：只讲成功，不讲负向实验

问题：面试官会质疑挑数据。

改法：主动说 SOP 变体、预算变体、strategy router 都有负向结果，gate 正确拦截。
