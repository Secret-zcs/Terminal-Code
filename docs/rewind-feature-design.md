# Rewind 快照回退功能设计文档

> **版本**: v1.0  
> **设计日期**: 2025-06-25  
> **参考标准**: [Claude Code Rewind](https://docs.anthropic.com/en/docs/claude-code/overview)  
> **关联审计**: [mewcode 架构审计与改进方向](./audit-claude-code-standards.md)

---

## 目录

1. [需求分析](#1-需求分析)
2. [现有基础设施评估](#2-现有基础设施评估)
3. [架构设计](#3-架构设计)
4. [详细设计](#4-详细设计)
5. [实施计划](#5-实施计划)
6. [测试策略](#6-测试策略)
7. [复盘总结](#7-复盘总结)

---

## 1. 需求分析

### 1.1 用户故事

| # | 作为 | 我想要 | 以便 |
|---|------|--------|------|
| US-1 | 开发者 | 在重大重构前手动创建命名快照 | 重构失败时能精确回退到重构前的状态 |
| US-2 | 开发者 | Agent 编辑文件前自动创建快照 | 不满意 Agent 的修改时撤销所有变更 |
| US-3 | 开发者 | 查看所有快照的列表（包含标签和时间） | 快速定位想要回退到的时间点 |
| US-4 | 开发者 | 回退前预览哪些文件会被改动 | 确认回退范围，避免意外丢失工作 |
| US-5 | 开发者 | 选择回退代码或回退对话或两者都回退 | 灵活控制回退粒度 |
| US-6 | 开发者 | 撤销最近一次回退 | 误操作回退后能恢复 |
| US-7 | 开发者 | 重启 mewcode 后快照不丢失 | 崩溃或关闭后仍能回退 |

### 1.2 功能需求

| ID | 需求 | 优先级 |
|----|------|--------|
| F-1 | 手动创建快照：`/checkpoint "标签"` | P0 |
| F-2 | 自动快照：编辑文件前、危险命令前、轮次结束时 | P0 |
| F-3 | 快照列表：显示序号、标签、触发方式、文件数、时间 | P0 |
| F-4 | 回退预览：`/rewind N --preview` | P1 |
| F-5 | 三种回退模式：代码+对话 / 仅代码 / 仅对话 | P0 |
| F-6 | 回退撤销：`/rewind --undo` | P1 |
| F-7 | 快照持久化：磁盘存储，跨会话存活 | P0 |
| F-8 | 自动快照速率限制：最小间隔 30 秒 | P1 |
| F-9 | 配置化：`checkpoints.autoBeforeRisky` 开关 | P2 |

---

## 2. 现有基础设施评估

### 2.1 已存在的能力

```
┌─────────────────────────────────────────────────────────────┐
│                    FileHistory (现有)                        │
│                                                             │
│  track_edit(path)        → 记录文件被编辑，备份到磁盘        │
│  make_snapshot(idx,text) → 创建快照（所有tracked文件+版本）  │
│  get_snapshots()         → 返回内存中的快照列表              │
│  rewind(index)           → 将文件还原到指定快照              │
│                                                             │
│  存储位置: .mewcode/file-history/{session_id}/               │
│  备份格式: {sha256[:16]}@v{version}                          │
│  快照上限: MAX_SNAPSHOTS = 100                               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                  /rewind 命令 (现有)                          │
│                                                             │
│  无参数                   → 列出快照（仅序号+时间+文件数）    │
│  /rewind N 1             → 恢复代码+对话                     │
│  /rewind N 2             → 仅恢复对话                        │
│  /rewind N 3             → 仅恢复代码                        │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 与 Claude Code 的差距

| 维度 | mewcode 现状 | Claude Code | 差距评估 |
|------|-------------|-------------|----------|
| 创建时机 | 仅轮次结束时自动创建 | 手动 + 多时机自动触发 | 🔴 缺少手动和细粒度自动 |
| 标签 | 使用 response.text 前 60 字符 | 用户自定义标签 + 自动描述 | 🟡 不可控 |
| 持久化 | 仅内存（`self._snapshots: list`） | 磁盘持久化 | 🔴 重启即丢失 |
| 列表展示 | 裸序号 + 秒数 | 序号/标签/类型/文件数/时间 | 🟡 信息不足 |
| 回退预览 | ❌ | ✅ 展示哪些文件会被改 | 🔴 无法预览 |
| 回退撤销 | ❌ | ✅ | 🟡 无安全网 |
| 对话恢复 | 截断 history（正确） | 截断 + 重渲 UI | 🟢 核心逻辑已正确 |

### 2.3 关键代码路径

```
快照创建流程（现有）:
  agent.py:621-624  → LoopComplete 时 → file_history.make_snapshot()
  
回退执行流程（现有）:
  commands/handlers/rewind.py:57-74
    → option==1: file_history.rewind(idx) + conversation.replace_history(...)
    → option==2: conversation.replace_history(...)
    → option==3: file_history.rewind(idx)

对话恢复（现有）:
  conversation.py:188-197 → replace_history(new_messages)
  app.py:1717-1738       → _render_restored_messages(messages)
```

---

## 3. 架构设计

### 3.1 设计原则

1. **叠加而非替换**：FileHistory 的文件备份逻辑已验证正确，在其上叠加 CheckpointManager 编排层
2. **与 session 系统一致**：持久化采用 JSONL + 原子写入模式，与 `memory/session.py` 保持一致
3. **渐进增强**：现有 `/rewind` 命令的行为保持不变，在此基础上增加新能力
4. **最小侵入**：Agent 循环中仅注入钩子点，不改变核心循环逻辑

### 3.2 组件架构图

```
                         ┌──────────────────────┐
                         │     app.py (TUI)      │
                         │  - 初始化 CM          │
                         │  - 处理 rewind 事件    │
                         │  - 重渲对话           │
                         └──────────┬───────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
    ┌─────────▼─────────┐  ┌───────▼────────┐  ┌─────────▼─────────┐
    │ /checkpoint 命令   │  │  /rewind 命令   │  │  Agent 循环        │
    │ (手动创建快照)      │  │ (列表/预览/回退) │  │ (自动触发快照)      │
    └─────────┬─────────┘  └───────┬────────┘  └─────────┬─────────┘
              │                    │                     │
              └────────────────────┼─────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │     CheckpointManager        │
                    │     (统一编排层)              │
                    │                              │
                    │  create_checkpoint()         │
                    │  list_checkpoints()          │
                    │  preview_rewind()            │
                    │  execute_rewind()            │
                    │  undo_last_rewind()          │
                    └──────┬────────────┬──────────┘
                           │            │
              ┌────────────▼──┐  ┌──────▼──────────┐
              │  FileHistory   │  │ CheckpointStore  │
              │  (EXISTING)    │  │ (NEW)            │
              │                │  │                  │
              │  文件备份/还原  │  │ JSONL 持久化     │
              │  版本追踪       │  │ 元数据管理       │
              └────────────────┘  └──────────────────┘
```

### 3.3 数据流

```
                    创建快照
                    ═══════
  User/Agent触发
       │
       ▼
  CheckpointManager.create_checkpoint(label, trigger, conv, agent)
       │
       ├──→ FileHistory.make_snapshot(msg_index, label)     # 文件备份
       │         └── 遍历 _tracked → 备份到 .mewcode/file-history/
       │
       ├──→ agent_state = {plan_mode, permission_mode, ...}  # 捕获Agent状态
       │
       └──→ CheckpointStore.save(Checkpoint{...})            # 持久化元数据
                 └── .mewcode/checkpoints/{session_id}/checkpoints.jsonl


                    回退执行
                    ═══════
  User: /rewind N
       │
       ▼
  CheckpointManager.preview_rewind(seq)
       │
       ├──→ 对比 FileHistory 快照 vs 当前文件状态 → 变更列表
       └──→ 计算对话截断点 → 消息删除数量
       │
       ▼ (用户确认后)
  CheckpointManager.execute_rewind(seq, option="both")
       │
       ├──→ FileHistory.rewind(snapshot_index)              # 还原文件
       ├──→ conversation.replace_history(history[:msg_idx]) # 截断对话
       ├──→ CheckpointStore.delete_from(seq+1)              # 清理后续快照
       └──→ (保存 undo checkpoint)
```

---

## 4. 详细设计

### 4.1 数据模型

```python
# mewcode/checkpoint/models.py

from dataclasses import dataclass, field
from typing import Literal

TriggerType = Literal[
    "manual",         # /checkpoint 命令
    "turn_end",       # 每轮 LLM 结束时
    "pre_write",      # WriteFile/EditFile 执行前
    "pre_bash",       # 危险 Bash 命令前
    "pre_delegate",   # AgentDelegate 前
    "pre_compact",    # 对话压缩前
]

@dataclass
class Checkpoint:
    """单个快照的完整元数据"""
    id: str                    # UUID，全局唯一标识
    seq: int                   # 单调递增序号（用户可见，从1开始）
    label: str                 # 用户标签或自动生成描述
    trigger: TriggerType       # 触发方式
    message_index: int         # conversation.history 中的位置
    file_count: int            # 快照包含的文件数
    agent_state: dict          # {plan_mode, permission_mode, iteration}
    created_at: float          # Unix 时间戳

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "Checkpoint": ...

@dataclass
class RewindPreview:
    """回退操作前的预览信息"""
    checkpoint: Checkpoint
    files_to_change: list[FileChange]  # 哪些文件会被改动
    messages_to_remove: int            # 多少条消息会被删除
    message_snapshot: str              # 截断后的最后一条消息预览

@dataclass
class FileChange:
    path: str
    action: Literal["modify", "delete", "create"]
    current_size: int
    backup_size: int
```

### 4.2 持久化存储

```python
# mewcode/checkpoint/store.py

class CheckpointStore:
    """JSONL 持久化的快照元数据存储"""

    def __init__(self, session_dir: Path):
        self._path = session_dir / "checkpoints.jsonl"
        self._lock = threading.Lock()

    def save(self, cp: Checkpoint) -> None:
        """原子追加写入：先写 .tmp 再 rename"""
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(cp.to_jsonl() + "\n")
            tmp.rename(self._path)

    def load_all(self) -> list[Checkpoint]:
        """加载全部快照"""
        if not self._path.exists():
            return []
        checkpoints = []
        for line in self._path.read_text().splitlines():
            if line.strip():
                checkpoints.append(Checkpoint.from_jsonl(line))
        return checkpoints

    def delete_from(self, seq: int) -> None:
        """删除 seq 及之后的所有快照（rewind 后清理）"""
        all_cps = self.load_all()
        kept = [cp for cp in all_cps if cp.seq < seq]
        # 原子重写
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text("".join(cp.to_jsonl() + "\n" for cp in kept))
        tmp.rename(self._path)

    def undo_snapshot_path(self) -> Path:
        """回退撤销快照的路径"""
        return self._path.parent / "undo_checkpoint.json"
```

### 4.3 CheckpointManager

```python
# mewcode/checkpoint/manager.py

class CheckpointManager:
    """快照系统的统一编排层"""

    def __init__(
        self,
        file_history: FileHistory,
        session_dir: Path,
        auto_before_risky: bool = True,
        auto_min_interval: float = 30.0,
    ):
        self._fh = file_history
        self._store = CheckpointStore(session_dir)
        self._auto_enabled = auto_before_risky
        self._min_interval = auto_min_interval
        self._last_auto_time: float = 0.0
        self._seq_counter: int = 0  # 从磁盘恢复时重建

    # ── 创建 ──────────────────────────────────────

    async def create_checkpoint(
        self,
        label: str,
        trigger: TriggerType,
        conversation: ConversationManager,
        agent: "Agent",
    ) -> Checkpoint:
        """创建新快照（文件备份 + 元数据持久化）"""
        msg_index = len(conversation.history)

        # 委托 FileHistory 做文件级快照
        self._fh.make_snapshot(msg_index, label)
        snapshots = self._fh.get_snapshots()
        file_count = len(snapshots[-1].backups) if snapshots else 0

        # 抓取 Agent 状态
        agent_state = {
            "plan_mode": getattr(agent, "plan_mode", False),
            "permission_mode": (
                agent.permission_checker.mode.value
                if hasattr(agent, "permission_checker") and agent.permission_checker
                else "default"
            ),
            "iteration": getattr(agent, "iteration", 0),
        }

        # 创建并持久化
        self._seq_counter += 1
        cp = Checkpoint(
            id=uuid4().hex[:12],
            seq=self._seq_counter,
            label=label,
            trigger=trigger,
            message_index=msg_index,
            file_count=file_count,
            agent_state=agent_state,
            created_at=time.time(),
        )
        self._store.save(cp)
        return cp

    # ── 自动触发控制 ──────────────────────────────

    def should_auto_checkpoint(self, trigger: TriggerType) -> bool:
        """速率限制检查"""
        if not self._auto_enabled:
            return False
        elapsed = time.monotonic() - self._last_auto_time
        return elapsed >= self._min_interval

    def mark_auto_checkpoint(self) -> None:
        self._last_auto_time = time.monotonic()

    # ── 查询 ──────────────────────────────────────

    def list_checkpoints(self) -> list[Checkpoint]:
        return self._store.load_all()

    def get_checkpoint(self, seq: int) -> Checkpoint | None:
        all_cps = self.list_checkpoints()
        for cp in all_cps:
            if cp.seq == seq:
                return cp
        return None

    def has_checkpoints(self) -> bool:
        return len(self.list_checkpoints()) > 0

    # ── 预览 ──────────────────────────────────────

    def preview_rewind(self, seq: int) -> RewindPreview | None:
        """预览回退效果（不执行）"""
        cp = self.get_checkpoint(seq)
        if cp is None:
            return None

        snapshots = self._fh.get_snapshots()
        # seq 映射到 FileHistory snapshot_index
        # FileHistory 的 snapshots 与 checkpoints 一一对应
        snapshot = snapshots[seq - 1] if seq - 1 < len(snapshots) else None
        if snapshot is None:
            return None

        files_to_change = []
        for file_path, backup in snapshot.backups.items():
            try:
                current = Path(file_path).read_bytes()
                backup_data = Path(backup.backup_path).read_bytes()
            except FileNotFoundError:
                current = b""
                try:
                    backup_data = Path(backup.backup_path).read_bytes()
                except FileNotFoundError:
                    continue

            if current != backup_data:
                if current == b"" and backup_data != b"":
                    action = "create"
                elif current != b"" and backup_data == b"":
                    action = "delete"
                else:
                    action = "modify"
                files_to_change.append(FileChange(
                    path=file_path,
                    action=action,
                    current_size=len(current),
                    backup_size=len(backup_data),
                ))

        return RewindPreview(
            checkpoint=cp,
            files_to_change=files_to_change,
            messages_to_remove=0,  # caller fills in
            message_snapshot="",    # caller fills in
        )

    # ── 执行 ──────────────────────────────────────

    def execute_rewind(
        self,
        seq: int,
        option: Literal["both", "code", "conv"],
        conversation: ConversationManager | None = None,
    ) -> RewindResult:
        """执行回退操作"""
        cp = self.get_checkpoint(seq)
        if cp is None:
            return RewindResult(success=False, error=f"Checkpoint {seq} not found")

        changed_files: list[str] = []
        if option in ("both", "code"):
            # seq 与 FileHistory snapshot index 对齐
            changed_files = self._fh.rewind(seq - 1)

        messages_removed = 0
        if option in ("both", "conv") and conversation is not None:
            old_len = len(conversation.history)
            conversation.replace_history(
                conversation.history[: cp.message_index]
            )
            messages_removed = old_len - len(conversation.history)

        # 清理后续快照
        self._store.delete_from(seq + 1)
        # 删除 FileHistory 中 seq 之后的快照（rewind 已在 code 路径处理）
        # 截断 _seq_counter
        self._seq_counter = seq

        # 保存 undo checkpoint
        self._store.save_undo(cp)

        return RewindResult(
            success=True,
            changed_files=changed_files,
            messages_removed=messages_removed,
            checkpoint=cp,
        )

    # ── 撤销 ──────────────────────────────────────

    def undo_last_rewind(self) -> RewindResult | None:
        """撤销最近一次回退"""
        undo_cp = self._store.load_undo()
        if undo_cp is None:
            return RewindResult(success=False, error="No rewind to undo")
        # 恢复到 undo checkpoint 的文件状态
        changed = self._fh.rewind(undo_cp.seq - 1)
        self._store.clear_undo()
        return RewindResult(
            success=True,
            changed_files=changed,
            messages_removed=0,
            checkpoint=undo_cp,
        )

    # ── 会话恢复 ──────────────────────────────────

    def load_from_disk(self) -> int:
        """从磁盘恢复快照列表，返回快照数量"""
        checkpoints = self._store.load_all()
        if checkpoints:
            self._seq_counter = max(cp.seq for cp in checkpoints)
        return len(checkpoints)
```

### 4.4 Agent 集成点

```python
# agent.py 修改点

class Agent:
    def __init__(self, ...):
        # 新增
        self.checkpoint_manager: CheckpointManager | None = None

    # ── 注入点 1: 工具执行前（自动快照）─────────────

    async def _maybe_auto_checkpoint(
        self, tool: Tool, params: dict, conversation: ConversationManager
    ) -> None:
        cm = self.checkpoint_manager
        if cm is None:
            return

        trigger = None
        if tool.category == "write":
            trigger = "pre_write"
        elif tool.name == "Bash" and _is_risky_bash(params.get("command", "")):
            trigger = "pre_bash"

        if trigger and cm.should_auto_checkpoint(trigger):
            await cm.create_checkpoint(
                label=f"Auto: before {tool.name}",
                trigger=trigger,
                conversation=conversation,
                agent=self,
            )
            cm.mark_auto_checkpoint()

    # ── 注入点 2: 轮次结束时（替换原 621-623 行）────

    # 在 LoopComplete 路径中：
    if self.checkpoint_manager is not None:
        label = response.text[:60] + "…" if len(response.text) > 60 else response.text
        await self.checkpoint_manager.create_checkpoint(
            label=f"Turn: {label}",
            trigger="turn_end",
            conversation=conversation,
            agent=self,
        )
```

### 4.5 命令设计

```
/checkpoint "重构了认证模块"     → 创建命名快照
/checkpoint                     → 同 /rewind，列出所有快照

/rewind                         → 列出所有快照
/rewind N                       → 回退到第 N 个快照（代码+对话）
/rewind N --code                → 仅恢复代码
/rewind N --conv                → 仅恢复对话
/rewind N --preview             → 预览变更，不执行
/rewind --undo                  → 撤销最近一次回退
```

**列表展示格式**:

```
⟲ 8 checkpoints available (session: abc123)

  [1] "重构了认证模块"               manual      5 files  2 min ago
  [2] Turn: Let me refactor the...   turn_end    3 files  3 min ago
  [3] Auto: before WriteFile          pre_write   1 file   4 min ago
  [4] Auto: before Bash               pre_bash    0 files  5 min ago
  [5] Turn: I'll now update the...    turn_end    2 files  8 min ago
  ...

Tip: /rewind <N> to restore both code and conversation
     /rewind <N> --preview to see what will change
     /rewind <N> --code to restore code only
     /rewind <N> --conv to restore conversation only
```

**预览格式** (`/rewind 3 --preview`):

```
⟲ Preview: Rewind to [3] "Auto: before WriteFile"

Files that would be restored:
  ✎ src/auth/login.py       (342 bytes → 128 bytes)
  ✎ src/auth/__init__.py    (56 bytes → deleted)

Conversation: 24 messages would be removed (from #47 back to #23)

To execute: /rewind 3
To cancel: no action needed
```

---

## 5. 实施计划

### Phase 1: 数据模型与持久化层

| 步骤 | 文件 | 内容 |
|------|------|------|
| 1.1 | `mewcode/checkpoint/__init__.py` | 模块导出 |
| 1.2 | `mewcode/checkpoint/models.py` | Checkpoint, RewindPreview, FileChange, RewindResult 数据类 |
| 1.3 | `mewcode/checkpoint/store.py` | CheckpointStore: save/load_all/delete_from/save_undo/load_undo/clear_undo |

### Phase 2: CheckpointManager 编排层

| 步骤 | 文件 | 内容 |
|------|------|------|
| 2.1 | `mewcode/checkpoint/manager.py` | create_checkpoint, list_checkpoints, preview_rewind, execute_rewind, undo_last_rewind, load_from_disk |

### Phase 3: Agent 自动快照集成

| 步骤 | 文件 | 内容 |
|------|------|------|
| 3.1 | `mewcode/agent.py` | 注入自动快照触发点 + CheckpointManager 引用 |
| 3.2 | `mewcode/agent.py` | is_risky_bash() 辅助函数 |
| 3.3 | `mewcode/agent.py` | 替换 turn-end snapshot 路径 |

### Phase 4: 命令层

| 步骤 | 文件 | 内容 |
|------|------|------|
| 4.1 | `mewcode/commands/handlers/checkpoint.py` | /checkpoint 命令 |
| 4.2 | `mewcode/commands/handlers/rewind.py` | 增强列表展示、--preview、--undo 支持 |
| 4.3 | `mewcode/commands/handlers/__init__.py` | 注册 /checkpoint |

### Phase 5: TUI 集成

| 步骤 | 文件 | 内容 |
|------|------|------|
| 5.1 | `mewcode/app.py` | 初始化 CheckpointManager，设置 agent.checkpoint_manager |
| 5.2 | `mewcode/app.py` | rewind 后重渲对话（复用 _render_restored_messages） |

---

## 6. 测试策略

### 6.1 单元测试 (`tests/test_checkpoint.py`)

| 测试用例 | 验证点 |
|----------|--------|
| `test_checkpoint_to_dict_roundtrip` | Checkpoint 序列化/反序列化正确性 |
| `test_store_save_and_load` | JSONL 写入后能正确读取 |
| `test_store_delete_from` | 按 seq 删除后列表正确 |
| `test_store_undo_roundtrip` | undo checkpoint 保存/加载/清除 |
| `test_manager_create_checkpoint` | 创建快照后 FileHistory 和 Store 状态 |
| `test_manager_auto_throttle` | 30 秒内连续调用只创建 1 个快照 |
| `test_manager_preview_rewind` | 预览中的 FileChange 与实际文件状态一致 |
| `test_manager_execute_rewind_both` | option=both 时文件和对话都恢复 |
| `test_manager_execute_rewind_code_only` | option=code 时只恢复文件 |
| `test_manager_execute_rewind_conv_only` | option=conv 时只截断对话 |
| `test_manager_undo_rewind` | 撤销后回到回退前状态 |
| `test_manager_load_from_disk` | 从磁盘恢复后 checkpoint 数量正确 |

### 6.2 集成测试

| 测试用例 | 步骤 |
|----------|------|
| 完整回退流程 | /checkpoint → 编辑文件 → /rewind --preview → /rewind N → 验证文件恢复 |
| 自动快照 | 触发 WriteFile → 自动创建快照 → 验证快照存在 |
| 跨会话持久化 | 创建快照 → 重启 → 验证快照仍存在 |
| 回退撤销 | /rewind N → /rewind --undo → 验证状态恢复 |

---

## 7. 复盘总结

### 7.1 设计亮点

1. **叠加架构**：不替换已验证的 FileHistory，而是叠加编排层——降低回归风险，保持向后兼容
2. **与 session 系统一致**：JSONL + 原子写入模式复用成熟模式
3. **渐进增强**：现有 `/rewind` 行为完全保留，所有新功能都是可选的增强
4. **可恢复的快照**：持久化到磁盘，崩溃后不丢失

### 7.2 权衡取舍

| 决策 | 选择 | 权衡 |
|------|------|------|
| 快照持久化格式 | JSONL | ✅ 简单、可人工读取；⚠️ 百万级快照时效率低（对本场景不构成问题） |
| 备份内容 | 编辑后的值 | ✅ 空间效率高；⚠️ 回退时恢复的是快照创建时的"新值"而非"旧值"（需配合 rewind 截断快照列表） |
| 自动快照间隔 | 30 秒 | ✅ 防止洪泛；⚠️ 30 秒内的多次编辑共享一个快照 |
| 不新增依赖 | 纯 stdlib | ✅ 零依赖增加；⚠️ UUID 用 secrets 模块 |

### 7.3 后续优化方向

1. **快照 diff**：支持 `/rewind N --diff` 展示文件级别的具体变更内容
2. **快照分支**：支持从快照创建分支（类似 git checkout -b）
3. **快照 GC**：自动清理超过 N 天或超过 M 个的快照
4. **快照 tag**：支持给快照打多个标签，便于按主题检索
5. **快照 export**：支持将快照导出为 patch 文件

---

> **关联文档**: [mewcode 架构审计](./audit-claude-code-standards.md) P3-5 FileHistory 快照轮转配置化
