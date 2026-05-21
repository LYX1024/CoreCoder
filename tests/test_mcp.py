"""MCP 客户端单元测试和集成测试。"""

import contextlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 将被测模块加入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "corecoder"))

from tools.mcp import MCPManager, MCPSession, MCPTool


# ============================================================
# 单元测试：MCPTool 适配器
# ============================================================


class TestMCPTool:
    """测试 MCPTool 适配器将 MCP 工具映射为 CoreCoder Tool 接口。"""

    def test_init_basic(self):
        """基本初始化：名称添加服务器前缀，保留原始名称。"""
        session = MagicMock(spec=MCPSession)
        session.server_name = "test_srv"

        tool = MCPTool(
            session=session,
            name="read_file",
            description="读取文件",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        )

        assert tool.name == "test_srv_read_file"
        assert tool._original_name == "read_file"
        assert tool.description == "读取文件"
        assert tool.parameters["type"] == "object"

    def test_schema(self):
        """schema() 返回 OpenAI function calling 兼容格式。"""
        session = MagicMock(spec=MCPSession)
        session.server_name = "fs"

        tool = MCPTool(
            session=session,
            name="list_dir",
            description="列出目录内容",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )

        schema = tool.schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "fs_list_dir"
        assert schema["function"]["description"] == "列出目录内容"
        assert "path" in schema["function"]["parameters"]["properties"]

    @patch("tools.mcp.asyncio.run")
    def test_execute_calls_session_call_tool(self, mock_run):
        """execute() 内部调用 session.call_tool()。"""
        session = MagicMock(spec=MCPSession)
        session.server_name = "test"
        mock_run.return_value = "file content"

        tool = MCPTool(
            session=session,
            name="read_file",
            description="读取文件",
            parameters={"type": "object", "properties": {}},
        )

        result = tool.execute(path="/test/file.txt")

        # 验证 asyncio.run 被调用，且传入了正确的异步调用
        mock_run.assert_called_once()
        # 验证 session.call_tool 被传入 asyncio.run
        call_args, _ = mock_run.call_args
        assert call_args is not None
        assert result == "file content"


# ============================================================
# 单元测试：MCPSession 初始化
# ============================================================


class TestMCPSessionInit:
    """测试 MCPSession 初始化逻辑。"""

    def test_init_stdio_config(self):
        """stdio 模式配置正确存储。"""
        session = MCPSession(
            server_name="fs",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"NODE_ENV": "production"},
        )
        assert session.server_name == "fs"
        assert session.command == "npx"
        assert session.args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        assert session.env == {"NODE_ENV": "production"}
        assert session.url is None
        assert not session._initialized

    def test_init_sse_config(self):
        """SSE 模式配置正确存储。"""
        session = MCPSession(
            server_name="remote",
            url="http://localhost:8080/mcp",
        )
        assert session.server_name == "remote"
        assert session.url == "http://localhost:8080/mcp"
        assert session.command is None
        assert not session._initialized

    def test_init_no_command_no_url(self):
        """既无 command 也无 url 时初始化抛出异常。"""
        session = MCPSession(server_name="bad")

        with pytest.raises(RuntimeError, match="must provide either 'command' \\(stdio\\) or 'url' \\(SSE\\)"):
            import asyncio
            asyncio.run(session.initialize())

    @patch("tools.mcp.stdio_client")
    @patch("tools.mcp.ClientSession")
    @patch("tools.mcp.StdioServerParameters")
    @patch("contextlib.AsyncExitStack")
    def test_initialize_stdio_success(self, mock_exit_stack, mock_params, mock_client, mock_stdio):
        """stdio 模式成功初始化和获取工具列表。"""
        # 模拟退出栈
        mock_stack = MagicMock()
        mock_exit_stack.return_value.__aenter__.return_value = mock_stack
        mock_stack.enter_async_context = AsyncMock()

        # 模拟传输层
        mock_read = MagicMock()
        mock_write = MagicMock()
        mock_stack.enter_async_context.return_value = (mock_read, mock_write)

        # 模拟 ClientSession
        mock_session = MagicMock()
        mock_client.return_value.__aenter__.return_value = mock_session

        # 模拟工具返回
        tool_mock = MagicMock()
        tool_mock.name = "read_file"
        tool_mock.description = "读取文件"
        tool_mock.inputSchema = {"type": "object", "properties": {"path": {"type": "string"}}}
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool_mock]))
        mock_session.initialize = AsyncMock()

        session = MCPSession(
            server_name="fs",
            command="npx",
            args=["-y", "server"],
        )

        import asyncio
        tools = asyncio.run(session.initialize())

        assert len(tools) == 1
        assert tools[0]["name"] == "read_file"
        assert tools[0]["description"] == "读取文件"
        assert session._initialized is True

    @patch("tools.mcp.sse_client")
    @patch("tools.mcp.ClientSession")
    @patch("contextlib.AsyncExitStack")
    def test_initialize_sse_success(self, mock_exit_stack, mock_client, mock_sse):
        """SSE 模式成功初始化和获取工具列表。"""
        mock_stack = MagicMock()
        mock_exit_stack.return_value.__aenter__.return_value = mock_stack
        mock_stack.enter_async_context = AsyncMock()
        mock_stack.enter_async_context.return_value = (MagicMock(), MagicMock())

        mock_session = MagicMock()
        mock_client.return_value.__aenter__.return_value = mock_session

        tool_mock = MagicMock()
        tool_mock.name = "hello"
        tool_mock.description = "打招呼"
        tool_mock.inputSchema = {"type": "object", "properties": {"name": {"type": "string"}}}
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool_mock]))
        mock_session.initialize = AsyncMock()

        session = MCPSession(
            server_name="remote",
            url="http://localhost:8080/mcp",
        )

        import asyncio
        tools = asyncio.run(session.initialize())

        assert len(tools) == 1
        assert tools[0]["name"] == "hello"
        assert session._initialized is True


