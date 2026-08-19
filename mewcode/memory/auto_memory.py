# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import asyncio
import copy
import threading
import time
from pathlib import Path
from typing import Any

from mewcode.conversation import ConversationManager, Message
from mewcode.memory.layered import (
    list_memory_documents,
    render_memory_documents,
    select_relevant_memory_documents,
    write_legacy_memory_documents,
)

USER_MEMORIES_RELPATH = ".mewcode/memories.md"
PROJECT_MEMORIES_RELPATH = ".mewcode/memories.md"

MEMORY_EXTRACTION_PROMPT = """\
你是一个记忆提取助手。分析下面的对话，提取值得长期记忆的信息，更新 memories.md。

分类规则：
- **用户偏好**：用户的编码习惯和风格要求（如缩进、命名规范、语言偏好）
- **纠正反馈**：用户明确指出的错误和正确做法
- **项目知识**：当前项目的具体技术信息（技术栈、目录结构、部署方式）
- **参考资料**：外部链接和文档地址

规则：
1. 已有相同含义的条目不要重复添加
2. 没有值得记忆的内容，该分类下留空（不要写任何条目，不要写占位符）
3. 每条记忆用一行 `- ` 开头，必须是具体内容，不要用 `...` 占位
4. 输出完整的 memories.md 内容，包含所有四个分类标题

输出格式（严格遵守，没有内容的分类下不写任何条目）：
### 用户偏好
- 用户偏好简洁代码风格

### 纠正反馈

### 项目知识
- 项目使用 PostgreSQL 15

### 参考资料

不要输出任何其他内容，不要调用任何工具。"""

_USER_LEVEL_HEADERS = {"用户偏好", "纠正反馈"}
_PROJECT_LEVEL_HEADERS = {"项目知识", "参考资料"}


