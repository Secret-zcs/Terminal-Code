# ch12：MCP 集成 — `mcp/`

> 文件：`client.py`, `manager.py`, `tool_wrapper.py`

---

## 一、两种连接方式

| | stdio | HTTP |
|------|------|------|
| 怎么连 | 启动子进程 | 连远程网址 |
| 通信 | stdin/stdout | HTTP 请求/响应 |
| 配置 | `command` + `args` | `url` + `headers` |

```python
async def connect(self):
    if self.config.is_stdio:
        read, write = await stdio_client(params, errlog=devnull)
    else:
        read, write = await streamable_http_client(url, http_client=...)
    session = await ClientSession(read, write).initialize()
```

---

## 二、MCPToolWrapper — 包装外部工具

```python
class MCPToolWrapper(Tool):
    name = f"mcp__{server_name}__{tool_name}"  # 双下划线防冲突
    should_defer = True                         # 默认懒加载
    params_model = create_model(f"{tool_name}Params", **fields)  # 动态生成Pydantic类
```

**`create_model`** 是 Pydantic 的运行时类工厂——根据 JSON Schema 动态生成参数模型。MCP 服务器的工具定义各不相同，无法提前写死。

**懒加载**：注册时 `should_defer=True`，不发完整 schema。LLM 通过 ToolSearch 搜索激活后才发送。

---

## 三、完整数据流

```
① 配置文件写 mcp_servers → ② MCPManager.register_all_tools(): 连接→list_tools()→注册
③ LLM 看到懒加载提示 → ④ LLM 调 ToolSearch → mark_discovered → 下轮发完整 schema
⑤ LLM 调 mcp__xxx__yyy → MCPToolWrapper.execute() → MCPClient.call_tool() → MCP 服务器执行
```

**LLM 不直接调 MCP 服务器。** 调用链：LLM → Agent → wrapper.execute() → client.call_tool() → MCP 服务器 → 结果原路返回。
