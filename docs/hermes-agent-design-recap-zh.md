# Hermes Agent 设计复盘：自进化、上下文管理与 Claude 对比

本文复盘本次围绕 Hermes Agent 的讨论，重点回答三个问题：

1. Hermes 相比常见 Agent 的关键差异是什么？
2. Hermes 的“自进化”具体如何实现？
3. Hermes 与 Claude/Claude Code 在 prompt cache、上下文压缩和工具结果管理上的设计差异是什么？

## 一、总体结论

Hermes Agent 更像一个“可扩展的个人 Agent 操作系统”，而不是单一的工具调用型 Agent。

它的设计重点不是把所有能力塞进核心模型工具，而是通过以下组件形成长期运行能力：

- 同一套 `AIAgent` 核心复用于 CLI、TUI、桌面端、消息网关、cron、batch、subagent。
- 通过 memory 记录用户偏好、长期事实和个人状态。
- 通过 skills 记录某类任务的做法、流程、坑点、脚本和模板。
- 通过 background review 在回合结束后自动复盘，把经验写入 memory/skills。
- 通过 `/learn` 把用户指定的资料、目录、URL 或刚完成的流程蒸馏成可复用 skill。
- 通过 plugins、toolsets、service-gated tools、MCP 等方式扩展能力，避免核心工具集膨胀。
- 通过 prompt cache 稳定性约束长期会话成本。

与 Claude/Claude Code 相比，可以概括为：

> Claude 更像“模型厂商提供的高集成 Agent Runtime”；Hermes 更像“围绕任意模型构建的可扩展个人 Agent OS”。

## 二、Hermes 的自进化策略

Hermes 的自进化不是训练模型权重，而是持续改写外部、可审计的行为资产：

- `MEMORY.md` / `USER.md` 等记忆文件；
- agent-created skills；
- skill 的 `references/`、`templates/`、`scripts/`；
- 可启用的插件、工具集、定时任务和长期会话状态。

### 2.1 回合结束后的 background review

在主回合结束后，Hermes 会在满足条件时触发后台复盘。

主要触发位置：

- `agent/turn_finalizer.py`
- `run_agent.py`
- `agent/background_review.py`

流程大致是：

```text
用户任务完成
  -> final response 已产生
  -> 若未 interrupt 且达到 memory/skill review 条件
  -> fork 一个后台 review agent
  -> replay 当前会话快照
  -> 只允许调用 memory / skill 管理工具
  -> 写入 memory 或 patch/create skill
  -> 向用户显示简短 self-improvement summary
```

关键点是：后台复盘不在主对话中直接修改上下文，而是在响应交付后异步执行。

### 2.2 复盘 Agent 是隔离且受限的

`agent/background_review.py` 中的后台 review fork 会继承父 agent 的运行时配置，但设置大量隔离开关：

- 继承 provider/model/base_url/api_key/api_mode，避免重新解析运行时。
- 同模型时复用父 agent 的 `_cached_system_prompt`，提高 prompt cache 命中。
- 设置 `max_iterations=16`，避免后台无限运行。
- 设置 `skip_memory=True`，防止复盘 harness 被外部 memory provider 当成真实对话吸收。
- 设置 `_persist_disabled=True`，防止“Review the conversation...” 这类后台指令写入真实会话历史。
- 设置 `_skip_mcp_refresh=True`，防止后台 fork 刷新 MCP 后改变工具列表。
- 设置 `compression_enabled=False`，防止后台 fork 抢占主会话压缩边界。
- 使用 thread-level tool whitelist，只允许 memory/skills 相关工具真正执行。
- 危险命令审批自动 deny，避免后台线程卡住交互。

这说明 Hermes 的“学习”不是让主 agent 在当前上下文中自我污染，而是通过一个隔离的 curator-like 分支把经验写到外部资产。

### 2.3 Memory 与 Skills 的分工

Hermes 明确区分两类长期资产：

- **Memory**：用户是谁、用户偏好、长期事实、当前状态。
- **Skills**：这类任务以后应该怎么做、工作流、坑点、验证步骤、脚本和模板。

这比“只做向量记忆”的 Agent 更工程化。

例如：

