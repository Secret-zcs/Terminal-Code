# 多Agent协作模块详解

> 日期：2026-08-01
> 类型：项目架构答疑

## 问题原文

"详细介绍一下该代码智能体项目的多Agent协作模块"

## 核心答案

MewCode 的多Agent协作分为两层基础设施 + 三种协作形态：

- **三层形态**：一次性子智能体（one-shot）、Fork（继承父会话）、团队协作（Team，长驻成员 + 独立 worktree + 消息协作）。
- **`mewcode/agents/`**：子智能体定义/加载/fork/任务管理/追踪/工具过滤。
- **`mewcode/teams/`**：团队管理、邮箱、共享任务、协调者、多后端 spawn。
- **集成点**：`tools/agent_tool.py`（Agent 工具）、`tools/send_message.py`、`tools/task_*.py`、`tools/team_*.py`、`app.py` 的通知轮询、`agent.py` 的 coordinator 模式。

## 关键模块与设计决策

### 1. 智能体定义与加载
- `parser.py`：`AgentDef` 从 Markdown YAML frontmatter 解析（name/description/tools/disallowedTools/model/maxTurns/permissionMode/background/isolation）。
- `loader.py`：项目级 > 用户级 > 内置 三级合并，支持热重载。
- 内置 4 类：Explore（只读/haiku）、general-purpose、Plan（只读）、Verification（只读/background）。

### 2. 工具过滤安全模型（tool_filter.py）
- MCP 工具（`mcp__*`）始终放行。
- 全局禁用：`Agent/AskUserQuestion/TaskStop/Workflow/EnterPlanMode/ExitPlanMode/TaskOutput`。
- 后台白名单 + 定义级 tools/disallowed 过滤。
- teammate 注入 5 个协作工具：`TaskCreate/TaskGet/TaskList/TaskUpdate/SendMessage`。
- coordinator 模式收窄主 agent 工具到调度集：`Agent/TaskStop/SendMessage/SyntheticOutput/TeamCreate/TeamDelete`。

### 3. 团队协作（teams/）
- `models.py`：`AgentTeam` + `TeammateInfo` + `BackendType`，JSON 持久化到 `~/.mewcode/teams/<slug>/`。
- `manager.py`：TeamManager 总控团队生命周期；删队要求全员 idle，并清理 pane/worktree/registry。
- `mailbox.py`：**基于文件的异步邮箱**（时间戳命名的 JSON），三种后端共用同一协议。
- `shared_task.py`：共享任务（pending/in_progress/completed/blocked + blocks/blocked_by）。
- `coordinator.py`：协调者 system prompt 定义四阶段工作流（Research→Synthesis→Implementation→Verification）。
- 多后端 spawn：in-process（asyncio task + progress 回调）、tmux/iTerm2（独立 pane 进程 + 环境变量注入团队身份）。

### 4. Spawn 团队成员流程（agent_tool.py:268）
1. 名字去重 → 2. 加载定义或 fork → 3. 创建 git worktree → 4. 选 LLM → 5. 构造 teammate 工具集 → 6. 创建子 Agent（注入 TEAMMATE_ADDENDUM）→ 7. 注册名字/成员 → 8. 按后端启动。

### 5. 消息闭环
队友完成 → 写 `[idle]` 到 lead mailbox → 轮询自己 mailbox（60×1s）等待 follow-up；lead 每 2s 轮询 drain_lead_mailbox → 注入 `<team-notification>` → 触发新一轮。

## 设计亮点（面试考点）

1. **文件即协议**：mailbox/task/team 全部 JSON 落盘，天然多进程 + 持久化。
2. **5 层工具沙箱** + verification 只读，收窄协作越权面。
3. **worktree 隔离**：并行实现不冲突。
4. **coordinator 模式是策略注入**：收窄工具 + 换 system prompt，而非硬编码调度。
5. **验证独立性**：实现者不能验证自己，避免锚定偏差。
6. **fork 复用 prompt cache**：`clone_replacement_state` 保证父子 cache 前缀字节一致。

## 涉及的关键文件

- `mewcode/agents/{loader,fork,parser,task_manager,tool_filter,trace,notification}.py`
- `mewcode/teams/{manager,mailbox,shared_task,coordinator,models,backend_detect,progress,registry,transcript,spawn_*}.py`
- `mewcode/tools/{agent_tool,send_message,task_create,task_get,task_list,task_update,team_create,team_delete}.py`
- `mewcode/app.py`（~L833 初始化、~L1480 通知轮询）、`mewcode/prompts.py`（coordinator 分支）
- `mewcode/worktree/`

---

## 通俗版（大白话，2026-08-01 追加）

> 用户反馈技术性太强没看懂，追加一版纯概念讲解。

**大前提**：MewCode 是 AI 编程助手。"多Agent协作" = 主 AI 能召唤其他 AI 帮手。

**三种帮手**：
- 一次性子Agent = 临时工（干完就走，不记得之前聊过什么）
- Fork = 分身（复制自己 + 全部记忆去干别的活）
- 团队 Team = 项目组（共享任务清单 + 共享信箱，成员长期驻留可互相发消息）

