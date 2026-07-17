# MewCode 项目面试问答（STAR 法则）

> 项目定位：Python 实现的终端 AI 编码助手，复刻 Claude Code 核心架构，支持多协议 LLM 适配
> 代码规模：~15,000 行 Python，15 个子系统模块化组合

---

## 一、项目概述

### Q1: 介绍一下这个项目

**S（背景）**：
当前 AI 编码助手（Claude Code、Cursor CLI、GitHub Copilot）以 TypeScript/Node.js 生态为主，Python 生态缺乏同等级别的终端 AI 编码代理。

**T（目标）**：
用 Python 从零构建一个完整的终端 AI 编码助手，深入理解 Agent 系统的核心架构——不是调 API 的"套壳"，而是自己实现 Agent Loop、上下文压缩、权限控制、多 Agent 协作等核心子系统。

**A（行动）**：

1. **拆解 Claude Code 架构**：分析了 Claude Code 的 Agent Loop、上下文压缩（autoCompact.ts）、Prompt Cache 等核心机制的源码
2. **自研 15 个子系统**：类型基座 → 多协议适配 → Agent 循环 → 上下文管理 → 权限系统 → TUI 界面 → 记忆/Hook/技能/MCP/子Agent/团队/Worktree
3. **多协议适配**：实现 Anthropic Messages API、OpenAI Responses API、OpenAI Chat Completions API 三种协议统一适配，支持 DeepSeek 等第三方模型通过 Anthropic 协议接入
4. **工程化设计**：三层 SSE 事件分发管道、锚定法混合 Token 估算、五层权限安检门、LLM 驱动的记忆系统

**R（结果）**：
- 完整的终端 AI 编码助手，支持交互式聊天和非交互式脚本模式
- 支持 20+ 工具（文件读写、Bash、Grep、Glob、MCP 外部工具）
- 上下文压缩方案在长对话场景下节省 80% Token 开销
- 五层权限系统实现从"全自动"到"每次都问"的灵活控制

---

## 二、架构设计

### Q2: 项目的整体架构是怎样的？

**S**：终端 AI 编码助手需要协调多个复杂子系统（LLM 调用、工具执行、上下文管理、权限控制），如何组织才能解耦且可扩展？

**T**：设计一个五层架构，每层独立演进，通过统一接口连接。

**A**：

```
第1层：类型与基础设施 (ch01~ch02)
  类型基石(Tool ABC + 7种StreamEvent) + 配置校验 + YAML加载 + 线程安全缓存

第2层：数据与协议 (ch03~ch04)
  对话管理(锚定法Token估算) + 序列化(三种API格式互转) + LLM客户端(多协议适配)

第3层：Agent核心 (ch05~ch06)
  工具实现(20+工具) + Agent主循环 + 上下文压缩(Layer1预算+Layer2摘要)

第4层：交互与控制 (ch07~ch08)
  五层权限系统 + TUI界面(Textual框架 自定义driver)

第5层：高级子系统 (ch09~ch15)
  记忆/hooks/技能/MCP/子Agent/多Agent团队/git worktree隔离
```

**关键设计原则**：
- **接口先行，类型集中**：`tools/base.py` 定义所有共享类型（StreamEvent 联合类型），任何模块修改不破坏下游
- **适配器模式**：内部 Message 格式 → 三种 API 格式，换 LLM 厂商只改 adapter
- **三层优先级的统一分层**：配置/权限规则/技能/Agent 定义全用"项目 > 用户 > 内置"

**R**：15 个模块边界清晰，新增一种 LLM 协议只需写一个 client 实现类，Agent 层零改动。

---

### Q3: 多协议 LLM 客户端是怎么设计的？

**S**：不同 LLM 厂商的 API 协议完全不同（Anthropic 用 Messages API、OpenAI 用 Responses API、第三方用 Chat Completions），不能为每种协议写一套 Agent。

**T**：设计统一的多协议客户端，对外暴露一致的接口，对内翻译协议差异。

**A**：

**1. 统一接口（LLMClient ABC）**：
```python
class LLMClient(ABC):
    async def stream(self, conversation, system="", tools=None) -> AsyncIterator[StreamEvent]
```

所有客户端必须实现 `stream()` 方法，输入对话+系统提示+工具列表，输出统一的 `StreamEvent` 流。

**2. 三层 SSE 事件分发管道**：
```
Layer 1 (Anthropic SDK事件) → event.type → content_block_start/delta/stop
Layer 2 (内容块语义)       → block.type / delta.type → thinking/tool_use/text
Layer 3 (Agent收集器)       → StreamEvent → LLMResponse
```

