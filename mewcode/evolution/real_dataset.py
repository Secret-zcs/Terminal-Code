"""Reproducible, privacy-preserving derivation from public conversation data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


OASST1_DATASET = "OpenAssistant/oasst1"
OASST1_REVISION = "fdf72ae0827c1cda404aff25b6603abec9e3399b"
OASST1_SOURCE_URL = "https://huggingface.co/datasets/OpenAssistant/oasst1"

_CODE_TERMS = (
    "code",
    "python",
    "test",
    "bug",
    "error",
    "function",
    "script",
    "代码",
    "测试",
    "错误",
    "函数",
)
_FEEDBACK_TERMS = (
    "wrong",
    "incorrect",
    "still fails",
    "doesn't work",
    "does not work",
    "please correct",
    "should be",
    "不对",
    "错误",
    "还是失败",
    "请修正",
    "应该",
)


def derive_oasst1_cases(
    rows: Iterable[dict[str, Any]],
    *,
    revision: str = OASST1_REVISION,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Derive benchmark cases without retaining conversation content.

    The source rows are treated as untrusted input. Only task-family signals and
    aggregate counts leave this function; raw text, user IDs, and message IDs are
    never copied into the derived JSONL.
    """
    by_id: dict[str, dict[str, Any]] = {}
    by_tree: dict[str, list[dict[str, Any]]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        message_id = str(raw.get("message_id", "")).strip()
        tree_id = str(raw.get("message_tree_id", "")).strip()
        if not message_id or not tree_id:
            continue
        by_id[message_id] = raw
        by_tree.setdefault(tree_id, []).append(raw)

    cases: list[dict[str, Any]] = []
    for tree_id, tree_rows in sorted(by_tree.items()):
        candidate: tuple[list[dict[str, Any]], bool, bool] | None = None
        for row in tree_rows:
            if str(row.get("role", "")).strip() != "assistant":
                continue
            chain = _message_chain(row, by_id)
            texts = [str(item.get("text", "")) for item in chain]
            code_signal = _contains_term(texts, _CODE_TERMS)
            follow_up_signal = any(
                str(item.get("role", "")).strip() in {"prompter", "user"}
                and index > 0
                for index, item in enumerate(chain)
            )
            explicit_correction_signal = any(
                str(item.get("role", "")).strip() in {"prompter", "user"}
                and index > 0
                and _contains_term([str(item.get("text", ""))], _FEEDBACK_TERMS)
                for index, item in enumerate(chain)
            )
            if len(chain) < 3 or not code_signal or not follow_up_signal:
                continue
            if candidate is None or len(chain) > len(candidate[0]):
                candidate = (chain, code_signal, explicit_correction_signal)
        if candidate is None:
            continue
        chain, code_signal, explicit_correction_signal = candidate
        language = _dominant_language(chain)
        case_id = "oasst1-" + hashlib.sha256(
            f"{revision}:{tree_id}".encode("utf-8")
        ).hexdigest()[:12]
        cases.append({
            "id": case_id,
            "source": "OASST1",
            "source_url": OASST1_SOURCE_URL,
            "source_revision": revision,
            "license": "Apache-2.0",
            "task_family": (
                "code-task-with-correction"
                if explicit_correction_signal
                else "code-task-with-follow-up"
            ),
            "task": (
                "Derived from a real multi-turn code conversation with user correction; "
                "generate a narrow skill that records feedback, verifies the patch, "
                "and reports evidence."
            ),
            "required_terms": [
                "阅读相关文件",
                "记录用户反馈",
                "回归测试",
                "验证报告",
            ],
            "forbidden_terms": ["跳过测试", "盲目重试"],
            "turn_count": len(chain),
            "language": language,
            "code_signal": code_signal,
            "feedback_signal": True,
            "explicit_correction_signal": explicit_correction_signal,
            "notes": (
                "Derived metadata only; raw conversation text, user IDs, and message "
                "IDs are intentionally excluded."
            ),
        })
        if len(cases) >= max(0, limit):
            break
    return cases


def build_source_manifest(
    raw_path: str | Path,
    *,
    dataset: str,
    split: str,
    revision: str,
    row_count: int,
) -> dict[str, Any]:
    path = Path(raw_path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "dataset": dataset,
        "split": split,
        "revision": revision,
        "sha256": digest.hexdigest(),
        "row_count": int(row_count),
        "raw_path": str(path),
        "license": "Apache-2.0",
        "source_url": OASST1_SOURCE_URL,
    }


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _message_chain(row: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    current = row
    seen: set[str] = set()
    while isinstance(current, dict):
        message_id = str(current.get("message_id", "")).strip()
        if not message_id or message_id in seen:
            break
        seen.add(message_id)
        chain.append(current)
        parent_id = str(current.get("parent_id", "") or "").strip()
        if not parent_id:
            break
        current = by_id.get(parent_id, {})
    chain.reverse()
    return chain


def _contains_term(texts: Iterable[str], terms: Iterable[str]) -> bool:
    normalized = "\n".join(texts).casefold()
    return any(term.casefold() in normalized for term in terms)


def _dominant_language(chain: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for row in chain:
        language = str(row.get("lang", "unknown")).strip() or "unknown"
        counts[language] = counts.get(language, 0) + 1
    return max(counts, key=counts.get) if counts else "unknown"
