from __future__ import annotations

import sys
import asyncio
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any


def _install_missing_mcp_test_stub() -> None:
    try:
        import mcp  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    mcp_module = ModuleType("mcp")
    types_module = ModuleType("mcp.types")
    client_module = ModuleType("mcp.client")
    stdio_module = ModuleType("mcp.client.stdio")
    http_module = ModuleType("mcp.client.streamable_http")

    @dataclass
    class Tool:
        name: str
        description: str | None = None
        inputSchema: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class TextContent:
        type: str
        text: str

    @dataclass
    class ImageContent:
        type: str
        data: str
        mimeType: str

    @dataclass
    class EmbeddedResource:
        type: str
        resource: Any

    @dataclass
    class CallToolResult:
        content: list[Any]
        isError: bool = False

    class ClientSession:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "ClientSession":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> Any:
            raise RuntimeError("mcp package is not installed")

        async def call_tool(self, _name: str, _arguments: dict[str, Any]) -> Any:
            raise RuntimeError("mcp package is not installed")

    @dataclass
    class StdioServerParameters:
        command: str
        args: list[str] = field(default_factory=list)
        env: dict[str, str] | None = None

    def _missing_mcp_dependency(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("mcp package is not installed")

    for name, value in {
        "Tool": Tool,
        "TextContent": TextContent,
        "ImageContent": ImageContent,
        "EmbeddedResource": EmbeddedResource,
        "CallToolResult": CallToolResult,
    }.items():
        setattr(types_module, name, value)

    mcp_module.ClientSession = ClientSession
    mcp_module.types = types_module
    stdio_module.StdioServerParameters = StdioServerParameters
    stdio_module.stdio_client = _missing_mcp_dependency
    http_module.streamable_http_client = _missing_mcp_dependency
    client_module.stdio = stdio_module
    client_module.streamable_http = http_module

    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.types"] = types_module
    sys.modules["mcp.client"] = client_module
    sys.modules["mcp.client.stdio"] = stdio_module
    sys.modules["mcp.client.streamable_http"] = http_module


_install_missing_mcp_test_stub()


def pytest_runtest_setup(item) -> None:  # type: ignore[no-untyped-def]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def pytest_ignore_collect(collection_path, config) -> bool:  # type: ignore[no-untyped-def]
    root = Path(str(config.rootpath)).resolve()
    cwd = Path.cwd().resolve()
    path = Path(str(collection_path)).resolve()

    for ignored in (".venv", ".mewcode", "__pycache__"):
        ignored_path = root / ignored
        if path == ignored_path or ignored_path in path.parents:
            return True

    fixtures = root / "fixtures"
    if cwd == fixtures or fixtures in cwd.parents:
        return False
    return path == fixtures or fixtures in path.parents
