# ch15：Git Worktree 隔离 — `worktree/`

> 文件：`manager.py`(313行), `setup.py`, `cleanup.py`

---

## 一、创建

```python
async def create(self, name, base_branch="HEAD"):
    # ① 名称校验 + 防重名
    # ② 快速恢复：读 HEAD SHA（不启动 git 子进程，毫秒级）
    head_sha = self.read_worktree_head_sha(wt_path)
    if head_sha:
        return Worktree(...)     # 复用已有 worktree
    # ③ git worktree add -B branch path base
    # ④ 创建后设置
    perform_post_creation_setup(...)
```

**快速恢复**：`read_worktree_head_sha()` 直接读磁盘文件（.git → commondir → HEAD → refs/heads/xxx → SHA），不启动 git 子进程，毫秒级。

---

## 二、创建后设置

```python
def perform_post_creation_setup(repo_root, wt_path, symlink_directories):
    _copy_local_configs(...)       # 复制 settings.local.json, .env
    _setup_git_hooks(...)          # 设置 core.hooksPath
    _create_symlinks(...)          # node_modules/.venv → 符号链接（省空间）
    _copy_ignored_files(...)       # .worktreeinclude 匹配的被忽略文件
```

---

## 三、退出保护

```python
if action == "remove" and not discard_changes:
    changes = count_worktree_changes(wt.path, wt.head_commit)
    if changes.uncommitted > 0 or changes.new_commits > 0:
        raise WorktreeError("有未提交改动，设discard_changes=True强制删除")
```

---

## 四、自动清理

每小时运行一次，24 小时以上的临时 worktree 自动删除。条件：匹配临时命名 + 非当前使用 + 无未提交改动 + 无未推送 commit。
