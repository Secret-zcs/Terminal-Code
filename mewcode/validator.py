# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
"""MewCode 的配置校验逻辑。"""

from __future__ import annotations

VALID_PROTOCOLS = {"anthropic", "openai", "openai-compat"}

VALID_PERMISSION_MODES = {
    "default",
    "acceptEdits",
    "plan",
    "bypassPermissions",
    "custom",
    "dontAsk",
}

VALID_TEAMMATE_MODES = {"", "in-process"}

VALID_SELF_EVOLUTION_APPROVAL_MODES = {"manual", "deferred", "trusted-auto"}
VALID_SELF_EVOLUTION_ROLLBACK_EVENTS = {"failure", "user_feedback"}

DEFAULT_CONTEXT_WINDOW = 200_000

# 内置的"模型名子串 -> context window（最大输入 token 数）"映射表，
# 是 context window 回退链的第 3 层（见 ProviderConfig.get_context_window）。
# 按从最具体到最通用排序，第一个子串命中即生效。值仅为合理起始点，
# 模型更新/重命名后可能过时。如果值不准确，在配置中设置 context_window 覆盖（最高优先级）。
MODEL_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    ("1m", 1_000_000),       # 也覆盖 "-1m" 后缀（如 claude-...-1m）
    ("gpt-4.1", 1_000_000),  # GPT-4.1 系列的 window 为 1M
    ("gpt-4o", 128_000),
    ("gpt-4-turbo", 128_000),
    ("o1", 200_000),         # OpenAI 推理模型 o1 / o3 / o4
    ("o3", 200_000),
    ("o4", 200_000),
    ("gpt-3.5", 16_385),
    ("claude", 200_000),
]


def lookup_model_context_window(model: str) -> int:
    """通过子串匹配（第 3 层），返回内置映射表中该模型对应的
    context window；没有匹配则返回 0。"""
    m = model.lower()
    for substr, window in MODEL_CONTEXT_WINDOWS:
        if substr in m:
            return window
    return 0


class ConfigError(Exception):
    pass


def validate_providers(
    raw_providers: list | dict,
    default_name: str | None = None,
) -> list[dict]:
    """校验 providers，并兼容旧版的 ``name: config`` 映射格式。"""
    if isinstance(raw_providers, dict):
        entries = []
        for name, entry in raw_providers.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise ConfigError("Provider mapping entries must be named mappings")
            normalized = dict(entry)
            normalized.setdefault("name", name)
            entries.append(normalized)
        if default_name:
            entries.sort(key=lambda entry: entry["name"] != default_name)
        raw_providers = entries

    if not isinstance(raw_providers, list) or len(raw_providers) == 0:
        raise ConfigError("At least one provider must be configured")

    providers: list[dict] = []
    for i, entry in enumerate(raw_providers):
        if not isinstance(entry, dict):
            raise ConfigError(f"Provider #{i + 1}: must be a mapping")

        missing = [f for f in ("name", "protocol", "base_url", "model") if f not in entry]
        if missing:
            raise ConfigError(f"Provider #{i + 1}: missing fields: {', '.join(missing)}")

        protocol = entry["protocol"]
        if protocol not in VALID_PROTOCOLS:
            raise ConfigError(
                f"Provider #{i + 1}: invalid protocol '{protocol}', "
                f"must be one of: {', '.join(sorted(VALID_PROTOCOLS))}"
            )

        # 默认为 0（"未设置"）而非硬编码的 window 值：0 会让
        # ProviderConfig.get_context_window() 走四层回退链解析
        #（自动拉取 / 映射表 / 默认值）。配置中显式指定的值仍须为正整数，
        # 且作为最高优先级覆盖。
        context_window = entry.get("context_window", 0)
        if not isinstance(context_window, int) or isinstance(context_window, bool) or context_window < 0:
            raise ConfigError(
                f"Provider #{i + 1}: context_window must be a positive integer"
            )

        thinking = entry.get("thinking", False)
        if not isinstance(thinking, bool):
            raise ConfigError(f"Provider #{i + 1}: thinking must be a boolean")

        max_output_tokens = entry.get("max_output_tokens", 0)
        if not isinstance(max_output_tokens, int) or max_output_tokens < 0:
            raise ConfigError(
                f"Provider #{i + 1}: max_output_tokens must be a non-negative integer"
            )

        providers.append(
            {
                "name": entry["name"],
                "protocol": protocol,
                "base_url": entry["base_url"],
                "model": entry["model"],
                "api_key": entry.get("api_key", ""),
                "thinking": thinking,
                "context_window": context_window,
                "max_output_tokens": max_output_tokens,
            }
        )

    return providers


