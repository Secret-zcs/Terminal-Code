# Repository Double-Run Evaluation Design

日期：2026-08-07

## 目标

比较 baseline Agent 与加载候选 Skill 的 evolved Agent 在同一个代码仓库任务上的真实表现。第一阶段使用本地可复现 fixture 验证评测框架；第二阶段再接入固定 revision 的公开 GitHub issue。

本评测只衡量候选 Skill 对任务执行的影响，不会自动批准、应用或提交候选 Skill。

## 方案

每个 case 提供一个初始仓库、issue 描述、测试命令和预期行为。Runner 为同一个 case 创建两个隔离副本：`baseline/` 和 `evolved/`。两次运行使用相同 provider、模型、权限模式、最大轮数和测试命令，唯一变量是是否注入候选 Skill。

```text
case fixture
  -> baseline repository copy -> baseline Agent -> test command
  -> evolved repository copy  -> evolved Agent  -> test command
```

候选 Skill 必须先通过现有 schema、static policy、Eval Case Gate 和 3 轮 execution eval，才能进入 evolved 双跑。候选失败时记录失败原因，不使用它替换 baseline。

## Fixture 格式

```text
fixtures/repository_double_run/<case-id>/
  repository/       初始代码和测试
  issue.md          用户任务描述
  expected.json     预期文件、测试命令和修改范围
```

`expected.json` 至少包含：`test_command`、`expected_tests`、`allowed_paths`、`forbidden_paths`。fixture 必须能在本机离线运行，初始化和测试不能访问网络。

第一批 fixture 覆盖：回归 bug 最小修复、缺失测试补充、工具失败后的有限恢复、checkpoint/rewind 安全和跨文件补丁。

## Agent 执行

Fake Client 阶段使用脚本化响应验证 runner、权限、文件隔离和指标计算，不作为模型效果结论。真实阶段使用已配置的 provider；baseline 使用通用编码 SOP，evolved 使用通过门禁的候选 Skill。每次执行都限制在对应副本目录，禁止写入正式项目目录和正式 Skill 目录。

## 指标

- `task_success`：任务预期行为和测试是否满足。
- `tests_passed`：目标测试及回归测试退出码是否为 0。
- `regression_free`：任务前后已有测试是否保持通过。
- `out_of_scope_changes`：是否修改允许路径之外的文件。
- `patch_size`：新增、删除和修改行数。
- `input_tokens`、`output_tokens`：模型调用成本。
- `elapsed_seconds`：从 Agent 启动到测试结束的耗时。
- `tool_call_count`、`permission_denied`、`rewind_used`：执行轨迹和安全行为。

报告必须同时保存 baseline/evolved 原始结果、diff 摘要、测试输出摘要、失败分类和运行配置。`approval-ready` 不等于任务成功，也不等于用户批准。

## 隔离与失败策略

每个副本使用独立临时目录；测试命令超时、网络访问、越界写入、权限拒绝和 provider 错误分别统计。baseline 与 evolved 任一侧失败都保留另一侧结果，不能用一侧结果填充另一侧。真实仓库阶段固定 commit/revision，并记录来源、许可证和校验值。

## 验证顺序

1. Fake Client 跑 1 个 fixture，验证 runner 和隔离。
2. Fake Client 扩展到全部本地 fixture，验证汇总指标。
3. 真实 provider 跑 1 个 fixture，确认调用成本和输出稳定。
4. 真实 provider 扩展到 3 个 fixture，检查 baseline/evolved 差异。
5. 通过后再接入固定 revision 的公开 issue。

## 非目标

本阶段不实现自动 Skill promote，不修改项目代码、不修改工具权限、不替用户决定审批，也不把关键词覆盖率当作真实代码修复成功率。
