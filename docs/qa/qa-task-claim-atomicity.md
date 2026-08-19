# 任务认领原子性修复（TDD 演示）

> 日期：2026-08-01
> 类型：并发缺陷 + TDD 修复

## 问题原文

"有没有兜底措施，比如发任务发放失败或者任务认领失败等等一系列并发问题"

→ 追问："可以"（同意演示并发认领 bug + 修复）

## 核心答案

多Agent 团队的任务认领存在竞态：`SharedTaskStore` 是纯 JSON 文件读写，无锁无 CAS，`assignee` 可被任意成员覆写。两个协程同时认领同一任务会双双成功（last-write-wins）。

**修复**：新增原子 `claim(task_id, agent_name)`，用 `fcntl.flock` 独占文件锁保护整个"读-判-写"序列，同一任务只允许一个认领者置为 `in_progress`。

## TDD 过程

### RED（先写测试，看它失败）
1. `test_naive_check_then_act_claim_has_race`：模拟"先查 pending 再认领"的朴素模式，用 `asyncio.sleep(0)` 强制制造读-写竞态窗口 → **失败**：`[True, True]`，双双认领成功（bug 复现）
2. `test_claim_is_atomic_across_threads`：期望新的原子 `claim()` API → **失败**：`AttributeError: no attribute 'claim'`

### GREEN（最小实现）
```python
def claim(self, task_id, agent_name) -> bool:
    # fcntl.flock 独占锁包住 load→check pending→改状态→save
    # 任务不存在或 status != pending → return False
```

### 验证
- `test_claim_is_atomic_across_threads` PASS（`asyncio.to_thread` 两个线程各自 open 锁文件 → flock 串行化）
- 原演示测试转 `xfail` 归档（文档化 update() 模式的已知竞态）
- 边界用例：已认领/完成态/不存在 → 均拒绝
- 结果：8 passed, 1 xfailed；全量 test_teams.py 6 个失败均为**预先存在**（环境相关，与本改动无关，stash 验证）

## 关键代码/设计决策

- 锁粒度：整个 check-then-act 序列加锁，而非仅写操作（锁写不锁读仍会竞态）
- 锁实现：`fcntl.flock`（POSIX），随 `.lock` 文件创建，自动释放（close 即解锁）
- `flock` 按 open file description 独立，同进程不同 fd 也会互斥 → 线程/进程都安全
- 非 POSIX 平台（Windows）无 fcntl → `ImportError` 兜底降级为无锁（文档注明）
- 兼容：`asyncio.to_thread` 用于测试模拟跨进程；生产 in-process 队友也在线程中跑，同样受保护

## 面试考点

1. **竞态的本质**：读-判-写（check-then-act）非原子，需在"读"与"写"之间加临界区
2. **锁的粒度**：锁覆盖整个事务序列，不是只锁写
3. **跨进程锁选型**：线程锁（threading.Lock）管不了跨进程；fcntl.flock 是 POSIX 跨进程文件锁
4. **TDD 纪律**：先写会失败的测试复现 bug → 最小实现 → 归档文档化旧缺陷（xfail）
5. **诚实的技术判断**：能区分"代码兜底"（崩溃/损坏类）与"行为约定"（协调类，靠 prompt 约束 AI）

## 涉及文件

- `mewcode/teams/shared_task.py`（新增 `claim()` + fcntl 导入）
- `tests/test_teams.py`（+3 测试：竞态复现 xfail、原子性验证、边界用例）

## 工具层落地（2026-08-05 完成）

延续 TDD：新增 `TaskClaimTool` 把原子 `claim()` 暴露给团队成员。

- `mewcode/tools/task_claim.py`：`TaskClaimTool(task_id)`，认领失败（已认领/关闭/不存在）返回 `is_error=True`
- `tool_filter.py`：`TaskClaim` 加入 `TEAMMATE_COORDINATION_TOOLS`，并在 `build_teammate_tools` 的协作工具列表注入
- 4 个新测试（认领成功 / 拒绝二次认领 / 常量注册 / teammate 注册表包含）全部 RED→GREEN
- `test_teams.py` 结果：51 passed, 1 xfailed，6 个失败为预先存在的环境问题（与本改动无关）

**剩余说明（诚实）**：`TaskUpdate` 仍是通用更新工具，理论上仍可直接覆写 assignee——但 `TaskClaimTool` 的 description 明确指导 agent"用 TaskClaim 认领，别用 TaskUpdate 改状态"，认领路径已从工具层收口到原子 API。
