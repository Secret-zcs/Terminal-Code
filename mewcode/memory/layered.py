# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


ENTRYPOINT_NAME = "MEMORY.md"
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

USER_LEVEL_HEADERS = {"用户偏好", "纠正反馈"}
PROJECT_LEVEL_HEADERS = {"项目知识", "参考资料"}


@dataclass
class MemoryDocument:
    path: Path
    scope: str
    type: str
    name: str
    description: str
    background: str
    content: str
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    mtime_ms: int = 0

    @property
    def filename(self) -> str:
        return self.path.name


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        return value[1:-1]
    return value


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        colon = line.find(":")
        if colon < 0:
            continue
        key = line[:colon].strip()
        value = _strip_quotes(line[colon + 1 :])
        if key:
            meta[key] = value
    return meta, content[match.end() :]


def _parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [part.strip().strip('"\'') for part in raw.split(",") if part.strip()]


def _parse_sections(body: str) -> dict[str, str]:
    matches = list(HEADING_RE.finditer(body))
    if not matches:
        return {"content": body.strip()}

    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[title] = body[start:end].strip()
    return sections


def parse_memory_document(path: Path, scope: str = "") -> MemoryDocument | None:
    try:
        raw = path.read_text(encoding="utf-8")
        stat = path.stat()
    except OSError:
        return None

    meta, body = parse_frontmatter(raw)
    sections = _parse_sections(body)
    content = (
        sections.get("记忆内容")
        or sections.get("内容")
        or sections.get("content")
        or sections.get("Content")
        or sections.get("content", "")
    )
    if not content:
        content = sections.get("content", body.strip())

    background = (
        sections.get("背景")
        or sections.get("产生背景")
        or sections.get("background")
        or sections.get("Background")
        or meta.get("background", "")
    )
    doc_scope = meta.get("scope") or scope
    doc_type = meta.get("type", "")
    name = meta.get("name") or path.stem
    description = meta.get("description", "")

    return MemoryDocument(
        path=path,
        scope=doc_scope,
        type=doc_type,
        name=name,
        description=description,
        background=background,
        content=content.strip(),
        tags=_parse_tags(meta.get("tags", "")),
        created_at=meta.get("created_at", ""),
        updated_at=meta.get("updated_at", ""),
        mtime_ms=int(stat.st_mtime * 1000),
    )


def list_memory_documents(
    user_mem_dir: Path | None,
    project_mem_dir: Path | None,
    limit: int = 200,
) -> list[MemoryDocument]:
    docs: list[MemoryDocument] = []
    for scope, directory in (("user", user_mem_dir), ("project", project_mem_dir)):
        if directory is None or not directory.is_dir():
            continue
        try:
            files = [fp for fp in directory.rglob("*.md") if fp.is_file()]
        except OSError:
            continue
        for fp in files:
            if fp.name == ENTRYPOINT_NAME:
                continue
            doc = parse_memory_document(fp, scope=scope)
            if doc is not None:
                # Directory scope is the isolation boundary. A project-scoped
                # memory misplaced under the global user directory must not
                # leak into other projects.
                if doc.scope and doc.scope != scope:
                    continue
                docs.append(doc)
    docs.sort(key=lambda doc: doc.mtime_ms, reverse=True)
    return docs[:limit]


def _slugify(value: str, fallback: str = "memory") -> str:
    lowered = value.lower()
    parts = re.findall(r"[a-z0-9]+", lowered)
    slug = "-".join(parts)[:48].strip("-")
    return slug or fallback


def _digest_for(*parts: str) -> str:
    raw = "\0".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:10]


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def format_memory_document(
    *,
    name: str,
    description: str,
    background: str,
    content: str,
    memory_type: str,
    scope: str,
    tags: list[str] | None = None,
    source_session: str = "",
    now: str | None = None,
) -> str:
    ts = now or _now_iso()
    clean_tags = tags or []
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        f"type: {memory_type}",
        f"scope: {scope}",
        f"tags: [{', '.join(clean_tags)}]",
        f"created_at: {ts}",
        f"updated_at: {ts}",
    ]
    if source_session:
        lines.append(f"source_session: {source_session}")
    lines.extend([
        "---",
        "",
        "## 背景",
        background.strip(),
        "",
        "## 记忆内容",
        content.strip(),
        "",
        "## 适用场景",
        "当用户任务与本记忆的名称、描述、标签或内容相关时注入。",
        "",
    ])
    return "\n".join(lines)


