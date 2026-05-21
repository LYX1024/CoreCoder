"""免费天气查询 MCP 服务器。

通过 wttr.in（免费，无需 API Key）查询天气。
使用 FastMCP 实现，代码量减少 60%。

配置方式（corecoder/mcp_servers/mcp.json）：
{
    "mcpServers": {
        "weather": {
            "command": "python",
            "args": ["corecoder/mcp_servers/weather_server.py"]
        }
    }
}
"""

import urllib.error
import urllib.parse
import urllib.request
from mcp.server.fastmcp import FastMCP

server = FastMCP("weather")


@server.tool(description="查询指定城市的当前天气（支持中文城市名）")
def get_weather(city: str) -> str:
    """获取指定城市的当前天气信息。

    Args:
        city: 城市名称，如 Beijing、Shanghai、东京
    """
    if not city.strip():
        return "请输入城市名称"

    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=%C+%t+%h+%w&lang=zh"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("utf-8").strip()
        return f"{city} 天气：{data}"
    except urllib.error.HTTPError as e:
        return f"查询失败：{e.code} {e.reason}"
    except Exception as e:
        return f"查询失败：{e}"


if __name__ == "__main__":
    server.run()
