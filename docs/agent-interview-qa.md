# 代码智能体项目面试问答整理

> 本文整理本次侧边对话中的问题与回答，聚焦多 Agent 通信、上下文压缩、权限拦截、自动记忆、提示词设计、子 Agent 创建、工具延迟加载与记忆更新策略。

---

## 1. 多 Agent 之间怎么通信？

### 问题

项目中多 Agent 之间怎么通信的，面试中如果别人问到了应该如何回答？

### 回答

项目里的多 Agent 通信可以概括为：

**它不是靠内存里的直接函数调用通信，而是以 `TeamManager` 管理团队，以文件型 `Mailbox` 作为消息总线，再通过 `SendMessageTool` 和后台轮询机制把消息送到对应 Agent。**

核心机制：

- **TeamManager 负责组队和路由**：维护 team、成员、mailbox、agent_id 到 team 的映射，负责创建团队目录、注册成员、删除团队、收集 lead 的消息。
- **Mailbox 是通信介质**：把每条消息序列化成 JSON 文件，按接收方 agent_id 写入对应目录。每个 Agent 有自己的 mailbox 子目录。
- **消息结构是 MailboxMessage**：包含 `id`、`from_agent`、`to_agent`、`content`、`summary`、`message_type`、`timestamp`、`metadata`，支持普通文本和 shutdown 类型消息。
- **SendMessageTool 是 Agent 主动发消息的工具**：允许 Agent 通过名字或 agent_id 发消息，也支持 `to="*"` 广播。
- **后台 Agent 通过轮询收消息**：后台任务完成初始任务后，会写一条 idle 消息给 lead，然后持续消费自己的 mailbox，有新消息就把消息内容拼成 prompt，再调用 `run_to_completion()` 继续执行。
- **Lead 通过 `drain_lead_mailbox()` 收集通知**：消费 lead 的 mailbox，把消息包装成 `<team-notification>` 注入主 Agent 对话上下文。

面试回答：

> 这个项目的多 Agent 通信采用的是基于文件 mailbox 的异步消息机制。每个 team 有独立团队目录，目录下有 `mailbox/`，每个 Agent 对应一个收件箱目录。Agent 之间通信时，不是直接调用对方对象，而是通过 `SendMessageTool` 创建结构化 `MailboxMessage`，写入目标 Agent 的 mailbox。目标 Agent 在自己的执行循环或后台任务轮询中消费这些 JSON 消息，再把消息内容作为新的 prompt 继续执行。

优点：

- 解耦：发送方不需要知道接收方是 in-process、tmux 还是 iTerm2 后端。
- 可持久化：消息落到文件系统，便于恢复和排查。
- 跨进程友好：不依赖同一个 Python 进程。
- 便于审计：每条消息有发送方、接收方、时间戳、类型和 metadata。
- 支持广播：`to="*"` 可以给团队内所有成员发消息。

缺点：

- 文件 mailbox 是轮询模型，不是实时事件驱动。
- 文件并发写入没有显式锁，高并发下需要更强一致性保护。
- `to_agent` 字段和实际写入目录主要靠上层约定。
- 团队规模扩大后，可以考虑 SQLite、Redis Stream、消息队列或 actor runtime。
- 当前消息消费是读完即删，如果要完整审计，需要增加归档日志或 transcript。

一句话总结：

> 这个项目的多 Agent 通信是一个轻量 actor/mailbox 模型：`TeamManager` 管理团队和成员，`Mailbox` 用文件系统保存结构化消息，`SendMessageTool` 负责投递，后台 Agent 轮询自己的 mailbox 并把收到的消息作为新 prompt 继续执行，Lead 再通过 `drain_lead_mailbox()` 收集团队通知。

---

## 2. 上下文压缩怎么做？

### 问题

项目中上下文压缩怎么做的？

### 回答

项目里的上下文压缩分两层：

**Layer 1：工具结果预算控制。**

这层发生在真正发给模型前，主要处理过大的 `tool_result`，核心函数是 `apply_tool_result_budget()`。

