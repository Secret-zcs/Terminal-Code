# ch12 答疑：MCP 完整流程、连接方式、参数、懒加载、执行路径

> 日期：2026-06-23

---

## Q1: MCP 整个流程是什么？

```
① 配置文件写 mcp_servers
② 启动时 MCPManager.register_all_tools(): 连接 → list_tools() → 注册到 ToolRegistry
③ LLM 看到懒加载提示（非完整 schema）
④ LLM 需要时 → ToolSearch → mark_discovered → 下轮发送完整 schema
⑤ LLM 调用 → MCPToolWrapper.execute() → MCPClient.call_tool() → MCP 服务器 → 返回
```

---

## Q2: stdio 与 HTTP 连接的区别

| | stdio | HTTP |
|------|------|------|
| 怎么连 | 启动本地子进程 | 连远程网址 |
| 通信 | 子进程 stdin/stdout | HTTP 请求/响应 |
| 配置 | `command` + `args` | `url` + `headers` |

stdio = 雇助手坐旁边写纸条。HTTP = 打电话给远程客服。

---

## Q3: mcp_servers 参数

- `name`: 唯一标识，工具注册名 = `mcp__{name}__{tool}`
- `command`: 可执行文件（stdio 模式，与 url 二选一）
- `args`: 命令行参数
- `url`: 远程地址（HTTP 模式，与 command 二选一）
- `headers`: HTTP 请求头，支持 `${VAR}` 替换
- `env`: 子进程环境变量，只继承 PATH，支持 `${}` 替换

---

## Q4: 为什么注册了还要懒加载？

注册到 ToolRegistry ≠ 发 schema 给 LLM。注册是给 Agent 用的（知道怎么执行），schema 是给 LLM 看的（知道怎么调用）。MCP 服务器可能有几十个工具，全部 schema 发出去浪费 token。懒加载只发一句提示（~100 token），用到再激活（~400 token）。

---

## Q5: 执行结果来自哪里？本地工具和 MCP 工具的区别？

**本地工具**：Agent → tool.execute() → 本地 Python 代码执行（ReadFile 读磁盘、Bash 开子进程）→ 返回。没有外部转发。

**MCP 工具**：Agent → MCPToolWrapper.execute() → MCPClient.call_tool() → 通过 stdio/HTTP 转发给 MCP 服务器 → MCP 服务器执行 → 结果原路返回。

Agent 不关心区别——它只调 `tool.execute()`。两条路通过 Tool 多态统一。
