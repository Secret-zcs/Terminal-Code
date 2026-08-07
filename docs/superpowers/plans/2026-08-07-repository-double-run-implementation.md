# Repository Double-Run Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个可离线复现的 baseline/evolved 仓库双跑评测框架，用相同任务、模型、权限和测试命令，量化候选 Skill 对真实代码任务的影响。

**Architecture:** 每个 fixture 提供一个初始仓库、issue 和预期配置。Runner 将 fixture 复制为两个临时仓库，分别启动一个隔离到该仓库根目录的 Agent；只有 evolved 运行注入候选 Skill。Agent 结束后，Runner 在两个副本中运行相同测试命令、比较文件快照并生成逐案 JSON 与 Markdown 结果，不执行审批、promote 或正式 Skill 写入。

**Tech Stack:** Python 3.11+, `asyncio`, `dataclasses`, `pathlib`, `subprocess`, `pytest`, `pytest-asyncio`, 现有 `mewcode.agent.Agent`、权限系统和 `LLMClient`。

---

## 文件边界

- Create: `mewcode/tools/workspace.py`，统一解析 workspace-relative 路径和 Bash 工作目录。
- Modify: `mewcode/tools/read_file.py`、`write_file.py`、`edit_file.py`、`glob.py`、`grep.py`、`bash.py`，接受可选 workspace root；不改变普通 CLI 的默认行为。
- Modify: `mewcode/tools/__init__.py`，让 `create_default_registry(work_dir=repo_root)` 为默认工具传入仓库根目录。
- Create: `mewcode/evolution/repository_benchmark.py`，fixture 加载、双跑编排、测试执行、快照 diff、指标和报告渲染。
- Create: `tests/test_repository_double_run_benchmark.py`，覆盖路径隔离、fixture 校验、Fake Client 双跑和报告指标。
- Create: `fixtures/repository_double_run/calculator-zero/repository/calculator.py`，最小可运行修复仓库。
- Create: `fixtures/repository_double_run/calculator-zero/repository/tests/test_calculator.py`，包含既有回归测试和待修复测试。
- Create: `fixtures/repository_double_run/calculator-zero/issue.md`，提供给 Agent 的用户 issue。
- Create: `fixtures/repository_double_run/calculator-zero/expected.json`，测试命令、预期测试文件和文件范围约束。
- Create: `scripts/run_repository_double_run_benchmark.py`，本地 Fake/真实 provider 共用的命令行入口。
- Modify: `docs/self-evolution-development-progress-recap-zh.md`，追加本阶段实现、命令、指标定义和验证结果。
- Modify: `docs/self-evolution-config-approval-recap-zh.md`，追加“评测只读、不自动审批、不自动 promote”的配置边界说明。

## Task 1: 让默认工具绑定隔离仓库

**Files:**
- Create: `mewcode/tools/workspace.py`
- Modify: `mewcode/tools/read_file.py`
- Modify: `mewcode/tools/write_file.py`
- Modify: `mewcode/tools/edit_file.py`
- Modify: `mewcode/tools/glob.py`
- Modify: `mewcode/tools/grep.py`
- Modify: `mewcode/tools/bash.py`
- Modify: `mewcode/tools/__init__.py`
- Test: `tests/test_repository_double_run_benchmark.py`

- [ ] **Step 1: 写 workspace root 的失败测试**

在测试文件先加入以下测试辅助工具和行为测试。测试要求相对路径始终落在 `tmp_path / "repo"`，Bash 创建的文件也必须出现在该目录，而不是 pytest 进程当前目录。

```python
import asyncio
from pathlib import Path

import pytest

from mewcode.tools import create_default_registry


@pytest.mark.asyncio
async def test_default_registry_resolves_relative_paths_inside_workspace(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "input.txt").write_text("before\n", encoding="utf-8")
    registry = create_default_registry(work_dir=repo)

    read = await registry.get("ReadFile").execute(
        registry.get("ReadFile").params_model(file_path="input.txt")
    )
    assert not read.is_error
    assert "before" in read.output

    bash = await registry.get("Bash").execute(
        registry.get("Bash").params_model(command="printf after > output.txt")
    )
    assert not bash.is_error
    assert (repo / "output.txt").read_text(encoding="utf-8") == "after"
    assert not (Path.cwd() / "output.txt").exists()
```

