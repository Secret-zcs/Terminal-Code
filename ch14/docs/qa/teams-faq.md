# ch14 答疑：Agent 通信、协作、兜底

> 日期：2026-06-23

---

## Q1: Agent 之间怎么通信？

**两条路径**：

1. **notify_queue**（asyncio.Queue，内存）：工人→主 Agent，任务完成通知。app.py 每 2 秒轮询 `poll_completed()`，结果注入主对话为 `<task-notification>`。

2. **Mailbox**（JSON 文件，磁盘）：任何人→任何人。`write()` 写文件到 `mailbox/<recipient_id>/`，`consume()` 读后删除。工人每轮循环开始时 `_consume_mailbox()` 消费。主 Agent 通过 `drain_lead_mailbox()` 消费。

**工人完成时两条都发**：notify_queue 发任务结果，Mailbox 发 `[idle]` 信号。

---

## Q2: Agent 之间不通信，需要配合怎么办？

**设计哲学：协调者中转，工人不私下通信。**

协调者派 A 和 B 分别干活 → 两人都回来 → 协调者自己审核拼合 → 派 C 继续。

不支持工人间直接通信。原因：LLM 之间的互相通信极易失控。Mailbox 技术上支持任何人写给任何人，但设计上鼓励走协调者中转。

---

## Q3: 消息失败和兜底？

**基本没有。**

- notify_queue：内存中，崩溃丢失。任务状态也在内存（`_tasks` dict），重启遗忘。
- Mailbox：磁盘持久化，可恢复。但读后即删（`consume()` 调 `f.unlink()`），读后崩溃消息丢失。
- 没有 ACK、没有重传、没有确认机制。

设计假设：丢消息的代价 < 实现可靠消息的开销。最坏情况：协调者发现工人没反应 → 重新派或重新启动。