class MemoryManager:
    def __init__(self, project_root: str) -> None:
        self._user_path = Path.home() / USER_MEMORIES_RELPATH
        self._project_path = Path(project_root) / PROJECT_MEMORIES_RELPATH
        self._last_extraction_msg_count = 0
        self._extraction_lock = threading.Lock()
        self._io_lock = threading.RLock()


    @property
    def user_path(self) -> Path:
        return self._user_path


    @property
    def project_path(self) -> Path:
        return self._project_path

    @property
    def user_mem_dir(self) -> Path:
        """User-level memory directory (~/.mewcode/memory/).

        This is where .md memory files with frontmatter (type user/feedback)
        live. Distinct from ``user_path`` which points at the flat
        ``memories.md`` file.
        """
        return Path.home() / ".mewcode" / "memory"

    @property
    def project_mem_dir(self) -> Path:
        """Project-level memory directory (<project>/.mewcode/memory/).

        This is where .md memory files with frontmatter (type
        project/reference) live. Distinct from ``project_path`` which
        points at the flat ``memories.md`` file.
        """
        return self._project_path.parent / "memory"

    def load(self) -> str:
        with self._io_lock:
            sections: list[str] = []

            content = self._read_text(self._user_path)
            if content:
                sections.append(content)

            content = self._read_text(self._project_path)
            if content:
                sections.append(content)

            return "\n\n".join(sections)

    def load_relevant(self, query: str, top_k: int = 5) -> str:
        """Return task-relevant layered memories, with legacy fallback.

        New memory is stored as one Markdown file per memory under the user and
        project memory directories. If no layered document is relevant yet, fall
        back to the old flat memories.md files so existing users do not lose
        context before their memories are migrated.
        """
        query = query.strip()
        with self._io_lock:
            if query:
                docs = list_memory_documents(self.user_mem_dir, self.project_mem_dir)
                selected = select_relevant_memory_documents(query, docs, top_k=top_k)
                rendered = render_memory_documents(selected)
                if rendered:
                    return rendered
            return self.load()

    async def extract(
        self,
        client: Any,
        conversation: ConversationManager,
        protocol: str,
    ) -> None:
        if not self._extraction_lock.acquire(blocking=False):
            return
        try:
            await self._extract_once(client, conversation, protocol)
        finally:
            self._extraction_lock.release()

    async def _extract_once(
        self,
        client: Any,
        conversation: ConversationManager,
        protocol: str,
    ) -> None:
        from mewcode.tools.base import StreamEnd, TextDelta

        current_memories = self.load()

        recent = conversation.history[self._last_extraction_msg_count :]
        if not recent:
            return

        conv_lines: list[str] = []
        for msg in recent:
            if msg.role == "user" and msg.content:
                conv_lines.append(f"用户: {msg.content}")
            elif msg.role == "assistant" and msg.content:
                conv_lines.append(f"助手: {msg.content}")

        if not conv_lines:
            return

        prompt = (
            f"{MEMORY_EXTRACTION_PROMPT}\n\n"
            f"## 当前 memories.md\n"
            f"{current_memories if current_memories else '(空)'}\n\n"
            f"## 最近对话\n"
            f"{chr(10).join(conv_lines)}\n\n"
            f"请输出更新后的完整 memories.md 内容。"
        )

        extract_conv = ConversationManager()
        extract_conv.history = [Message(role="user", content=prompt)]

        collected = ""
        try:
            async for event in client.stream(
                extract_conv, system="你是一个记忆提取助手。"
            ):
                if isinstance(event, TextDelta):
                    collected += event.text
                elif isinstance(event, StreamEnd):
                    pass
        except Exception:
            return

        self._last_extraction_msg_count = len(conversation.history)

        collected = collected.strip()
        if not collected:
            return

        context_excerpt = "\n".join(conv_lines[-6:])[:1000]
        background_note = (
            "最近对话片段：\n" + context_excerpt if context_excerpt else ""
        )
        self._write_memories(collected, background_note=background_note)

    def schedule_fork_extraction(
        self,
        client: Any,
        conversation: ConversationManager,
        protocol: str,
        delay_seconds: float = 15.0,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> threading.Thread | None:
        """Run memory extraction in a delayed daemon thread.

        This mirrors the Claude-Code style end-of-conversation background fork:
        the foreground response is not blocked, a snapshot of the conversation is
        handed to a delayed background worker, and the existing extractor writes
        layered Markdown memories when it finishes. When a running event loop is
        supplied, the worker only handles the delay and schedules extraction back
        on that loop so async provider clients are not used from a different
        event loop.
        """
        if not conversation.history:
            return None

        snapshot = ConversationManager()
        snapshot.history = copy.deepcopy(conversation.history)
        snapshot.env_injected = conversation.env_injected
        snapshot.ltm_injected = conversation.ltm_injected
        snapshot.last_input_tokens = conversation.last_input_tokens
        snapshot.baseline_tokens = conversation.baseline_tokens
        snapshot.anchor_count = conversation.anchor_count

        def runner() -> None:
            try:
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                if loop is not None and loop.is_running():
                    loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(
                            self.extract(client, snapshot, protocol)
                        )
                    )
                    return
                asyncio.run(self.extract(client, snapshot, protocol))
            except Exception:
                return

        thread = threading.Thread(
            target=runner,
            name="mewcode-memory-fork",
            daemon=True,
        )
        thread.start()
        return thread

    def _write_memories(self, content: str, background_note: str = "") -> None:
        with self._io_lock:
            self._write_memories_locked(content, background_note=background_note)

    def _write_memories_locked(self, content: str, background_note: str = "") -> None:
        user_sections: list[str] = []
        project_sections: list[str] = []

        current_header = ""
        current_lines: list[str] = []

        for line in content.split("\n"):
            if line.startswith("### "):
                if current_header:
                    self._assign_section(
                        current_header, current_lines, user_sections, project_sections
                    )
                current_header = line
                current_lines = []
            else:
                current_lines.append(line)

        if current_header:
            self._assign_section(
                current_header, current_lines, user_sections, project_sections
            )

        if user_sections:
            self._write_text_atomic(
                self._user_path,
                "\n".join(user_sections).strip() + "\n",
            )

        if project_sections:
            self._write_text_atomic(
                self._project_path,
                "\n".join(project_sections).strip() + "\n",
            )

        write_legacy_memory_documents(
            content,
            user_mem_dir=self.user_mem_dir,
            project_mem_dir=self.project_mem_dir,
            source_context=background_note,
        )

    @staticmethod
    def _is_placeholder(line: str) -> bool:
        stripped = line.strip().lstrip("- ").strip()
        return stripped in {"", "...", "…", "无", "暂无", "N/A"}


    @staticmethod
    def _assign_section(
        header: str,
        lines: list[str],
        user_sections: list[str],
        project_sections: list[str],
    ) -> None:
        real_lines = [l for l in lines if l.strip().startswith("- ") and not MemoryManager._is_placeholder(l)]
        if not real_lines:
            return

        section_text = header + "\n" + "\n".join(real_lines)

        for keyword in _USER_LEVEL_HEADERS:
            if keyword in header:
                user_sections.append(section_text)
                return

        for keyword in _PROJECT_LEVEL_HEADERS:
            if keyword in header:
                project_sections.append(section_text)
                return


    def clear(self) -> None:
        with self._io_lock:
            if self._user_path.exists():
                self._write_text_atomic(self._user_path, "")
            if self._project_path.exists():
                self._write_text_atomic(self._project_path, "")
            for directory in (self.user_mem_dir, self.project_mem_dir):
                if not directory.is_dir():
                    continue
                for fp in directory.rglob("*.md"):
                    if fp.is_file():
                        try:
                            fp.unlink()
                        except OSError:
                            pass

    def get_display_text(self) -> str:
        with self._io_lock:
            parts: list[str] = []

            layered = list_memory_documents(self.user_mem_dir, self.project_mem_dir)
            if layered:
                lines = ["[分层记忆 Markdown]"]
                for doc in layered:
                    desc = f" - {doc.description}" if doc.description else ""
                    lines.append(f"{doc.scope}/{doc.type}: {doc.path}{desc}")
                parts.append("\n".join(lines))

            content = self._read_text(self._user_path)
            if content:
                parts.append(f"[用户级] {self._user_path}\n{content}")

            content = self._read_text(self._project_path)
            if content:
                parts.append(f"[项目级] {self._project_path}\n{content}")

            if not parts:
                return "当前没有任何自动记忆。"

            return "\n\n".join(parts)

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        return ""

    @staticmethod
    def _write_text_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(
            f".{path.name}.{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