它的目标是：**不改原始 conversation，而是生成一个用于本轮 LLM 调用的压缩版 conversation。**

具体有三步：

- **单条工具结果超限落盘**：如果某个工具结果超过 `SINGLE_RESULT_CHAR_LIMIT = 50_000` 字符，就把完整结果保存到 `.mewcode/session/tool-results/{tool_use_id}.txt`，上下文里只保留 `<persisted-output>` 预览。
- **聚合工具结果超限继续落盘**：如果本轮所有工具结果加起来超过 `AGGREGATE_CHAR_LIMIT = 200_000` 字符，就按长度从大到小继续把结果落盘，直到总量降到预算以内。
- **旧工具结果裁剪**：对较早轮次的工具结果，如果不在最近 `KEEP_RECENT_TURNS = 10` 轮内，并且结果很长，就替换成 `<snipped>` 摘要，只保留短 preview。

这一层的关键点是：**工具输出太大时，不直接塞进上下文，而是落盘 + 预览 + replacement 记录。**

**Layer 2：整段对话 Auto Compact。**

第二层是当整个对话接近 context window 上限时触发的摘要压缩，核心函数是 `auto_compact()`。

触发阈值由 `compute_compact_threshold()` 决定：

```python
effective = context_window - SUMMARY_OUTPUT_RESERVE
margin = AUTO_COMPACT_SAFETY_MARGIN
threshold = effective - margin
```

Auto Compact 流程：

- 使用 `conversation.current_tokens()` 估算当前上下文大小。
- `_compute_keep_start_index()` 从尾部往前数，保留最近一段原文。
- 早期消息 `messages[:keep_start]` 交给 LLM 生成结构化摘要。
- 尾部 `messages[keep_start:]` 不摘要，直接拼回新 history。
- `build_recovery_attachment()` 会附加最近读过的文件、已激活 skill、当前可用工具列表等恢复信息。
- 最终新的 conversation 变成“摘要消息 + recovery attachment + 近期尾部消息原文”。

压缩后的持久化：

- 压缩后会保存 `CompactBoundary`。
- `CompactBoundary` 包含 `summary` 和 `keep`。
- Session 层通过 `make_compact_boundary(summary, keep)` 写一条特殊记录。
- 恢复 session 时，从最后一个 `compact_boundary` 开始重建。
- boundary 之前的原始记录保留在磁盘上做审计，但不再重放。

失败保护：

- 项目有 `CompactCircuitBreaker`。
- 自动压缩连续失败 3 次后熔断。
- 摘要生成遇到 prompt 太长，会按 turn 分组丢弃更早历史后重试，最多重试 3 次。

面试回答：

> 这个项目的上下文压缩是两层设计。第一层是工具结果预算控制，针对大工具输出做落盘和 preview 替换。第二层是对话级 auto compact，当上下文接近模型窗口上限时，把早期对话交给 LLM 生成结构化摘要，但保留最近一段原始消息，并保证 tool_use 和 tool_result 不被拆开。压缩后的 history 变成“摘要消息 + 恢复附件 + 近期原文”，并通过 compact_boundary 持久化，恢复 session 时从最后一个 boundary 开始重建。

---

## 3. 五层权限拦截怎么做？

### 问题

五层权限拦截是怎么做的？

### 回答

项目里的权限入口在 `PermissionChecker.check()`，Agent 每次真正执行工具前都会调用它。整体顺序是从“确定安全/确定危险”到“配置规则”再到“人工确认”。

严格按代码看，它有一个 **Layer 0 Plan 模式特例**，然后是 Layer 1 到 Layer 5：

