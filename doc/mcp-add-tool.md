# 添加 MCP 工具

只需两步。

## 第一步：找到或编写 MCP 服务器

**方式 A：使用现成的 npm 包**

```bash
npx -y @modelcontextprotocol/server-everything     # 测试用，包含各种示例工具
npx -y @anthropic/mcp-code-assist                  # 代码辅助
```

**方式 B：编写 Python MCP 服务器**

参考 `corecoder/mcp_servers/weather_server.py`，核心骨架：

```python
import asyncio
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("my-server")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="tool_name",
            description="工具描述",
            inputSchema={
                "type": "object",
                "properties": {
                    "param1": {"type": "string", "description": "参数说明"},
                },
                "required": ["param1"],
            },
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "tool_name":
        result = do_something(arguments)
        return [TextContent(type="text", text=str(result))]
    return [TextContent(type="text", text="Unknown tool")]

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, InitializationOptions(
            server_name="my-server", server_version="1.0.0",
            capabilities={"tools": {}},
        ))

asyncio.run(main())
```

## 第二步：注册到 MCP 配置

编辑项目本地的 `corecoder/mcp_servers/mcp.json`：

```json
{
  "mcpServers": {
    "weather": {
      "command": "python",
      "args": ["mcp_servers/weather_server.py"]
    },
    "my-server": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-everything"]
    }
  }
}
```

也可使用全局配置 `~/.corecoder/mcp.json`（项目本地配置优先）。

## 生效

启动 CoreCoder 时，MCPManager 会自动加载所有注册的服务器：

```
MCP tools loaded: weather_get_weather, everything_echo
```

工具会自动出现在 agent 的可用工具列表中，以 `{服务器名}_{工具名}` 的格式命名。

## 常见问题

**MCP SDK 未安装？**
```bash
pip install mcp>=1.0.0
```

**服务器启动失败？**
CoreCoder 会打印错误日志：`[yellow]MCP [server-name]: 错误信息[/yellow]`

**修改配置后需要重启 CoreCoder**，MCP 配置只在启动时加载一次。

## 配置加载优先级

```
corecoder/mcp_servers/mcp.json  ← 项目本地（优先）
     ↓ 不存在
~/.corecoder/mcp.json           ← 全局配置
     ↓ 不存在
CORECODER_MCP_SERVERS 环境变量  ← 由调用方处理
```