- 用户说“以后回答别这么啰嗦”，这是 memory，也是相关任务 skill 中的风格约束。
- 用户纠正了某个调试顺序，应写入对应 debugging skill。
- 某次工具失败是因为环境没装依赖，不应沉淀成“工具不可用”，而应沉淀安装/配置修复步骤。

### 2.4 Skills 更新优先级

后台复盘 prompt 对 skill 更新有明确策略：

1. 优先 patch 当前会话中已经加载过的 skill。
2. 如果不适合，patch 已有 umbrella skill。
3. 如果细节较多，写入 `references/`、`templates/` 或 `scripts/` 支撑文件。
4. 只有没有现有 skill 覆盖时，才创建新的 class-level skill。

它还明确禁止把以下内容固化为长期技能：

- 临时环境错误；
- 一次性任务叙事；
- “工具坏了”这类负面判断；
- 已经通过重试解决的短暂问题；
- 只对当前会话有效的事实。

这是一种防止 Agent “学坏”的保护机制。

### 2.5 `/learn` 是显式技能蒸馏入口

`agent/learn_prompt.py` 实现了 `/learn` 的 prompt 构造。

用户可以让 Hermes 从以下来源学习：

- 本次对话刚完成的流程；
- 本地目录或文件；
- URL/API 文档；
- 粘贴的笔记；
- 用户描述的操作规范。

`/learn` 不新增专门的核心模型工具，而是让当前 agent 用已有工具读取资料，再通过 `skill_manage(action="create")` 写出一个 `SKILL.md`。

这符合 Hermes 的窄核心原则：学习能力通过 skill 系统实现，而不是扩张 core model tool。

## 三、Prompt Cache 稳定性

Hermes 非常重视每个长会话的 prefix cache 稳定性。核心原则是：

> 长会话中，能保持 byte-stable 的前缀就不重建；能不改变 tools schema 就不改变；后台任务也尽量复用主会话已热的缓存前缀。

### 3.1 缓存命中不只看 system prompt

在 Claude/Anthropic 这类 prefix cache 模型下，请求前缀可能包含：

- system prompt；
- tools schema；
- 历史 user/assistant/tool messages；
- 技能索引；
- 其他 provider 需要重放的结构。

本轮最新 user message 通常在尾部，因此不可能被完整复用；但它之前的稳定前缀可以命中缓存。

所以 Hermes 关注的是：

```text
[稳定 system prompt + 稳定 tools schema + 稳定旧历史消息] -> 尽量命中缓存
[本轮新 user message]                                  -> 新增 suffix
```

### 3.2 复用 cached system prompt

后台 review fork 在同模型下直接继承父 agent 的 `_cached_system_prompt`，并钉住 `session_start` / `session_id`，避免重新渲染时出现时间戳、session id、技能 prompt、工具集差异。

这能减少 byte-exact prefix cache miss。

### 3.3 保持 tools[] 与主会话一致

后台复盘理论上只需要 memory/skills 工具，但 Hermes 不直接把 API 请求里的工具列表缩到最小。

它的设计是：

- API 请求层：尽量保持 `tools[]` 与主会话一致，提高 cache parity。
- 运行时执行层：用 thread-level whitelist 限制只能执行 memory/skill 工具。

这说明 Hermes 把工具 schema 视为缓存 key 的重要组成部分。

### 3.4 禁用后台 MCP refresh

后台 fork 设置 `_skip_mcp_refresh=True`，避免 between-turn MCP refresh 发现新工具，从而改变 `tools[]` 并破坏缓存命中。

### 3.5 技能索引缓存与快照

`agent/prompt_builder.py` 对 skills prompt 做了两层优化：

- 内存 LRU：`_SKILLS_PROMPT_CACHE`
- 磁盘 snapshot：优先读取预解析 manifest，冷路径才扫描所有 `SKILL.md`

这样既降低每轮构造成本，也减少 system prompt 的无意义波动。

### 3.6 技能采用“索引 + 按需加载”

Hermes 不把所有 skill 全文塞进 system prompt，而是注入技能索引：

```text
<available_skills>
  category:
    - skill-name: short description
</available_skills>
```

真正需要时再通过 `skill_view` 加载完整 `SKILL.md`。

这有三个好处：