- **Layer 0：Plan 模式例外放行**：`plan` 模式只允许规划相关工具，如 `Agent`、`ToolSearch`、`AskUserQuestion`、`ExitPlanMode`。同时允许写入当前 plan 文件。
- **Layer 1：安全只读命令自动放行**：如果是 command 类工具，并且命中 `is_safe_command()`，直接 allow。比如 `ls`、`pwd`、`cat`、`git status`、`git diff`。
- **Layer 1b：危险命令黑名单拦截**：`DangerousCommandDetector` 用正则匹配高危命令，比如 `rm -rf /`、`mkfs.*`、`dd if=... of=/dev/...`、fork bomb、`curl | bash`、`wget | bash`。命中后直接 deny。
- **Layer 2：路径沙箱**：对 read/write 类文件工具，用 `PathSandbox.check()` 校验路径。相对路径解析到项目根目录，允许访问项目根和系统临时目录，其他路径默认拒绝。
- **Layer 3：规则引擎**：`RuleEngine` 读取用户级、项目级、本地级三层 YAML 规则，按 `ToolName(pattern)` 做 fnmatch 匹配。命中 allow 就放行，命中 deny 就拒绝。
- **Layer 4：权限模式兜底**：如果前面没有明确结果，就按权限模式矩阵判断。`default` 下 read 放行，write/command 询问；`acceptEdits` 下 read/write 放行，command 询问；`bypassPermissions` 和 `dontAsk` 下全部放行。
- **Layer 5：人工确认 HITL**：如果模式矩阵返回 ask，Agent 会 yield `PermissionRequest` 给 UI，由用户决定 allow、deny 或 allow always。allow always 会生成本地权限规则，下次同类操作自动放行。

面试回答：

> 权限系统是一个分层短路决策链。工具执行前先抽取关键内容，比如 Bash 的 command、文件工具的 file_path。然后依次经过 plan 模式限制、安全只读命令白名单、危险命令黑名单、路径沙箱、用户/项目/本地规则、权限模式矩阵，最后才进入人工确认。这样既能自动放行低风险读操作，也能硬拦截高危命令，并把中等风险操作交给用户确认。

局限：

- 黑名单只能覆盖已知危险模式。
- `is_safe_command()` 是字符串启发式，无法完全理解复杂 shell 语义。
- 路径沙箱主要保护文件工具，Bash 命令自身仍可能访问更广路径。

---

## 4. 自动记忆提取怎么做？

### 问题

自动记忆提取是怎么做的？

### 回答

自动记忆在 `MemoryManager` 里实现，Agent 中通过 `_extract_memories()` 触发。

核心机制是：**每隔固定轮次，把最近新增对话交给一个独立的记忆提取 prompt，总结出值得长期保存的内容，写入 memories.md。**

触发机制：

- 常量 `MEMORY_EXTRACTION_INTERVAL = 5`。
- 每隔 5 轮左右触发一次。
- 用 `asyncio.ensure_future()` 异步执行，不阻塞主对话。
- `_extracting` 标志防止重复提取并发重入。

提取流程：

- `MemoryManager.load()` 读取已有记忆：用户级 `~/.mewcode/memories.md` 和项目级 `<project>/.mewcode/memories.md`。
- 用 `_last_extraction_msg_count` 记录上次提取到哪条消息，下一次只处理新增消息。
- 只抽取 user 和 assistant 的普通文本，不处理工具结果。
- prompt 要求按四类输出：用户偏好、纠正反馈、项目知识、参考资料。
- `_write_memories()` 解析标题，将用户偏好和纠正反馈写入用户级 memory，将项目知识和参考资料写入项目级 memory。
- `...`、`无`、`暂无`、`N/A` 等占位内容会被过滤。
- 记忆提取失败不会影响主 Agent 执行。

面试回答：

> 自动记忆是异步长期记忆提取机制。Agent 每隔 5 轮触发一次后台任务，把上次提取后新增的用户和助手文本交给专门的记忆提取 prompt。模型需要输出完整的 memories.md，并按用户偏好、纠正反馈、项目知识、参考资料分类。系统再把用户偏好和纠正反馈写到用户级 `~/.mewcode/memories.md`，把项目知识和参考资料写到项目 `.mewcode/memories.md`。整个过程不阻塞主对话，并且有 `_extracting` 防重入和 `_last_extraction_msg_count` 防重复提取。

---

## 5. 记忆以什么形式插入上下文？

### 问题

记忆是以什么形式插入上下文的，是用户消息吗？如果系统提示词发生变化，是覆盖原来的系统提示词，还是新创建一个 Agent 使用新的提示词？