# ============================================================
# 单元测试：MCPManager
# ============================================================


class TestMCPManager:
    """测试 MCPManager 管理多个服务器连接。"""

    def test_init_empty_servers(self):
        """空服务器配置不创建任何会话。"""
        manager = MCPManager({})
        assert len(manager.sessions) == 0
        assert len(manager.tools) == 0

    def test_init_multiple_servers(self):
        """多服务器配置创建多个 MCPSession。"""
        servers = {
            "fs": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            },
            "remote": {
                "url": "http://localhost:8080/mcp",
            },
        }
        manager = MCPManager(servers)
        assert len(manager.sessions) == 2
        assert manager.sessions[0].server_name == "fs"
        assert manager.sessions[1].server_name == "remote"

    @patch.object(MCPSession, "initialize", new_callable=AsyncMock)
    def test_initialize_all_success(self, mock_init):
        """initialize_all() 成功初始化所有服务器并返回工具列表。"""
        mock_init.return_value = [
            {
                "name": "read_file",
                "description": "读取文件",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "write_file",
                "description": "写入文件",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

        servers = {
            "fs": {"command": "npx", "args": ["-y", "server"]},
        }
        manager = MCPManager(servers)
        tools = manager.initialize_all()

        assert len(tools) == 2
        assert isinstance(tools[0], MCPTool)
        assert tools[0].name == "fs_read_file"
        assert tools[1].name == "fs_write_file"

    @patch.object(MCPSession, "initialize", new_callable=AsyncMock)
    def test_initialize_all_partial_failure(self, mock_init):
        """部分服务器失败时优雅降级，不中断整体初始化。"""
        # 第一次调用（fs）成功，第二次调用（bad）失败
        mock_init.side_effect = [
            [
                {
                    "name": "read_file",
                    "description": "读取文件",
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ],
            Exception("Connection refused"),
        ]

        servers = {
            "fs": {"command": "npx", "args": ["-y", "good-server"]},
            "bad": {"command": "invalid-command"},
        }
        manager = MCPManager(servers)
        tools = manager.initialize_all()

        # 只有一个成功
        assert len(tools) == 1
        assert tools[0].name == "fs_read_file"
        # 检查错误记录
        errors = manager.get_errors()
        assert len(errors) == 1
        assert errors[0][0] == "bad"
        assert "Connection refused" in errors[0][1]

    @patch.object(MCPSession, "close", new_callable=AsyncMock)
    def test_close_all(self, mock_close):
        """close_all() 关闭所有会话。"""
        servers = {
            "srv1": {"command": "echo"},
            "srv2": {"command": "echo"},
        }
        manager = MCPManager(servers)
        manager.close_all()
        assert mock_close.call_count == 2


# ============================================================
# 集成测试：使用测试 MCP 服务器
# ============================================================


@pytest.fixture
def echo_mcp_server_script():
    """创建一个简单的 MCP 服务器脚本，用于集成测试。

    该服务器提供一个 'echo' 工具，接收 'message' 参数并原样返回。
    """
    script = """
import json
import sys

def handle_request(request):
    req_id = request.get("id")
    method = request.get("method")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}}
            }
        }
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo back the message",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {
                                    "type": "string",
                                    "description": "Message to echo"
                                }
                            },
                            "required": ["message"]
                        }
                    }
                ]
            }
        }
    elif method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments", {})
        if name == "echo":
            msg = arguments.get("message", "")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Echo: {msg}"
                        }
                    ]
                }
            }
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}

def main():
    content_length = 0
    buffer = ""
    for line in sys.stdin:
        buffer += line
        if "\\r\


" in buffer:
            parts = buffer.split("\\r\


")
            headers = parts[0]
            buffer = parts[1] if len(parts) > 1 else ""
            for h in headers.split("\\r\
"):
                if h.lower().startswith("content-length:"):
                    content_length = int(h.split(":")[1].strip())
            if content_length > 0 and len(buffer) >= content_length:
                body = buffer[:content_length]
                buffer = buffer[content_length:]
                request = json.loads(body)
                response = handle_request(request)
                if response is not None:
                    resp_str = json.dumps(response)
                    sys.stdout.write(f"Content-Length: {len(resp_str.encode())}\\r\
")
                    sys.stdout.write("Content-Type: application/json\\r\
")
                    sys.stdout.write("\\r\
")
                    sys.stdout.write(resp_str)
                    sys.stdout.flush()
                content_length = 0

if __name__ == "__main__":
    main()
"""
    return script


@pytest.mark.integration
def test_echo_server_integration(echo_mcp_server_script):
    """使用模拟的 echo MCP 服务器进行端到端集成测试。

    注意：此测试需要 mcp Python SDK 支持 stdio 客户端。
    如果 mcp 包未安装，测试被跳过。
    """
    pytest.importorskip("mcp")

    import asyncio
    import tempfile

    # 创建临时脚本
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(echo_mcp_server_script)
        script_path = f.name

    try:
        session = MCPSession(
            server_name="echo_test",
            command=sys.executable,
            args=[script_path],
        )

        async def run_test():
            tools = await session.initialize()
            assert len(tools) == 1
            assert tools[0]["name"] == "echo"

            result = await session.call_tool("echo", {"message": "Hello MCP"})
            assert "Echo: Hello MCP" in result

            await session.close()

        asyncio.run(run_test())
    finally:
        import os
        os.unlink(script_path)


# ============================================================
# 配置加载测试
# ============================================================


class TestMCPConfig:
    """测试 MCP 配置加载逻辑。"""

    def test_load_mcp_config_file_not_exists(self):
        """配置文件不存在时返回空字典。"""
        from config import _load_mcp_config_file

        # 临时修改 home 目录指向不存在的路径
        with patch("config.Path.home") as mock_home:
            mock_home.return_value = Path("/nonexistent")
            result = _load_mcp_config_file()
            assert result == {}

    def test_load_mcp_config_file_valid(self, tmp_path):
        """有效的 MCP 配置文件正确加载。"""
        from config import _load_mcp_config_file

        config_dir = tmp_path / ".corecoder"
        config_dir.mkdir()
        config_file = config_dir / "mcp.json"
        config_file.write_text(
            json.dumps({
                "mcpServers": {
                    "fs": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    }
                }
            }),
            encoding="utf-8",
        )

        with patch("config.Path.home") as mock_home:
            mock_home.return_value = tmp_path
            result = _load_mcp_config_file()
            assert "fs" in result
            assert result["fs"]["command"] == "npx"

    def test_load_mcp_config_file_invalid_json(self, tmp_path, capsys):
        """无效的 JSON 配置文件给出警告并返回空字典。"""
        from config import _load_mcp_config_file

        config_dir = tmp_path / ".corecoder"
        config_dir.mkdir()
        config_file = config_dir / "mcp.json"
        config_file.write_text("not valid json", encoding="utf-8")

        with patch("config.Path.home") as mock_home:
            mock_home.return_value = tmp_path
            result = _load_mcp_config_file()
            assert result == {}
            captured = capsys.readouterr()
            assert "Warning" in captured.out
