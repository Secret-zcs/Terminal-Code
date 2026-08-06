from __future__ import annotations

from mewcode.config import load_config
from mewcode.validator import validate_config_structure


def test_provider_config_resolves_api_key_environment_variable(monkeypatch) -> None:
    from mewcode.config import ProviderConfig

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    provider = ProviderConfig(
        name="deepseek",
        protocol="anthropic",
        base_url="https://api.example.invalid",
        model="deepseek-v4-flash",
        api_key="${DEEPSEEK_API_KEY}",
    )

    assert provider.resolve_api_key() == "test-secret"


def test_provider_config_treats_unresolved_api_key_placeholder_as_missing(
    monkeypatch,
) -> None:
    from mewcode.config import ProviderConfig

    monkeypatch.delenv("MISSING_DEEPSEEK_KEY", raising=False)
    provider = ProviderConfig(
        name="deepseek",
        protocol="anthropic",
        base_url="https://api.example.invalid",
        model="deepseek-v4-flash",
        api_key="${MISSING_DEEPSEEK_KEY}",
    )

    assert provider.resolve_api_key() == ""


def test_legacy_named_provider_mapping_is_normalized_and_default_is_first() -> None:
    config = validate_config_structure(
        {
            "default": "deepseek",
            "providers": {
                "openai": {
                    "protocol": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o",
                },
                "deepseek": {
                    "protocol": "anthropic",
                    "base_url": "https://api.deepseek.com/anthropic",
                    "model": "deepseek-v4-flash",
                },
            },
        }
    )

    assert [provider["name"] for provider in config["providers"]] == [
        "deepseek",
        "openai",
    ]


def test_config_loads_with_environment_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """providers:
  - name: deepseek-v4-flash
    protocol: anthropic
    base_url: https://api.deepseek.com/anthropic
    model: deepseek-v4-flash
    api_key: ${DEEPSEEK_API_KEY}
""",
        encoding="utf-8",
    )
    config = load_config(config_file)

    provider = config.providers[0]
    assert provider.name == "deepseek-v4-flash"
    assert provider.protocol == "anthropic"
    assert provider.model == "deepseek-v4-flash"
    assert provider.resolve_api_key() == "test-secret"