### 回答

自动记忆不是作为真正的 system role 插入的，而是作为一条 `role="user"` 的 `<system-reminder>` 消息插入上下文。

形式大致是：

```text
role = "user"
content =
<system-reminder>
As you answer the user's questions, you can use the following context:

# mewcodeMd
...

# autoMemory
...

# currentDate
...

IMPORTANT: this context may or may not be relevant...
</system-reminder>
```

它包含：

- `# mewcodeMd`：项目/用户说明，即 `instructions_content`。
- `# autoMemory`：自动记忆，从 `MemoryManager.load()` 读取。
- `# currentDate`：当前日期。

真正的 system prompt：

- 来自 `build_system_prompt()`。
- 每轮重新构建。
- 通过 `client.stream(..., system=system)` 发送。
- 不进入 conversation history。
- 变化后下一轮可直接生效。

长期记忆和 mewcodeMd：

- 来自 `inject_long_term_memory()`。
- 被包装成 `role="user"` 的 `<system-reminder>`。
- 插入 conversation history。
- 默认只插入一次，因为有 `ltm_injected` 标志。
- 如果 memory 文件之后变化，当前 conversation 不会自动覆盖前面那条已插入的 reminder。

面试回答：

> 项目里有两类上下文注入。第一类是真正的 system prompt，它不进入历史消息，而是 Agent 每一轮调用 `build_system_prompt()` 动态构建，然后通过 `client.stream(..., system=system)` 作为本次 API 的 system 参数发送。因此 system prompt 的动态部分变化后，不需要新建 Agent，下一轮请求就会使用新 system。第二类是长期记忆和项目说明，它们不是 system role，而是被包装成 `<system-reminder>`，作为一条 user message 插入到 conversation history 前部。这个长期记忆默认每个 conversation 只注入一次，后续 memories.md 即使更新，也不会覆盖已注入的旧 reminder，除非新会话或 compact 后重建 history。

---

## 6. 有没有考虑缓存命中？

### 问题

那你没有考虑缓存命中吗？

### 回答

有考虑。需要区分：

```text
每轮重新发送上下文 != 每轮缓存失效
```

LLM API 是无状态的，所以项目每轮都会把当前 `system`、`messages`、`tools` 发出去。但如果 provider 支持 prompt cache，只要前缀内容稳定，仍然可以命中缓存。

项目里对缓存命中有统计字段：

```python
cache_read: int = 0
cache_creation: int = 0
```

`ConversationManager.record_usage_anchor()` 会把它们纳入 token 锚点：

```python
baseline_tokens = input_tokens + cache_read + cache_creation + output_tokens
```

这是因为 Anthropic 返回的 `input_tokens` 不包含 cache hit / cache creation 部分，所以项目要把 `cache_read` 和 `cache_creation` 加回来，才能估算真实上下文规模。

影响缓存命中的因素：

- hook prompt 变化
- coordinator mode 变化
- agent catalog 变化
- system prompt section 内容变化
- tool schema 列表变化
- deferred tool 被 discovered 后 schema 变多
- compact 后 history 被重建
- memory / instructions 被重新注入

自动记忆作为 user reminder 默认只注入一次，这其实有利于 prompt cache 稳定。它避免每次记忆更新都改变上下文前缀，但代价是新提取的记忆不会立刻进入当前上下文。

面试回答：

> 我们考虑了缓存命中。虽然 Agent 每轮都会重新发送 system prompt 和 messages，但这不代表每轮都完整重新计费。项目在流式事件里记录了 `cache_read` 和 `cache_creation`，并在 token anchor 里把它们加回来，用于上下文预算估算。真正影响 cache hit 的不是是否重新发送，而是请求前缀是否稳定。系统提示词、工具 schema、长期记忆、compact boundary 等内容如果变化，就会降低缓存命中；如果内容稳定，provider 侧仍然可以命中 prompt cache。

---

## 7. tools 里有没有延迟加载？

### 问题

你的 tools 里有没有延迟加载的？

### 回答