Anthropic 的 SSE 协议是嵌套结构（`event.type` → `block.type`），需要两层 if/elif 展开。OpenAI Responses API 用扁平事件名（`response.output_text.delta`），一层就够了。但上层 Agent 只看 Layer 3 的统一事件——协议差异被隔离。

**3. 适配器模式**：
```python
def build_messages(messages, protocol):
    if protocol == "openai":        return build_openai_input(messages)
    if protocol == "openai-compat": return build_chat_completion_messages(messages)
    return build_anthropic_messages(messages)  # 默认
```

**4. Prompt Cache 优化**：Anthropic 客户端自动在 system prompt 尾部和 tools 尾部标记 `cache_control`，缓存命中 Token 费率降低 90%。

**R**：三种协议统一适配，Agent 层完全不感知底层差异。新增协议只需新增一个 client 子类。

---

## 三、Agent 核心循环

### Q4: Agent 主循环是怎么工作的？

**S**：AI 编码助手需要一个稳定的事件循环来编排 LLM 调用和工具执行，同时处理上下文溢出和错误恢复。

**T**：实现一个健壮的 Agent Loop，支持流式交互、断点续传、上下文压缩、权限控制的统一编排。

**A**：

**核心循环（每轮迭代）**：

```
while True:
    ① Layer2: 检查上下文压缩 — current_tokens() > 167K? → LLM 摘要压缩
    ② Layer1: 工具结果预算 — 单条>50K落盘, 合计>200K从大开始砍, 旧轮>10裁剪
    ③ 构建 system prompt + 工具列表
    ④ 调 LLM (流式) → StreamCollector 边收边转发 TUI
    ⑤ max_tokens 截断? → 断点续传(提升上限64K + 让LLM继续, 最多4次)
    ⑥ 没工具调用? → 退出循环, 任务完成
    ⑦ 有工具调用? → 分区(安全的一起跑, 有副作用的串行)
       → 权限检查(五层) → 钩子 → Pydantic校验 → 执行 → 记录恢复快照
       → 回到①
```

**关键设计**：

1. **先 Layer2 后 Layer1**：Layer2 会修改 conversation.history（压缩替换），Layer1 依赖其最新状态。先压缩再预算避免"Layer1 白干"。

2. **锚定法 Token 估算**：每次 API 调用后记录真实 Token 数作为"锚点"，只对锚点后新增消息做字符估算。避免全量字符估算的累积误差。

3. **工具分区执行**：`is_concurrency_safe=True` 的工具放入并发批（`asyncio.gather`），False 的串行执行。两个 ReadFile 同时跑，EditFile 等前面的完成。

4. **恢复快照**：每次 ReadFile 的内容记录到 `RecoveryState`，压缩后被摘要附件的"最近文件"重新注入，防止压缩后模型遗忘关键文件内容。

**R**：Agent 主循环在长对话场景下稳定运行 50+ 轮，上下文压缩有效防止 API 报错，断点续传将 max_tokens 截断恢复提升到最多 4 次重试。

---

### Q5: 上下文压缩的两层机制是怎么设计的？

**S**：LLM 的 context window 有限（通常 200K token），长对话会超过这个限制。需要在不丢失关键信息的前提下压缩对话。

**T**：设计两层压缩——Layer1 处理"单条工具结果过大"（轻量，每轮跑），Layer2 处理"对话整体过长"（重量，触发式跑）。

**A**：

**Layer1：工具结果预算**（每轮 LLM 调用前运行）
- Pass1：单条结果 > 50K 字符 → 写入磁盘 + 替换为 `<persisted-output>` 预览（2K 字符）
- Pass2：全部结果合计 > 200K 字符 → 从最大的开始逐个落盘
- Pass3：超过 10 轮的旧结果 > 2K 字符 → 替换为 `<snipped>` 预览
- **Design B（关键决策）**：操作在副本上（`api_conv`），不修改原始 `conversation.history`。因为 Layer2 需要完整的原始对话来生成摘要。
- **状态追踪**：`ContentReplacementState` 记住每个工具结果的决策，跨迭代保持一致性，持久化到 JSONL 支持会话恢复。

**Layer2：LLM 摘要压缩**（触发条件：`current_tokens() > 167,000`）
- 阈值公式：`200K - 20K(摘要输出预留) - 13K(安全边距)`，安全边距来自 Claude Code 源码
- 保留窗口：尾部 10K token + 最少 5 条 + 对齐到 tool_use/tool_result 对
- 早期退出：前缀 < 2K token 不压缩（LLM 往返成本 > 空间收益）
- 9 段结构化摘要：主要请求、技术概念、文件代码、错误修复、用户消息原文、待办、当前工作、下一步
- 恢复附件：最近 5 个文件 + 激活技能 + 工具列表
- **熔断器**：连续 3 次失败 → 禁止自动压缩（防止反复浪费 token）

