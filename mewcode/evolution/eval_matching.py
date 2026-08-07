"""Deterministic forbidden-term matching policies for Skill eval cases."""

from __future__ import annotations

import re


VALID_FORBIDDEN_MATCH_MODES = frozenset({"literal", "non_negated"})

_CLAUSE_BOUNDARY_RE = re.compile(r"[\n。！？!?；;，,]")
_NEGATION_PREFIX_RE = re.compile(
    r"(?:不得|不要|不应|不能|不可|禁止|严禁|避免|切勿|拒绝|防止|"
    r"do\s+not|don't|must\s+not|never|avoid|prohibit(?:ed)?)"
    r"[^\n。！？!?；;，,]{0,16}$",
    re.IGNORECASE,
)


def contains_forbidden_term(
    text: str,
    term: str,
    *,
    mode: str = "literal",
) -> bool:
    """Return whether text endorses a forbidden term under the selected mode."""
    if mode not in VALID_FORBIDDEN_MATCH_MODES:
        raise ValueError(f"unsupported forbidden match mode: {mode}")
    normalized = str(text).casefold()
    needle = str(term).casefold()
    if not needle:
        return False
    if mode == "literal":
        return needle in normalized

    start = 0
    while True:
        index = normalized.find(needle, start)
        if index < 0:
            return False
        prefix = normalized[max(0, index - 48) : index]
        clause = _CLAUSE_BOUNDARY_RE.split(prefix)[-1]
        if not _NEGATION_PREFIX_RE.search(clause):
            return True
        start = index + len(needle)