有。项目里有明确的 **工具延迟加载 / 延迟暴露机制**，主要是为了减少每轮 prompt 里的工具 schema 体积，尤其是 MCP 工具很多的时候。

默认字段：

```python
class Tool:
    should_defer: bool = False
```

默认所有工具不延迟加载。只有工具显式设置：

```python
should_defer = True
```

才会被认为是 deferred tool。

明确延迟暴露的包括：

- `AskUserQuestion`
- `EnterWorktree`
- `ExitWorktree`
- MCP 工具包装器

`ToolSearch` 自己永远不延迟，因为它是发现和加载其他 deferred tools 的入口。

延迟加载流程：

- 工具先正常注册到 `ToolRegistry`。
- 如果工具 `should_defer=True`，并且还没被 discovered，`get_all_schemas()` 不会把它发给模型。
- Agent 每轮通过 `get_deferred_tool_names()` 检查还有哪些 deferred tools。
- 如果存在，就往上下文里插入 `<system-reminder>`，告诉模型这些工具可以通过 `ToolSearch` 加载。
- 模型调用 `ToolSearch`，可以关键词搜索，也可以 `select:<name>` 精确加载。
- `ToolSearchTool.execute()` 找到 schema 后调用 `mark_discovered()`。
- 下一轮 `get_all_schemas()` 会把这些已 discovered 的工具 schema 加进请求里。

面试回答：

> 有。项目里的工具延迟加载不是懒实例化，而是延迟暴露 schema。所有工具仍然可以先注册到 `ToolRegistry`，但如果工具设置了 `should_defer=True`，在未被发现前，`get_all_schemas()` 不会把它的完整 schema 发给模型。Agent 只会通过 system-reminder 告诉模型有哪些 deferred tools 可以通过 `ToolSearch` 加载。模型调用 `ToolSearch` 后，注册表会把对应工具标记为 discovered，下一轮请求才会把完整 schema 暴露给模型。

---

## 8. 用户强制性要求放在 user reminder 会不会被稀释？

### 问题

消息列表里没有系统消息会不会导致有一些用户的强制性要求的注意力会被稀释，而如果作为系统消息则会让模型更加重视？

### 回答

会，有这个风险。把“强制性要求”放在普通 `user` 消息里的 `<system-reminder>`，注意力和指令优先级都弱于真正的 API `system` prompt。

从模型指令优先级看：

- 真正的 `system` prompt 通常优先级最高，用来放身份、安全边界、不可违背规则、工具使用原则等硬约束。
- 项目里的长期记忆、`mewcodeMd`、自动记忆是 `Message(role="user", content="<system-reminder>...</system-reminder>")`。
- 它虽然写了 `<system-reminder>`，但从 API 角色上看仍然是 user message。

从长上下文注意力看：

- 位置靠前，长对话后注意力可能衰减。
- auto compact 时可能被摘要化，而不是原文保留。
- 如果只注入一次，后续 memory 更新不会马上刷新。
- 工具结果、代码片段、用户新指令可能挤占模型注意力。
- `<system-reminder>` 只是文本约定，不是 API 级别权限边界。

更合理的分层：

- **真正 system prompt**：放安全边界、工具调用规范、必须遵守的全局硬约束。
- **developer / project instruction 或高优先级 instruction section**：放项目级强约束，如测试要求、禁止修改区域、代码风格硬要求。
- **user reminder / memory**：放软信息，如用户偏好、项目背景、历史纠正、参考资料。

面试回答：

> 会有这个问题。项目里的自动记忆和 mewcodeMd 是作为 `role=user` 的 `<system-reminder>` 插入 history，而不是真正的 system message。这样做方便持久化、压缩和恢复，也有利于保持 system prompt 稳定、减少 prompt cache 失效。但缺点是这些内容的指令优先级低于真正的 system prompt，在长上下文里也可能被注意力稀释。因此我会把它定位为软上下文和偏好记忆，而不是硬约束。如果是用户明确的强制性要求，更合理的做法是提升到 system prompt 或专门的 high-priority instruction section，每轮动态构建并通过 API system 参数发送。

---

