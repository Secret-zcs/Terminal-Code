# ch14：多 Agent 团队 — `teams/`

> 文件：`manager.py`, `models.py`, `mailbox.py`, `coordinator.py`, `spawn_inprocess.py`

---

## 一、三种 spawn 模式

- `IN_PROCESS`：同一 Python 进程，asyncio.create_task
- `TMUX`：独立 tmux 分屏（可视化）
- `ITERM2`：独立 iTerm2 标签（可视化）

---

## 二、Mailbox — 文件邮箱

```python
class Mailbox:
    def write(self, agent_id, message):
        # 写 JSON 文件到 mailbox/<agent_id>/<timestamp>.json

    def consume(self, agent_id):
        # 读所有消息 → 返回 → 删除文件（f.unlink()）
```

**两条通信路径**：
1. notify_queue（`asyncio.Queue`，内存）：工人→主Agent，任务完成通知
2. Mailbox（JSON 文件，磁盘）：任何人→任何人，SendMessage 写信

工人完成时两条都发：notify_queue 发任务结果，Mailbox 发 `[idle]` 信号。

---

## 三、协调者模式

主 Agent 的 system prompt 被 230 行 coordinator prompt 替换。协调者工具只有 5 个：Agent、SendMessage、TaskStop、SyntheticOutput、TeamCreate/Delete。

**核心规则**：
1. 并行是超能力 — 独立任务同时派
2. 验证必须是独立工人 — 永远不要让实现的工人验证自己的代码
3. 工人看不见主对话 — prompt 必须自包含，不要写"按你的发现来改"
4. 协调者中转 — 工人不私下通信

---

## 四、兜底情况

基本没有。notify_queue 在内存中（崩溃丢失），Mailbox 在磁盘上（可恢复但读后即删）。没有 ACK、没有重传。设计假设：丢消息的代价 < 实现可靠消息的开销。