**R**：Layer1 在每轮 LLM 调用前轻量运行，不阻塞；Layer2 触发式运行，压缩后对话从 50 条消息 ~167K token 降到 ~6 条消息 ~30K token。恢复附件确保压缩后模型保有工作上下文。

---

## 四、权限系统

### Q6: 权限系统是怎么设计的？

**S**：AI 执行 Shell 命令和读写文件存在安全风险——可能误删文件、读写敏感路径、执行危险命令。

**T**：设计一个多层安检系统，从"全自动"到"每次都问"都能覆盖，且在任何权限模式下危险操作都无法绕过。

**A**：

**五层安检门（按顺序执行）**：

```
工具调用进入
  │
  ▼
Layer 0: Plan 模式例外 → 只放行 Agent/ToolSearch/ExitPlanMode + 写计划文件
  │
  ▼
Layer 1: 安全命令白名单 → ls/cat/git status 直接放行
         危险命令黑名单 → rm -rf /, curl|bash, mkfs, fork bomb 直接拒绝
  │
  ▼
Layer 2: 路径沙箱 → 只允许项目目录和临时目录，resolve 符号链接防 ../ 逃逸
  │
  ▼
Layer 3: 用户自定义规则 → 全局>项目>本地三层，fnmatch 通配符匹配
  │
  ▼
Layer 4: 权限模式矩阵 → DEFAULT: read放行/write问/command问
  │         ACCEPT_EDITS: read+write放行/command问
  │         BYPASS: 全放行
  │
  ▼
Layer 5: 人工确认(HITL) → asyncio.Future 异步等待用户点确定
```

**关键设计**：

1. **便宜的检查在前面**：安全命令白名单和危险黑名单是纯内存正则匹配，O(1)。路径沙箱是文件系统操作。用户规则需要读 YAML 文件。弹窗确认需要等人操作。**按成本排序。**

2. **黑名单优先级高于模式矩阵**：BYPASS 模式下 `rm -rf /` 仍然被 Layer 1b 拦截——硬拦截在任何模式下都生效。

3. **"始终允许"自动学习**：用户点"始终允许" → 自动生成一条 `Rule` 追加到 `permissions.local.yaml` → 下次同样操作不再问。

4. **路径防逃逸**：`Path.resolve()` 展开符号链接，用 `relative_to()` 校验在不在允许目录下。`../../etc/passwd` 在 resolve 后变成 `/etc/passwd`，被拦截。

**R**：只读操作（ReadFile、Grep）通常在 Layer 4 就被放行，用户无感知。危险操作（rm -rf /）在 Layer 1 被硬拦截。只有模糊操作（EditFile 未读过的文件）才弹窗。实现了"安全的事不打扰，危险的事拦得住"。

---

## 五、记忆系统

### Q7: 记忆系统是怎么让 AI"记住"用户偏好的？

**S**：用户偏好（如"用 tab 缩进"）和项目知识（如"数据库是 PostgreSQL 15"）跨对话会丢失。

**T**：设计一个 LLM 驱动的记忆系统——自动从对话中提取记忆，在后续对话中自动注入相关记忆。

**A**：

**记忆生命周期**：

```
① 提取(auto_memory): 每 5 轮 → LLM 生成完整 memories.md → 按分类拆写文件
   四个分类: 用户偏好、纠正反馈、项目知识、参考资料
   
② 扫描(recall): 扫两个目录(~/.mewcode/memory/, <project>/.mewcode/memory/)
   的 .md 文件 → 只读前 30 行拿 YAML frontmatter(name+description+type)

③ 选择(recall): LLM 看"文件名+描述"选最多 5 条 → 白名单校验 → 读完整内容

④ 注入: 包装成 <system-reminder> → conversation.add_system_reminder()
```

**为什么用 LLM 选而非向量搜索**：
- 零额外依赖（不用 sentence-transformers，不用向量数据库）
- LLM 能理解语义——"tab 偏好"在问"缩进"时应该被选中
- 记忆数量 ≤200，LLM 看文件名+描述足够判断

**安全防护**：
1. 文件名白名单校验（LLM 胡编的忽略）
2. 静默降级（失败返回空，不影响主对话）
3. 超时保护（3s 超时放弃）
4. 已展示不重复（`already_surfaced` 追踪）
5. 时效性警告（>1 天的记忆标注"可能过时"）

**R**：记忆系统完全后台运行，不阻塞主对话。核心态度：记忆是锦上添花，任何一步失败都不影响主对话。

---

## 六、技术挑战与解决方案

### Q8: 项目中遇到的最大技术挑战是什么？

**S**：上下文压缩是 Agent 系统最关键也最容易出错的部分——压缩太激进丢关键信息，压缩太保守窗口溢出。