## 9. 提示词是怎么设计的？创建子 Agent 的提示词呢？

### 问题

你的提示词是怎么设计的，比如创建子 Agent 的提示词，面试官问到了我该如何回答？

### 回答

可以从 **主 Agent 提示词分层 + 子 Agent 定义文件 + AgentTool 运行时组装** 三个角度回答。

主 Agent 提示词设计：

- 主提示词不是一整段硬编码，而是拆成多个 `PromptSection`。
- 每个 section 有 `name`、`priority`、`content`。
- 构建时按 priority 排序后拼接。

主要分层：

- `Identity`：定义身份，说明它是终端里的 AI 编程助手，并强调安全编码。
- `System`：通用系统规则，比如工具结果可能包含 prompt injection、hook 反馈视为用户输入、上下文会自动压缩。
- `DoingTasks`：任务执行策略，比如先读代码再改、不要过度设计、完成前要验证。
- `ExecutingActions`：高风险操作处理原则，比如删除、force push、reset 要确认。
- `UsingTools`：工具使用策略，比如优先专用工具，不要用 Bash 替代 ReadFile/EditFile/Grep。
- `ToneStyle`：输出风格，要求简洁、少废话。
- `TextOutput`：对用户可见文本的规范。
- `Environment`：当前目录、操作系统、日期。
- `CustomInstructions`、`Skills`、`Memory`：可选动态注入。

子 Agent 提示词设计：

- 子 Agent 的提示词不是写在代码里，而是用 markdown 文件定义。
- 每个子 Agent 文件由 YAML frontmatter 和 markdown body 组成。
- frontmatter 描述元数据，body 作为子 Agent 的 system_prompt。

元数据包括：

- `name`
- `description`
- `disallowedTools`
- `model`
- `maxTurns`
- `permissionMode`
- `background`
- `isolation`

内置子 Agent：

- `Explore`：只读搜索专家，禁止写文件和创建子 Agent。
- `Plan`：架构规划专家，只读分析，不直接修改。
- `general-purpose`：通用子 Agent，拥有完整能力，用于独立上下文任务。
- `Verification`：验证专家，目标是找 bug，要求实际跑构建、测试、lint，最终输出 `VERDICT`。

子 Agent 创建流程：

- 主 Agent 调用 `AgentTool`。
- 如果传了 `subagent_type`，`AgentLoader.get()` 加载对应 `AgentDef`。
- `AgentDef.system_prompt` 来自 markdown body。
- 根据 `disallowedTools` 过滤工具注册表。
- 根据 `model` 选择 LLM client。
- 根据 `permissionMode` 创建子 Agent 的权限检查器。
- 创建新的 `Agent` 实例。
- 把 `definition.system_prompt` 传给子 Agent 的 `instructions_content`。
- 子 Agent 用自己的独立 `ConversationManager` 执行任务。
- 前台子 Agent 直接返回结果，后台子 Agent 由 `TaskManager` 管理。

面试回答：

> 我们的提示词设计是分层的。主 Agent 的 system prompt 由多个 `PromptSection` 组成，包括身份、安全边界、任务执行规范、工具使用规范、输出风格、环境信息等，按 priority 稳定拼接。子 Agent 则采用声明式 markdown 定义，frontmatter 描述 name、description、model、maxTurns、disallowedTools、permissionMode、background、isolation，正文是该子 Agent 的专用提示词。运行时 `AgentTool` 根据 `subagent_type` 通过 `AgentLoader` 加载 `AgentDef`，再为它创建独立的 Agent 实例、独立上下文、过滤后的工具集和权限检查器。这样子 Agent 既继承基础系统行为，又通过自己的 prompt 和工具权限形成专门角色。

---

## 10. 主 Agent 怎么判断需要创建子 Agent？

### 问题

主 Agent 是怎么判断需要创建子 Agent 的？

### 回答

主 Agent 不是用传统 if/else 代码判断“什么时候创建子 Agent”，而是把“是否创建子 Agent”交给 LLM 在推理时决定。

项目采用的是：