def validate_permission_mode(mode: str) -> str:
    """校验 permission_mode 取值。"""
    if mode not in VALID_PERMISSION_MODES:
        raise ConfigError(
            f"Invalid permission_mode '{mode}', "
            f"must be one of: {', '.join(sorted(VALID_PERMISSION_MODES))}"
        )
    return mode


def validate_mcp_servers(raw_mcp: list | dict | None) -> list[dict]:
    """校验 mcp_servers 配置段，返回清洗后的 server 配置字典列表。

    兼容两种写法：新版列表格式，以及旧版 ``name: config`` 映射格式。
    """
    if raw_mcp is None:
        return []

    if isinstance(raw_mcp, dict):
        entries = []
        for name, entry in raw_mcp.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise ConfigError("MCP server mapping entries must be named mappings")
            normalized = dict(entry)
            normalized.setdefault("name", name)
            entries.append(normalized)
        raw_mcp = entries

    if not isinstance(raw_mcp, list):
        raise ConfigError("'mcp_servers' must be a list or named mapping of server configs")

    servers: list[dict] = []
    for i, entry in enumerate(raw_mcp):
        if not isinstance(entry, dict):
            raise ConfigError(f"MCP server #{i + 1}: must be a mapping")
        name = entry.get("name")
        if not name:
            raise ConfigError(f"MCP server #{i + 1}: missing 'name'")
        has_command = "command" in entry
        has_url = "url" in entry
        if has_command and has_url:
            raise ConfigError(
                f"MCP server '{name}': cannot have both 'command' and 'url'"
            )
        if not has_command and not has_url:
            raise ConfigError(
                f"MCP server '{name}': must have either 'command' or 'url'"
            )
        servers.append(
            {
                "name": name,
                "command": entry.get("command"),
                "args": entry.get("args", []),
                "url": entry.get("url"),
                "headers": entry.get("headers", {}),
                "env": entry.get("env", {}),
            }
        )

    return servers


def validate_hooks(raw_hooks: list | None) -> list:
    """校验 hooks 配置段。"""
    if raw_hooks is None:
        return []
    if not isinstance(raw_hooks, list):
        raise ConfigError("'hooks' must be a list of hook definitions")
    return raw_hooks


def validate_bool_field(value: object, field_name: str) -> bool:
    """校验一个布尔类型的配置字段。"""
    if not isinstance(value, bool):
        raise ConfigError(f"'{field_name}' must be a boolean")
    return value


def validate_worktree(raw_wt: dict | None) -> dict:
    """校验 worktree 配置段，返回清洗后的配置字典。"""
    defaults = {
        "symlink_directories": ["node_modules", ".venv", "vendor"],
        "stale_cleanup_interval": 3600,
        "stale_cutoff_hours": 24,
    }

    if raw_wt is None:
        return defaults

    if not isinstance(raw_wt, dict):
        raise ConfigError("'worktree' must be a mapping")

    sym = raw_wt.get("symlink_directories", defaults["symlink_directories"])
    if not isinstance(sym, list) or not all(isinstance(s, str) for s in sym):
        raise ConfigError("'worktree.symlink_directories' must be a list of strings")

    interval = raw_wt.get("stale_cleanup_interval", defaults["stale_cleanup_interval"])
    if not isinstance(interval, int) or interval <= 0:
        raise ConfigError("'worktree.stale_cleanup_interval' must be a positive integer")

    cutoff = raw_wt.get("stale_cutoff_hours", defaults["stale_cutoff_hours"])
    if not isinstance(cutoff, int) or cutoff <= 0:
        raise ConfigError("'worktree.stale_cutoff_hours' must be a positive integer")

    return {
        "symlink_directories": sym,
        "stale_cleanup_interval": interval,
        "stale_cutoff_hours": cutoff,
    }


def validate_teammate_mode(mode: object) -> str:
    """校验 teammate_mode 取值。"""
    if not isinstance(mode, str) or mode not in VALID_TEAMMATE_MODES:
        raise ConfigError(
            f"Invalid teammate_mode '{mode}', "
            f"must be one of: {', '.join(repr(m) for m in sorted(VALID_TEAMMATE_MODES))}"
        )
    return mode