- 降低常驻 prompt token；
- 减少 skill 小改动对巨大前缀的影响；
- 保留按需加载的灵活性。

## 四、上下文压缩与工具结果管理

### 4.1 Hermes 有运行时上下文压缩

Hermes 不是没有上下文压缩。运行时压缩主要在：

- `agent/conversation_compression.py`
- `agent/context_compressor.py`
- `run_agent.py` 的 `_compress_context()` 转发

压缩流程大致是：

```text
上下文接近阈值或用户手动 /compress
  -> 保护头部与尾部上下文
  -> 对中间历史做工具结果 pruning
  -> 用 auxiliary compression model 生成结构化摘要
  -> 插入 [CONTEXT COMPACTION — REFERENCE ONLY] 摘要消息
  -> 更新 session DB / system prompt / memory provider 边界
  -> 清理 file-read dedup
```

### 4.2 它不是每轮都大幅重写上下文

Hermes 的主策略是：

> 正常轮次尽量保留历史稳定；到阈值或手动压缩时，才集中做 compaction/pruning/replacement。

这与 Claude/Claude Code 常见的高频 runtime context management 不同。

Claude/Claude Code 更像每轮都可能对工具结果、上下文块、文件读取结果做整理、裁剪、替换或重排。

Hermes 更偏向：

- 保留近期工作集；
- 保持 prefix cache 稳定；
- 到上下文压力点再集中压缩。

### 4.3 Hermes 会裁剪/替换工具调用结果

`ContextCompressor` 中有 `_prune_old_tool_results()`。

它会在 protected tail 之外，把旧 tool result 替换成短摘要或占位，例如：

- `terminal`：运行了什么命令、exit code、多少行输出；
- `read_file`：读了哪个文件、多少字符；
- `search_files`：查了什么、多少匹配；
- 图片/截图：替换为文本占位；
- 大 tool output：替换为摘要或 `[Old tool output cleared to save context space]`。

这说明 Hermes 并不是原样保留所有 tool result 到爆上下文。

### 4.4 图片和截图处理

Hermes 会把历史消息中的图片 parts 替换为占位文本，避免 base64 图片每轮重复进入上下文。

相关函数包括：

- `_strip_image_parts_from_parts()`
- `_strip_images_from_content()`
- `_strip_historical_media()`

### 4.5 工具参数裁剪

工具调用参数也可能很大，例如 `write_file` 携带 50KB content。

Hermes 有 `_truncate_tool_call_args_json()`：

- 先解析 JSON；
- 裁剪长字符串字段；
- 再重新序列化；
- 避免直接截断导致非法 JSON。

### 4.6 尾部保护按 token budget，而不是只按消息数

Hermes 的 tail protection 会估算：

- message content；
- tool_calls envelope；
- 图片 token；
- provider replay fields；
- reasoning/codex replay blobs 等隐藏字段。

这避免“最近 20 条消息”中某条超大 tool result 把上下文撑爆。

### 4.7 压缩后清理 file-read dedup

压缩后，原始文件内容可能已经被摘要掉。

Hermes 会 reset file dedup，这样模型之后重新读取同一文件时，可以拿到完整内容，而不是只得到“file unchanged” stub。

## 五、为什么不把所有工具结果都换成索引？

讨论中提出了一个重要问题：

> 既然工具结果费 token，为什么不把所有工具调用结果都替换成索引，每次需要时再检索？

结论是：这是有价值的方向，但不能简单“全部索引化”。

原因如下。

### 5.1 模型需要连续局部上下文

复杂任务中，模型经常需要同时看到：

- 最近读了哪些文件；
- 哪个测试失败；
- 输出与修改点之间的关系；
- 用户是否否定过某个方案；
- 当前计划推进到哪一步。

如果全部外置成索引，模型必须不断判断“该查什么”，会增加工具调用次数和出错概率。

### 5.2 检索不完美

索引化依赖检索系统：

- 查询词可能不匹配；
- top-k 可能漏掉关键输出；
- 相似命令输出容易混淆；
- 一行错误、路径、版本号可能决定调试方向。

debug 场景尤其容易因为漏检而自信地出错。

### 5.3 工具结果是推理证据

很多时候 tool result 不只是数据，而是 assistant 后续判断的依据。