```text
提示词引导 + Agent 工具 schema + 子 Agent 类型说明 + LLM 自主选择
```

而不是：

```text
if task_complexity > threshold:
    create_subagent()
```

判断机制：

- 系统提示词会告诉主 Agent：复杂、多步骤、需要专门角色或独立上下文的任务，可以使用 `Agent` 工具委派。
- `AgentTool` 本身的描述也告诉模型它可以 “Launch a sub-agent to handle a task in an isolated context”。
- 项目启动时 `AgentLoader` 会加载子 Agent 定义，形成 agent catalog。
- 主 Agent 看到这些描述后，由模型自主决定是否调用 `AgentTool`，以及选择哪个 `subagent_type`。

主 Agent 倾向创建子 Agent 的场景：

- 需要并行探索。
- 需要独立上下文。
- 需要专门角色，如 `Explore`、`Plan`、`Verification`。
- 需要后台执行。
- 需要团队协作。

面试回答：

> 主 Agent 判断是否创建子 Agent 不是靠硬编码规则，而是靠提示词和工具调用机制。系统提示词会告诉主 Agent：复杂、多步骤、需要专门角色或独立上下文的任务，可以使用 Agent 工具委派。项目启动时 `AgentLoader` 会加载内置、用户级和项目级子 Agent 定义，每个定义都有 name、description、工具权限、模型和最大轮数。主 Agent 在看到用户任务和可用 agent catalog 后，由 LLM 自主决定是否调用 `AgentTool`，并选择合适的 `subagent_type`，比如 `Explore`、`Plan`、`Verification` 或 `general-purpose`。

如果面试官追问有没有确定性规则：

> 没有传统意义上的复杂度打分器。确定性部分在于 `AgentTool` 如何创建子 Agent、如何加载 `AgentDef`、如何过滤工具、如何设置权限和最大轮数；非确定性部分在于主 Agent 是否调用 `AgentTool`、选择哪个 `subagent_type`，这是由模型根据提示词和任务语义决定的。

---

## 11. 记忆是覆盖式更新还是增量？

### 问题

记忆是覆盖式更新吗还是增量？

### 回答

是 **覆盖式更新为主，不是纯增量 append**。

流程：

- `MemoryManager.extract()` 先读取当前已有记忆。
- 然后只取最近新增对话。
- 把“当前 memories.md + 最近对话”一起交给 LLM。
- LLM 被要求输出完整的 memories.md 内容，不是只输出新增条目。
- `_write_memories(collected)` 会重新解析模型输出，然后用 `write_text()` 写回文件。

所以它是：

```text
输入：已有记忆 + 新增对话
输出：合并后的完整记忆文件
写入：覆盖原文件
```

不是：

```text
只把新增记忆 append 到文件末尾
```

但它也不是完全每次全量扫描对话。它在对话侧是增量的：

```python
_last_extraction_msg_count
```

只把上次提取之后的新消息交给记忆提取器。

总结：

```text
对话读取：增量
记忆生成：基于已有记忆做合并
文件写入：覆盖式
```

面试回答：

> 自动记忆不是简单 append，而是覆盖式合并更新。系统每次只取上次提取后新增的对话片段，但会同时把当前已有 memories.md 发给 LLM，让它输出更新后的完整 memories.md。最后根据分类重新写入用户级和项目级记忆文件，因此磁盘写入是覆盖式的。这样可以避免重复条目，但风险是 LLM 如果漏掉旧内容，可能导致记忆被意外删减。

优点：

- 可以去重。
- 可以重组分类。
- 可以修正已有记忆。
- 文件始终保持结构化完整格式。

缺点：

- 依赖 LLM 正确保留旧记忆。
- 覆盖写有误删风险。
- 并发提取时可能出现后写覆盖先写。
- 不如 append-only log 容易审计历史变化。

改进方向：

- 使用 append-only 原始记忆日志。
- 定期 compaction 生成 `memories.md`。
- 每条 memory 带 id、source、created_at、updated_at。
- LLM 输出 patch/diff，而不是完整覆盖文件。
- 覆盖前做旧条目保护和冲突检测。