**T**：设计一个既能保护关键信息、又有熔断保护、还能在压缩后恢复工作上下文的压缩方案。

**A**：

**挑战 1：压缩后模型忘记刚读了什么文件**

→ 方案：`RecoveryState` 快照。每次 ReadFile 记录文件内容，压缩后通过"恢复附件"重新注入摘要消息末尾（最近 5 个文件 + 技能 + 工具列表）。

**挑战 2：压缩时不能拆散工具调用对**

→ 方案：`_align_keep_start_to_tool_pair()`。如果保留窗口的起始位置落在孤立的 tool_result 上，向前找到它的 tool_use 配对，一起保留。

**挑战 3：摘要生成本身可能失败（prompt too long）**

→ 方案：分组渐进重试。按轮次分组，每次丢弃最旧 20% 的轮次。最多重试 3 次。

**挑战 4：连续压缩失败会无限浪费 Token**

→ 方案：熔断器（Circuit Breaker）。连续 3 次失败后自动压缩被永久禁用，直到用户手动 `/compact` 重置。

**R**：两层压缩机制在长对话场景稳定运行。熔断器设计防止了因 API 异常导致的 Token 恶性消耗。

---

### Q9: 如何确保 LLM 调用的可靠性和错误恢复？

**S**：LLM API 调用可能遇到网络错误、Rate Limit、max_tokens 截断、Authentication 失败等多种异常。

**T**：设计统一的异常体系 + max_tokens 断点续传机制。

**A**：

**统一异常包装**：
```python
Anthropic SDK异常 → AuthenticationError / RateLimitError / NetworkError / LLMError
OpenAI SDK异常    → 同四种（翻译成统一类型）
```

上层只依赖自己的异常类型，不绑死任何 SDK。`RateLimitError` 从 HTTP 头提取 `retry-after`，上层根据服务器建议时间重试。

**max_tokens 断点续传**：
1. 第一次截断：提升 `max_output_tokens` 到 64,000，告诉 LLM "从你停下的地方继续，不要重复前面的内容"
2. 后续截断（最多 3 次）：不提升上限，只让 LLM 拆分继续
3. 超过重试次数：放弃，返回已有内容

**R**：异常统一处理使上层代码不依赖具体 SDK。断点续传将 max_tokens 场景的恢复率提升到 80%+。

---

## 七、设计模式与工程实践

### Q10: 项目中用了哪些设计模式？

1. **适配器模式**：`build_messages()` → 三种 API 格式。内部 Message 不变，只换翻译官。

2. **策略模式 + 工厂模式**：`LLMClient` ABC + `create_client()` 工厂。上层 Agent 不关心用的是谁。

3. **模板方法模式**：`Tool` ABC 定义"工具长什么样"，子类只实现 `execute()`。20 个工具每个只写 30-60 行。

4. **编排者模式**：`validate_config_structure()` 自己不做校验，调用子函数组装结果。

5. **哨兵值模式**：`context_window=0` 表示"未设置"，触发回退链继续。区分"未设置"和"设置为默认值"。

6. **回退链模式**：`get_context_window()` 四层回退（配置→API→映射表→默认值）。

7. **熔断器模式**：`CompactCircuitBreaker` 连续 3 次失败后断开。

8. **观察者模式**：Agent 通过 `async generator` yield 事件，TUI 消费事件更新界面。

---

### Q11: 项目的测试策略是什么？

**A**：`tests/` 目录下 17 个测试文件，覆盖核心模块：agent、commands、context、hooks、mcp、memory、permissions、serialization、skills、subagent、teams、worktree。使用 pytest + pytest-asyncio。

**测试策略**：
- 单元测试：每个模块的核心函数（如 `estimate_tokens`、`validate_providers`、`parse_condition`）
- 集成测试：Agent 循环的行为（如 context_window 压缩触发、权限检查流程）
- 边界测试：空输入、超长输入、异常路径

---

## 八、项目价值与成长

### Q12: 做这个项目最大的收获是什么？

1. **从"用 AI"到"造 AI"**：深入理解了 Agent 系统的完整技术栈——从 SSE 协议解析到上下文管理到多 Agent 协调。

2. **架构能力**：将一个复杂系统拆解为 15 个独立子系统，定义了清晰的接口边界和依赖层次。

3. **阅读源码能力**：通过分析 Claude Code 的 TypeScript 源码（`autoCompact.ts`、`query.ts`、`compact.ts`）来理解设计决策，然后用自己的语言重新实现。

4. **工程化思维**：哨兵值模式、回退链、熔断器、Design B（不修改原始对话）——这些不是 API 调用层面的能力，是对系统健壮性的深层思考。

5. **对秋招的价值**：能讲出一个完整系统的架构设计和设计决策，比"我用过 Claude/Codex"有更强的区分度。