如果只留下 `tool_result_17: read file X`，模型回答“为什么这么改”时必须再次检索。如果检索失败，可审计性就变差。

### 5.4 工具调用协议需要结构配对

OpenAI/Anthropic 风格工具调用通常要求：

- assistant tool call 与 tool result 配对；
- `tool_call_id` 正确；
- role alternation 合法；
- 多工具并发结果顺序可解释。

不能随意删除或外置所有 tool result，必须保留结构占位和足够摘要。

### 5.5 索引本身也会增长

如果每轮都保留一张 tool result 索引表，它也会变长。

模型为了决定查什么，需要读索引；查回来之后，又要把结果重新放入上下文。最终不一定比保留最近关键上下文便宜。

### 5.6 更合理的是三级上下文

更合理的设计是分层：

1. **Hot context**
   最近几轮、当前任务必需的 tool result，原样或轻度裁剪保留。

2. **Warm summary**
   较旧但仍相关的历史，压缩成结构化摘要，保留决策、文件、命令、错误、待办。

3. **Cold archive**
   完整原始 tool result 外置存储，以 ref/id 可检索。需要证据或细节时再精确取回。

Hermes 当前更接近“hot context + threshold compaction + archived session DB”的方向。

## 六、Hermes 与 Claude 的最大设计差异

### 6.1 Claude：高集成厂商 Runtime

Claude/Claude Code 的很多关键能力在厂商 runtime 内部：

- 上下文选择；
- 工具结果裁剪；
- compact 策略；
- 文件上下文管理；
- 工具协议；
- 权限与安全逻辑；
- 用户交互体验。

优点是体验顺滑、模型与 runtime 协同强。

缺点是可观察性和可改造性较低，很多行为由闭源 runtime 决定。

### 6.2 Hermes：可扩展个人 Agent OS

Hermes 把很多 runtime 能力放在可见代码中：

- `AIAgent` 主循环；
- `model_tools.py` 工具分发；
- `toolsets.py` 工具集管理；
- `agent/context_compressor.py` 上下文压缩；
- `agent/background_review.py` 自我复盘；
- `tools/skills_tool.py` 技能加载；
- `tools/skill_manager_tool.py` 技能创建与 patch；
- `hermes_state.py` 会话 DB；
- `plugins/` 插件系统；
- `gateway/` 多平台消息入口；
- `cron/` 定时任务。

优点是可控、可审计、可扩展、可替换。

代价是工程复杂度更高，很多问题需要 Hermes 自己处理，例如 session 边界、压缩并发、prompt cache、插件隔离、多入口一致性。

### 6.3 一句话对比

```text
Claude:
  强模型 + 厂商 runtime
  重点是当前任务体验和高频上下文治理

Hermes:
  可插拔 agent OS
  重点是长期个人代理、跨入口运行、可审计学习、插件扩展和成本控制
```

## 七、后续值得继续研究的问题

1. Hermes 的 context compression 触发阈值具体如何配置？
2. `compression.in_place` 与 legacy rotation 在真实 gateway 场景中各有什么 tradeoff？
3. Hermes 是否可以进一步做 tool result cold archive + ref 检索？
4. Skills 的自动增长是否需要 curator 做去重、合并、质量控制？
5. prompt cache 命中率有没有运行时指标或日志可观测？
6. 与 Claude Code 相比，Hermes 在长任务中的 token 成本曲线如何实测？
7. 插件提供 context engine 时，如何保证不破坏核心 cache 和 session invariants？

## 八、本次讨论形成的关键判断

- Hermes 有自进化，但不是训练模型，而是更新 memory 和 skills。
- Hermes 有上下文压缩，不是完全原样保留历史。
- Hermes 也会裁剪和替换旧 tool result、旧图片和长工具参数。
- Hermes 不是每轮大幅改写上下文，而是更偏阈值触发式集中压缩。
- Hermes 强调 prompt cache 稳定，原因是长会话成本和一致性。
- Claude 更偏厂商 runtime 黑盒治理；Hermes 更偏开放、可审计、可扩展的 agent runtime。
- “全部工具结果索引化”有价值，但更合理的是 hot/warm/cold 分层，而不是全量外置。