def write_memory_document(
    directory: Path,
    *,
    name: str,
    description: str,
    background: str,
    content: str,
    memory_type: str,
    scope: str,
    tags: list[str] | None = None,
    source_session: str = "",
    now: str | None = None,
) -> Path | None:
    name = " ".join(name.split())[:80] or "自动记忆"
    description = " ".join(description.split())[:240]
    content = content.strip()
    if not content:
        return None

    digest = _digest_for(scope, memory_type, name, description, content)
    slug = _slugify(name, fallback=memory_type or "memory")
    path = directory / f"{memory_type or 'memory'}-{slug}-{digest}.md"
    body = format_memory_document(
        name=name,
        description=description or content[:160],
        background=background,
        content=content,
        memory_type=memory_type,
        scope=scope,
        tags=tags,
        source_session=source_session,
        now=now,
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            _atomic_write_text(path, body)
        return path
    except OSError:
        return None


def write_legacy_memory_documents(
    content: str,
    *,
    user_mem_dir: Path,
    project_mem_dir: Path,
    source_session: str = "",
    source_context: str = "",
    now: str | None = None,
) -> list[Path]:
    written: list[Path] = []
    current_header = ""
    current_lines: list[str] = []

    def flush() -> None:
        if not current_header:
            return
        header_name = current_header.removeprefix("### ").strip()
        scope, memory_type, tags, directory = _classify_legacy_header(
            header_name, user_mem_dir, project_mem_dir
        )
        if directory is None:
            return
        for line in current_lines:
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            item = stripped[2:].strip()
            if item in {"", "...", "…", "无", "暂无", "N/A"}:
                continue
            background = f"从对话自动提取，原始分类：{header_name}。"
            if source_context:
                background = f"{background}\n\n{source_context.strip()}"
            path = write_memory_document(
                directory,
                name=item[:60],
                description=item,
                background=background,
                content=item,
                memory_type=memory_type,
                scope=scope,
                tags=tags,
                source_session=source_session,
                now=now,
            )
            if path is not None:
                written.append(path)

    for line in content.splitlines():
        if line.startswith("### "):
            flush()
            current_header = line
            current_lines = []
        else:
            current_lines.append(line)
    flush()
    return written


def _classify_legacy_header(
    header: str,
    user_mem_dir: Path,
    project_mem_dir: Path,
) -> tuple[str, str, list[str], Path | None]:
    if "用户偏好" in header:
        return "user", "user", ["preference"], user_mem_dir
    if "纠正反馈" in header:
        return "user", "feedback", ["feedback"], user_mem_dir
    if "项目知识" in header:
        return "project", "project", ["project"], project_mem_dir
    if "参考资料" in header:
        return "project", "reference", ["reference"], project_mem_dir
    return "", "", [], None


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    cjk = re.findall(r"[\u4e00-\u9fff]", lowered)
    if len(cjk) == 1:
        tokens.add(cjk[0])
    else:
        tokens.update("".join(cjk[i : i + 2]) for i in range(len(cjk) - 1))
    return tokens


def _score(query_tokens: set[str], doc: MemoryDocument) -> int:
    if not query_tokens:
        return 0
    weighted_fields = [
        (8, " ".join(doc.tags)),
        (6, doc.name),
        (5, doc.description),
        (3, doc.background),
        (2, doc.content),
    ]
    score = 0
    for weight, text in weighted_fields:
        score += weight * len(query_tokens & _tokens(text))
    if score > 0 and doc.scope == "project":
        score += 1
    return score


def select_relevant_memory_documents(
    query: str,
    documents: list[MemoryDocument],
    top_k: int = 5,
) -> list[MemoryDocument]:
    query_tokens = _tokens(query)
    scored = [(_score(query_tokens, doc), doc.mtime_ms, doc) for doc in documents]
    selected = [item for item in scored if item[0] > 0]
    selected.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [doc for _, _, doc in selected[:top_k]]


def render_memory_documents(documents: list[MemoryDocument]) -> str:
    if not documents:
        return ""
    parts = [
        "以下是根据当前任务选择出的长期记忆。只在高度相关时使用，"
        "且涉及代码事实时需要以当前代码为准。"
    ]
    for doc in documents:
        label = doc.name or doc.filename
        meta = []
        if doc.scope:
            meta.append(f"scope={doc.scope}")
        if doc.type:
            meta.append(f"type={doc.type}")
        if doc.tags:
            meta.append("tags=" + ",".join(doc.tags))
        parts.append(f"## {label}")
        if meta:
            parts.append("- " + "; ".join(meta))
        if doc.description:
            parts.append(f"- 描述：{doc.description}")
        if doc.background:
            parts.append(f"- 产生背景：{doc.background}")
        if doc.content:
            parts.append("- 记忆内容：" + doc.content)
    return "\n".join(parts).strip()