def validate_self_evolution(raw_self_evolution: object) -> dict:
    """校验 self_evolution 配置段。

    自进化只由配置开关控制；trusted-auto 只允许通过完整评测门禁的
    自动生成 candidate 进入策略化提升路径。
    """
    defaults = {
        "enabled": False,
        "skill_approval_mode": "manual",
        "trusted_auto_rollback_threshold": 1,
        "trusted_auto_rollback_events": ["failure", "user_feedback"],
    }

    if raw_self_evolution is None:
        return defaults
    if not isinstance(raw_self_evolution, dict):
        raise ConfigError("'self_evolution' must be a mapping")

    enabled = validate_bool_field(
        raw_self_evolution.get("enabled", defaults["enabled"]),
        "self_evolution.enabled",
    )
    approval_mode = raw_self_evolution.get(
        "skill_approval_mode",
        defaults["skill_approval_mode"],
    )
    if (
        not isinstance(approval_mode, str)
        or approval_mode not in VALID_SELF_EVOLUTION_APPROVAL_MODES
    ):
        raise ConfigError(
            "Invalid self_evolution.skill_approval_mode "
            f"'{approval_mode}', must be one of: "
            f"{', '.join(sorted(VALID_SELF_EVOLUTION_APPROVAL_MODES))}"
        )
    rollback_threshold = raw_self_evolution.get(
        "trusted_auto_rollback_threshold",
        defaults["trusted_auto_rollback_threshold"],
    )
    if (
        not isinstance(rollback_threshold, int)
        or isinstance(rollback_threshold, bool)
        or rollback_threshold <= 0
    ):
        raise ConfigError(
            "self_evolution.trusted_auto_rollback_threshold must be a positive integer"
        )
    rollback_events = raw_self_evolution.get(
        "trusted_auto_rollback_events",
        defaults["trusted_auto_rollback_events"],
    )
    if (
        not isinstance(rollback_events, list)
        or not rollback_events
        or not all(isinstance(event, str) for event in rollback_events)
    ):
        raise ConfigError(
            "self_evolution.trusted_auto_rollback_events must be a non-empty list"
        )
    clean_rollback_events = []
    for event in rollback_events:
        clean = event.strip()
        if clean not in VALID_SELF_EVOLUTION_ROLLBACK_EVENTS:
            raise ConfigError(
                "Invalid self_evolution.trusted_auto_rollback_events item "
                f"'{event}', must be one of: "
                f"{', '.join(sorted(VALID_SELF_EVOLUTION_ROLLBACK_EVENTS))}"
            )
        if clean not in clean_rollback_events:
            clean_rollback_events.append(clean)

    return {
        "enabled": enabled,
        "skill_approval_mode": approval_mode,
        "trusted_auto_rollback_threshold": rollback_threshold,
        "trusted_auto_rollback_events": clean_rollback_events,
    }


def validate_config_structure(raw: object) -> dict:
    """校验的主入口。校验解析后的原始配置，返回清洗后的字典。

    返回的字典包含以下键：
        providers、permission_mode、mcp_servers、hooks、
        enable_fork、enable_verification_agent、worktree、
        teammate_mode、enable_coordinator_mode、self_evolution
    """
    if not isinstance(raw, dict) or "providers" not in raw:
        raise ConfigError("Config must contain a 'providers' list or named mapping")

    return {
        "providers": validate_providers(raw["providers"], raw.get("default")),
        "permission_mode": validate_permission_mode(raw.get("permission_mode", "default")),
        "mcp_servers": validate_mcp_servers(raw.get("mcp_servers")),
        "hooks": validate_hooks(raw.get("hooks")),
        "enable_fork": validate_bool_field(raw.get("enable_fork", False), "enable_fork"),
        "enable_verification_agent": validate_bool_field(
            raw.get("enable_verification_agent", False), "enable_verification_agent"
        ),
        "worktree": validate_worktree(raw.get("worktree")),
        "teammate_mode": validate_teammate_mode(raw.get("teammate_mode", "")),
        "enable_coordinator_mode": validate_bool_field(
            raw.get("enable_coordinator_mode", False), "enable_coordinator_mode"
        ),
        "self_evolution": validate_self_evolution(raw.get("self_evolution")),
        "self_evolution_explicit": "self_evolution" in raw,
    }