- [ ] **Step 2: 运行红灯测试**

运行：

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py::test_default_registry_resolves_relative_paths_inside_workspace -q
```

预期：失败，因为 `create_default_registry` 尚不接受 `work_dir`，且现有 `Bash` 没有 workspace `cwd`。

- [ ] **Step 3: 添加统一路径解析器**

创建 `mewcode/tools/workspace.py`：

```python
from __future__ import annotations

from pathlib import Path


def resolve_workspace_path(root_dir: str | Path | None, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute() or root_dir is None:
        return path
    return Path(root_dir).resolve() / path


def resolve_command_cwd(root_dir: str | Path | None) -> str | None:
    return str(Path(root_dir).resolve()) if root_dir is not None else None
```

- [ ] **Step 4: 将路径解析器接入五个文件工具和 Bash**

每个工具的构造函数增加兼容的可选参数 `work_dir: str | Path | None = None`，并把所有 `Path(params.file_path)`、`Path(params.path)` 替换为 `resolve_workspace_path(self.work_dir, ...)`。`Bash` 保存同名属性，并将子进程创建改为：

```python
proc = await asyncio.create_subprocess_shell(
    params.command,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=resolve_command_cwd(self.work_dir),
)
```

`ReadFile`、`WriteFile`、`EditFile` 的返回文本继续使用模型传入的原始相对路径；内部 cache、state cache 和 file history 使用解析后的绝对路径。`Glob` 和 `Grep` 从解析后的 base 搜索，并继续返回相对于 base 的路径。

把注册函数签名改成：

```python
def create_default_registry(
    file_cache: FileCache | None = None,
    file_history: Any = None,
    work_dir: str | Path | None = None,
) -> ToolRegistry:
```

并将 `work_dir=work_dir` 传给 `ReadFile`、`WriteFile`、`EditFile`、`Glob`、`Grep` 和 `Bash`。保留现有调用方式，使 CLI 仍默认使用当前进程目录。

- [ ] **Step 5: 运行工具回归测试**

运行：

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py::test_default_registry_resolves_relative_paths_inside_workspace tests/test_agent.py -q
```

预期：新测试和现有 Agent 测试通过；若发现旧测试依赖进程 cwd，修正测试调用以显式传入 `work_dir`，不改变生产默认行为。

- [ ] **Step 6: 提交工具隔离改动**

```bash
git add mewcode/tools/workspace.py mewcode/tools/read_file.py mewcode/tools/write_file.py mewcode/tools/edit_file.py mewcode/tools/glob.py mewcode/tools/grep.py mewcode/tools/bash.py mewcode/tools/__init__.py tests/test_repository_double_run_benchmark.py
git commit -m "为仓库双跑工具增加工作区根目录"
```

## Task 2: 实现 fixture 格式和严格校验

**Files:**
- Create: `mewcode/evolution/repository_benchmark.py`
- Modify: `tests/test_repository_double_run_benchmark.py`

- [ ] **Step 1: 写 fixture loader 的失败测试**

```python
def test_load_repository_fixtures_reads_issue_and_expected(tmp_path: Path):
    case = tmp_path / "calculator-zero"
    (case / "repository").mkdir(parents=True)
    (case / "issue.md").write_text("Fix division by zero.", encoding="utf-8")
    (case / "expected.json").write_text(
        json.dumps({
            "test_command": "python -m pytest -q",
            "regression_command": "python -m pytest -q -k 'not divide_by_zero'",
            "expected_tests": ["tests/test_calculator.py"],
            "allowed_paths": ["calculator.py", "tests/test_calculator.py"],
            "forbidden_paths": [".mewcode/**", "secrets/**"],
        }),
        encoding="utf-8",
    )

    fixtures = load_repository_fixtures(tmp_path)

    assert fixtures[0].id == "calculator-zero"
    assert fixtures[0].issue == "Fix division by zero."
    assert fixtures[0].test_command == "python -m pytest -q"
    assert fixtures[0].regression_command.endswith("not divide_by_zero'")


def test_load_repository_fixtures_rejects_absolute_or_parent_paths(tmp_path: Path):
    case = tmp_path / "bad"
    (case / "repository").mkdir(parents=True)
    (case / "issue.md").write_text("task", encoding="utf-8")
    (case / "expected.json").write_text(
        json.dumps({
            "test_command": "true",
            "expected_tests": ["../outside.py"],
            "allowed_paths": ["/tmp/outside"],
            "forbidden_paths": [],
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="relative"):
        load_repository_fixtures(tmp_path)
```

- [ ] **Step 2: 运行红灯测试**

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py::test_load_repository_fixtures_reads_issue_and_expected tests/test_repository_double_run_benchmark.py::test_load_repository_fixtures_rejects_absolute_or_parent_paths -q
```

预期：失败，因为 loader 尚不存在。

- [ ] **Step 3: 定义 fixture 数据模型和 loader**

在 `repository_benchmark.py` 中实现如下稳定接口：

```python
@dataclass(frozen=True)
class RepositoryFixture:
    id: str
    repository: Path
    issue: str
    test_command: str
    regression_command: str
    expected_tests: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]