**团队干活的故事**（以"重构登录模块"为例）：
1. 建团队 = 建文件夹：`config.json`（花名册）+ `tasks.json`（任务清单）+ `mailbox/`（信箱）
2. 招募成员：研究员（只读）/ 写代码的 / 质检员（只读），每人发：① 隔离代码副本 worktree（防冲突）② 受限工具箱（防失控）③ 员工手册（必须用 SendMessage 才有人看见）
3. 沟通 = 传纸条：成员写 JSON 文件到信箱；主 AI 每 2s 轮询信箱，读到就当作系统通知；成员干完待机，收到消息才被唤醒
4. 协调 = 共享任务清单：标记 pending/in_progress/completed/blocked
5. 收尾：全员待机才能解散团队，清掉 worktree

**三个"为什么"**：
1. 通信全用文件 → 三种后端（同进程/tmux/iTerm2）统一协议，天然持久化
2. 质检员必须"新人" → 避免写代码的人橡皮图章自己的代码
3. 协调者模式 = 缴械主 AI：只剩 Agent/SendMessage/TaskStop/TeamCreate 调度工具，系统提示词换成"项目经理手册"，逼它分工

**代码三句话**：
- `mewcode/agents/` = 定义 AI 帮手 + 配工具
- `mewcode/teams/` = 团队/信箱/任务/起成员
- `tools/agent_tool.py` = 召唤入口（带 team_name = 招正式员工，不带 = 临时工）

---

## 四个深入点（2026-08-01 追加）

### ① 传纸条机制（mailbox）
- 物理 = 目录里的 JSON 文件，每成员一个收件箱目录，文件名带时间戳保证顺序
- 发信 = write 文件到对方目录；收信 = 按序读完即删（consume）；群发 = broadcast
- 消息是"拉"的：lead 每 2s 轮询；成员干完活写 `[idle]` 给 lead，然后轮询自己信箱（1s×60 次），收到消息即被唤醒继续干
- 文件+轮询让三种后端（同进程/tmux/iTerm2）共用同一协议，无需 IPC 适配

### ② coordinator 手册要点
- 角色：项目经理，不写代码，只用调度集工具（Agent/SendMessage/TaskStop/SyntheticOutput/TeamCreate/TeamDelete）
- 四阶段：Research（并行）→ Synthesis（lead 自己读结果写精确 spec）→ Implementation（worker 改）→ Verification（独立只读 worker 验收）
- 硬纪律：验证必须新人（防橡皮图章）；prompt 必须自包含、禁 "based on your findings"；按上下文重叠决定 SendMessage 继续 vs 新招；写文件任务一次一个；失败用 SendMessage 继续；不编造 worker 结果

### ③ worktree 隔离
- 物理机制：`git worktree add -B worktree-<名> <目录> HEAD`，每人独立目录+分支
- 三重隔离：文件系统、git 分支、权限沙箱（PathSandbox 指向各自 worktree，ADDENDUM 强制相对路径）
- 作用：防并发写同一文件互相踩；代价：把冲突推迟到合并阶段

### ④ 冲突怎么提交（核心）
- **代码事实：无自动合并逻辑**（grep 确认，git merge/cherry-pick 只存在于 prompt 文本）
- 流程：成员各自分支 commit → 报告 hash → **lead 手动 merge**（`git merge worktree-<名>` 或 cherry-pick）→ 冲突由 lead 读两边代码手动解决
- 理由：合并需理解双方意图，只有掌握全局的 lead 能做；lead=理解者/整合者，worker=执行者；同真实 GitHub 流程（maintainer merge）
- 防丢保障：auto_cleanup 检测到未提交/未推送变更即保留 worktree
- 坑：冲突延后到合并才暴露；lead 无脑 merge 可能引入半成品 → 手册强制验证独立 worker + 合并后跑测试

---

## 并发兜底分析（2026-08-01 追加）

结论：**代码兜底 + 手册兜底双保险**。崩溃类风险代码层兜得扎实；协调类风险全靠 AI 自律（手册软约束）。

### 有的兜底
- spawn 失败分级兜底：team 不存在报错、名字自动加后缀、worktree 创建失败 catch、模型 client 创建失败**退化用父 client**、context window 拉取失败退化内置表
- mailbox 读坏 JSON 跳过不删（`mailbox.py:63,79`）、`_consume_mailbox` 整体 try/except、`_wake_pane` 失败 pass
- 删团队要求全员 idle（`manager.py:176` 拒绝 active members）
- 全部 git 命令带 timeout + `GIT_TERMINAL_PROMPT=0` 防挂死
- worktree 有变更保留；force 清理失败 fallback `shutil.rmtree`；后台定时清 stale worktree
- 三处锁：worktree 创建 `asyncio.Lock`、名字注册表 `threading.Lock`、进度计数 `threading.Lock`
- worker 运行异常 → status=failed + 错误文本，不崩进程

### 真实缺口
1. **无自动重试**：无任何 retry 循环；重试是 coordinator 手册的行为约定，非硬保证
2. **任务认领无原子性**：`SharedTaskStore` 无锁无 CAS，assignee 可被覆写，双人认领同一任务可能（最严重）
3. **"长期驻留"有寿命**：in-process 队友 idle 后只轮询 60×1s；pane 队友 `-p` 轮询 90×2s≈3min 后进程退出——超时消息无人消费，成僵尸纸条
4. **pane 失败回退名不副实**：`agent_tool.py:482` 日志写 "falling back to in-process"，实际直接返回错误未回退
5. **通知轮询无异常保护**：`_start_notification_polling`/`_process_task_notifications`（`app.py:1499,1479`）无 try/except，异常会杀死后台轮询协程

### 若要生产化最该补的两点
- 认领原子性（lock/CAS 占用）
- 消息寿命（队友持久化或消息超时重投）