def load_repository_fixtures(root: str | Path) -> list[RepositoryFixture]:
    """Load sorted, offline repository fixtures and reject unsafe path metadata."""
```

loader 必须按目录名排序；每个 case 必须拥有 `repository/`、`issue.md` 和 `expected.json`；`test_command`、`expected_tests`、`allowed_paths` 不能为空；路径必须是相对 POSIX 路径，且不能包含 `..`；`regression_command` 缺省时使用空字符串。JSON 错误、字段类型错误和路径越界都转换为包含 case id 的 `ValueError`。

- [ ] **Step 4: 运行 fixture 测试并提交**

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py -k "fixture" -q
git add mewcode/evolution/repository_benchmark.py tests/test_repository_double_run_benchmark.py
git commit -m "增加仓库双跑 fixture 校验"
```

预期：fixture loader 测试全部通过。

## Task 3: 实现单次 Agent 执行和文件指标

**Files:**
- Modify: `mewcode/evolution/repository_benchmark.py`
- Modify: `tests/test_repository_double_run_benchmark.py`

- [ ] **Step 1: 写文件快照、命令执行和指标的失败测试**

测试构造初始文件和修改后文件，验证只把真实修改文件计入 diff，并排除 `.git`、`__pycache__` 和 `.pytest_cache`。同时验证非零测试退出码、超时和越界修改的分类字段，而不是把它们合并成普通失败。

```python
def test_snapshot_diff_counts_lines_and_out_of_scope_paths(tmp_path: Path):
    before = {"calculator.py": "return a / b\n", "README.md": "demo\n"}
    after = {"calculator.py": "return None if b == 0 else a / b\n", "README.md": "changed\n", "notes.md": "extra\n"}

    diff = compare_snapshots(before, after, ["calculator.py"])

    assert diff["changed_paths"] == ["README.md", "calculator.py", "notes.md"]
    assert diff["out_of_scope_changes"] == ["README.md", "notes.md"]
    assert diff["patch_size"]["added"] == 1
    assert diff["patch_size"]["removed"] == 1


def test_run_command_classifies_timeout(tmp_path: Path):
    result = run_local_command("python -c 'import time; time.sleep(1)'", tmp_path, 0.01)
    assert result["status"] == "timeout"
    assert result["exit_code"] is None
```

- [ ] **Step 2: 运行红灯测试**

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py::test_snapshot_diff_counts_lines_and_out_of_scope_paths tests/test_repository_double_run_benchmark.py::test_run_command_classifies_timeout -q
```

预期：失败，因为快照比较和命令分类函数尚未实现。

- [ ] **Step 3: 实现可审计的快照和本地命令执行**

实现以下接口：

```python
def snapshot_repository(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    ignored = {".git", ".pytest_cache", "__pycache__", ".mypy_cache"}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        files[path.relative_to(root).as_posix()] = path.read_text(
            encoding="utf-8", errors="replace"
        )
    return files


def compare_snapshots(
    before: dict[str, str],
    after: dict[str, str],
    allowed_paths: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    out_of_scope = [path for path in changed if not any(fnmatch(path, rule) for rule in allowed_paths)]
    return {
        "changed_paths": changed,
        "out_of_scope_changes": out_of_scope,
        "patch_size": count_unified_diff_lines(before, after, changed),
    }


def run_local_command(command: str, cwd: Path, timeout_seconds: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True, text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {"status": "timeout", "exit_code": None, "stdout": str(exc.stdout or ""), "stderr": str(exc.stderr or "")}
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-10000:],
        "stderr": completed.stderr[-10000:],
    }
```

`count_unified_diff_lines` 使用 `difflib.unified_diff` 统计新增、删除和总变更行数，并排除 `+++`、`---` 文件头。`compare_snapshots` 另接收 `forbidden_paths` 并返回 `forbidden_changes`；上面的测试固定验证 `allowed_paths` 过滤。`snapshot_repository` 只读取普通文件并使用 UTF-8 替换错误；结果键使用 POSIX 相对路径。

- [ ] **Step 4: 写单次 Agent runner 的失败测试**

定义 Fake Client：第一次调用 `ReadFile(calculator.py)`，第二次调用 `EditFile`，第三次调用 `Bash` 跑 fixture 的测试命令，第四次返回最终文本。测试调用 `run_repository_case`，并断言 Agent 的 workspace 是临时副本、源 fixture 没有变化、指标至少包含以下字段：

```python
assert result["tests_passed"] is True
assert result["regression_free"] is True
assert result["tool_call_count"] == 3
assert result["permission_denied"] == 0
assert result["rewind_used"] is False
assert result["changed_paths"] == ["calculator.py"]
```

- [ ] **Step 5: 运行红灯测试**

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py::test_run_repository_case_isolated_and_collects_metrics -q
```

预期：失败，因为单次 runner 尚未实现。

- [ ] **Step 6: 实现单次 runner**

使用以下接口，避免在当前进程调用 `os.chdir`：

```python
ClientFactory = Callable[[Path, bool], LLMClient]


async def run_repository_case(
    fixture: RepositoryFixture,
    repository_root: Path,
    *,
    evolved: bool,
    candidate_skill: str,
    client_factory: ClientFactory,
    protocol: str,
    max_iterations: int,
    test_timeout_seconds: float,
) -> dict[str, Any]:
    # 构造 checker、registry、Agent，执行 issue，再运行回归和目标命令。
    # 实际实现必须保持下方“函数流程固定为”段落中的调用顺序。
    raise NotImplementedError
```

函数流程固定为：读取初始快照；构造 `PermissionChecker`，其 `PathSandbox` 根目录为 `repository_root`、模式为 `PermissionMode.DONT_ASK`；调用 `create_default_registry(work_dir=repository_root)`；构造 `Agent(... work_dir=str(repository_root), permission_checker=checker)`；仅在 `evolved` 为真时调用 `agent.activate_skill("candidate", candidate_skill)`；调用 `await agent.run_to_completion(fixture.issue, event_callback=...)`；运行 `regression_command`（若存在）和 `test_command`；读取结束快照并计算 diff。

事件回调只记录 `tool_use` 数量。工具结果从 `conversation.history` 中统计：`content` 包含 `Permission denied` 或 `权限` 且对应错误时累加 `permission_denied`。`rewind_used` 通过执行期间是否出现 `rewind` 工具调用或结果中的 rewind 标记判断；当前 fixture 没有 rewind 时必须显式输出 `False`。provider 异常输出 `status="provider-failed"`，测试超时输出 `test_status="timeout"`，不能用另一侧结果补齐失败侧。

- [ ] **Step 7: 运行单次 runner 测试并提交**

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py::test_snapshot_diff_counts_lines_and_out_of_scope_paths tests/test_repository_double_run_benchmark.py::test_run_command_classifies_timeout tests/test_repository_double_run_benchmark.py::test_run_repository_case_isolated_and_collects_metrics -q
git add mewcode/evolution/repository_benchmark.py tests/test_repository_double_run_benchmark.py
git commit -m "实现隔离仓库单次评测指标"
```

## Task 4: 加入本地 fixture 和 baseline/evolved 双跑

**Files:**
- Create: `fixtures/repository_double_run/calculator-zero/repository/calculator.py`
- Create: `fixtures/repository_double_run/calculator-zero/repository/tests/test_calculator.py`
- Create: `fixtures/repository_double_run/calculator-zero/issue.md`
- Create: `fixtures/repository_double_run/calculator-zero/expected.json`
- Modify: `mewcode/evolution/repository_benchmark.py`
- Modify: `tests/test_repository_double_run_benchmark.py`

- [ ] **Step 1: 添加会失败的最小 fixture**

`calculator.py` 初始内容为：

```python
def divide(a: int, b: int) -> int | None:
    return a // b
```

`tests/test_calculator.py` 必须同时包含已通过的正常路径、预期失败的除零测试和回归测试标记：

```python
from calculator import divide


def test_divide_normal_case():
    assert divide(8, 2) == 4


def test_divide_by_zero_returns_none():
    assert divide(8, 0) is None


def test_divide_negative_numbers():
    assert divide(-8, 2) == -4
```

`issue.md` 只描述“修复 `divide` 在除数为 0 时抛出异常的问题，保持已有正常行为，并运行测试”。`expected.json` 使用：

```json
{
  "test_command": "python -m pytest -q",
  "regression_command": "python -m pytest -q -k 'not divide_by_zero'",
  "expected_tests": ["tests/test_calculator.py"],
  "allowed_paths": ["calculator.py", "tests/test_calculator.py"],
  "forbidden_paths": [".mewcode/**", "secrets/**"]
}
```

- [ ] **Step 2: 运行 fixture 初始失败测试**

```bash
(cd fixtures/repository_double_run/calculator-zero/repository && python -m pytest -q)
```

预期：`2 passed, 1 failed`，证明 fixture 确实要求代码修改；回归命令应通过。

- [ ] **Step 3: 写双跑汇总的失败测试**

新增稳定入口：

```python
async def run_repository_double_run_benchmark(
    client_factory: ClientFactory,
    fixture_root: str | Path,
    candidate_skill: str,
    *,
    workspace_root: str | Path | None = None,
    protocol: str = "anthropic",
    max_iterations: int = 20,
    test_timeout_seconds: float = 120.0,
    max_cases: int | None = None,
) -> dict[str, Any]: ...
```

测试 Fake Client 的 baseline/evolved 两次运行都能修复副本，并断言：`summary.case_count == 1`、`baseline.task_success is True`、`evolved.task_success is True`、`delta.tests_passed == 0`、源 fixture 中 `calculator.py` 仍为 `return a // b`。另外用一个只额外修改 `notes.md` 的 Fake Client，断言 `out_of_scope_changes == ["notes.md"]` 且 `task_success` 为假。

- [ ] **Step 4: 运行红灯测试**

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py::test_double_run_compares_baseline_and_evolved_without_mutating_fixture tests/test_repository_double_run_benchmark.py::test_double_run_marks_out_of_scope_change -q
```

预期：失败，因为双跑入口尚未实现。

- [ ] **Step 5: 实现临时目录双跑和聚合结果**

Runner 使用 `tempfile.TemporaryDirectory(dir=workspace_root)`，分别 `shutil.copytree(fixture.repository, temp / "baseline")` 和 `shutil.copytree(fixture.repository, temp / "evolved")`。同一 fixture 的两个 Agent 只允许差异是 `evolved` 是否激活候选 Skill；两边都使用同一 `protocol`、`max_iterations`、权限模式和测试超时。

每个 case 结果至少包含：

```python
{
    "id": fixture.id,
    "baseline": {"task_success": True, "tests_passed": True, "regression_free": True, "out_of_scope_changes": [], "patch_size": {"added": 1, "removed": 1, "total": 2}, "input_tokens": 0, "output_tokens": 0, "elapsed_seconds": 0.0, "tool_call_count": 0, "permission_denied": 0, "rewind_used": False},
    "evolved": {"task_success": True, "tests_passed": True, "regression_free": True, "out_of_scope_changes": [], "patch_size": {"added": 1, "removed": 1, "total": 2}, "input_tokens": 0, "output_tokens": 0, "elapsed_seconds": 0.0, "tool_call_count": 0, "permission_denied": 0, "rewind_used": False},
    "delta": {"tests_passed": 0, "task_success": 0, "patch_size_total": 0, "elapsed_seconds": 0.0}
}
```

汇总字段包括 `case_count`、`baseline_success`、`evolved_success`、`evolved_regression_free`、`provider_failed`、`test_timeouts`、`out_of_scope_case_count`、两侧 token 总数和平均耗时。`task_success` 定义为测试命令通过、预期测试文件存在、无 forbidden path 修改且无 out-of-scope 修改。

- [ ] **Step 6: 运行双跑测试并提交 fixture**

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py -q
git add fixtures/repository_double_run/calculator-zero mewcode/evolution/repository_benchmark.py tests/test_repository_double_run_benchmark.py
git commit -m "增加 baseline evolved 仓库双跑评测"
```

## Task 5: 生成 JSON/Markdown 报告和命令行入口

**Files:**
- Modify: `mewcode/evolution/repository_benchmark.py`
- Create: `scripts/run_repository_double_run_benchmark.py`
- Modify: `tests/test_repository_double_run_benchmark.py`

- [ ] **Step 1: 写报告渲染失败测试**

```python
def test_render_repository_benchmark_markdown_exposes_side_by_side_metrics():
    report = render_repository_benchmark_markdown({
        "summary": {"case_count": 1, "baseline_success": 0, "evolved_success": 1, "provider_failed": 0},
        "cases": [{"id": "calculator-zero", "baseline": {"task_success": False, "tests_passed": False}, "evolved": {"task_success": True, "tests_passed": True}, "delta": {"task_success": 1}}],
        "configuration": {"automatic_promotion": False},
    })
    assert "Baseline" in report and "Evolved" in report
    assert "calculator-zero" in report
    assert "automatic_promotion: false" in report
```

- [ ] **Step 2: 运行红灯测试**

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py::test_render_repository_benchmark_markdown_exposes_side_by_side_metrics -q
```

预期：失败，因为报告渲染函数尚未实现。

- [ ] **Step 3: 实现 Markdown renderer 和 CLI**

在 `repository_benchmark.py` 中实现：

```python
def render_repository_benchmark_markdown(result: dict[str, Any]) -> str: ...
```

报告必须包含方法、fixture 来源、候选 Skill 是否注入、baseline/evolved 并列表格、每案测试输出摘要、diff 文件、失败分类、token/耗时/工具调用/权限拒绝/rewind 指标、限制和“不会自动审批或 promote”的声明。原始 JSON 结果保持机器可读，Markdown 只做摘要，不丢失失败详情。

脚本 `scripts/run_repository_double_run_benchmark.py` 提供这些参数：

```text
--fixtures fixtures/repository_double_run
--candidate-skill PATH
--config PATH
--provider-index 0
--workspace-root PATH
--max-cases N
--max-iterations N
--test-timeout SECONDS
--json-output PATH
--md-output PATH
```

脚本读取候选 Skill 文本；读取配置并通过 `create_client(provider)` 创建每次运行所需的 client；`client_factory` 返回独立 provider client，避免两侧共享请求状态。未提供 `--candidate-skill` 时使用 `DEFAULT_EVOLVED_SKILL`，并在报告中标记这是默认演示 SOP。配置错误、provider 初始化失败和 fixture 校验失败都以非零退出码结束。

- [ ] **Step 4: 运行报告和 CLI 测试**

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py -q
PYTHONPATH=. python scripts/run_repository_double_run_benchmark.py --help
```

预期：测试通过；帮助文本包含所有参数，且不触发 provider 初始化。

- [ ] **Step 5: 提交报告能力**

```bash
git add mewcode/evolution/repository_benchmark.py scripts/run_repository_double_run_benchmark.py tests/test_repository_double_run_benchmark.py
git commit -m "增加仓库双跑结果报告脚本"
```

## Task 6: 完成阶段性验证和文档留档

**Files:**
- Modify: `docs/self-evolution-development-progress-recap-zh.md`
- Modify: `docs/self-evolution-config-approval-recap-zh.md`

- [ ] **Step 1: 运行 Fake Client 全流程**

```bash
PYTHONPATH=. pytest tests/test_repository_double_run_benchmark.py -q
```

预期：本地 fixture 双跑、隔离、失败分类、指标和 Markdown 报告测试全部通过；该结果只证明 runner 正确，不作为真实模型效果结论。

- [ ] **Step 2: 运行聚合回归测试**

```bash
PYTHONPATH=. pytest tests/test_evolution.py tests/test_self_evolution_benchmark.py tests/test_proposer_benchmark.py tests/test_agent.py -q
python3 -m py_compile mewcode/tools/workspace.py mewcode/evolution/repository_benchmark.py scripts/run_repository_double_run_benchmark.py
git diff --check
```

记录实际输出；已有与当前 read-before-edit 安全策略冲突的旧测试必须单独列为遗留失败，不得把它写成双跑框架失败。

- [ ] **Step 3: 追加中文复盘记录**

在 `docs/self-evolution-development-progress-recap-zh.md` 追加日期为 `2026-08-07` 的章节，说明：

```text
本阶段新增 repository double-run benchmark。每个 fixture 被复制为 baseline/evolved 两个临时仓库；只有 evolved 注入候选 Skill。评测记录 task_success、tests_passed、regression_free、out_of_scope_changes、patch_size、token、耗时、tool calls、permission denials 和 rewind usage。Fake Client 只验证编排和隔离，不能证明模型能力；真实 provider 结果必须标注模型、provider、revision、运行次数和失败分类。
```

在 `docs/self-evolution-config-approval-recap-zh.md` 追加说明：仓库双跑是只读评测，自动自进化仍只产生候选和报告；无论审批模式如何配置，benchmark 都不会自动提交审批申请、promote 或修改正式 `.mewcode/skills/`。

- [ ] **Step 4: 提交文档留档**

```bash
git add docs/self-evolution-development-progress-recap-zh.md docs/self-evolution-config-approval-recap-zh.md
git commit -m "留档仓库双跑自进化评测"
```

## Task 7: 按设计顺序运行真实 provider 评测

**Files:**
- Modify only generated outputs under `.mewcode/` or an explicitly selected report path outside tracked source files.

- [ ] **Step 1: Fake Client 跑单 fixture**

```bash
PYTHONPATH=. python scripts/run_repository_double_run_benchmark.py --fixtures fixtures/repository_double_run --candidate-skill /tmp/candidate-skill.md --json-output /tmp/repository-double-run-fake.json --md-output /tmp/repository-double-run-fake.md
```

CLI 只负责真实 provider 运行；Fake Client 通过 `run_repository_double_run_benchmark()` 的 Python API 在 pytest 中执行。Fake Client 的完整结果以 pytest 产物和测试断言留档，不伪造真实 provider 报告。

- [ ] **Step 2: 使用已配置 provider 跑一个真实 fixture**

```bash
PYTHONPATH=. python scripts/run_repository_double_run_benchmark.py --fixtures fixtures/repository_double_run --max-cases 1 --config ~/.mewcode/config.yaml --json-output /tmp/repository-double-run-real-1.json --md-output /tmp/repository-double-run-real-1.md
```

报告记录 provider 协议、模型、运行时间和 token；API key 只从现有配置或环境变量读取，不写入结果。

- [ ] **Step 3: 扩展到三个 fixture 或明确记录 fixture 不足**

当前第一阶段只有一个离线 fixture，因此在没有新增 fixture 前不能声称“3 个真实 fixture 已完成”。真实 provider 扩展前应补充至少两个独立任务族 fixture，并重复同一命令，分别保存原始 JSON 和 Markdown。

- [ ] **Step 4: 记录结果，不自动应用候选 Skill**

结果文档必须说明：双跑只评测 Skill 的增益和副作用；`approval-ready`、`evolved_success` 和 `regression_free` 都不等于用户批准；任何候选 Skill 仍必须遵循现有评测门禁和审批状态机。

## 自审清单

- 设计文档中的所有要求均有对应任务：fixture、隔离副本、相同参数、候选 Skill 单变量注入、测试/diff/轨迹指标、Fake 到真实 provider 的分阶段验证、固定 revision 公开 issue 的后续入口。
- 所有代码接口在任务间保持一致：`RepositoryFixture`、`ClientFactory`、`run_repository_case`、`run_repository_double_run_benchmark` 和 `render_repository_benchmark_markdown` 的参数名称不变。
- 计划不修改自动审批、promote 或正式 Skill loader；生成结果只写临时目录或用户明确指定的报告路径。
- 计划不使用全局 `os.chdir`；所有文件工具和 Bash 都通过 `work_dir` 绑定隔离仓库。
- 当前工作区中已存在的 `interview-mock-transcript.md`、`mewcode/agents/tool_filter.py`、`mewcode/teams/shared_task.py`、`tests/test_teams.py`、`docs/qa/*` 和 `mewcode/tools/task_claim.py` 不属于本计划，实施时不得覆盖或回滚。
