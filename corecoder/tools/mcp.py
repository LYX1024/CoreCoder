"""MCP 客户端 - 将 MCP 工具适配为 CoreCoder 的 Tool 接口。

提供 MCPSession、MCPTool、MCPManager 三个类，支持 stdio 和 SSE 两种传输协议。
"""

import asyncio
import json
import os
import sys
import threading
from contextlib import AsyncExitStack
from typing import Any

from .base import Tool

# 延迟导入 mcp SDK，允许未安装时优雅降级
try:
    from mcp import ClientSession, StdioServerParameters, stdio_client
    from mcp.client.sse import sse_client

    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]
    sse_client = None  # type: ignore[assignment]


class MCPSession:
    """管理单个 MCP 服务器连接的生命周期。

    支持两种传输协议：
    - stdio：通过子进程方式运行 MCP 服务器，通过 stdin/stdout 进行 JSON-RPC 通信
    - SSE：通过 HTTP 连接 MCP 服务器，使用 Server-Sent Events 进行通信
    """

    def __init__(
        self,
        server_name: str,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        env: dict[str, str] | None = None,
    ):
        self.server_name = server_name
        self.command = command       # stdio 模式：可执行文件路径
        self.args = args or []       # stdio 模式：命令行参数
        self.url = url               # SSE 模式：服务器 URL
        self.env = env               # 额外环境变量
        self._exit_stack: AsyncExitStack | None = None
        self._session: Any = None
        self._initialized = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def initialize(self) -> list[dict]:
        """连接 MCP 服务器，获取工具列表。

        Returns:
            工具元数据列表，每项包含 name、description、inputSchema。

        Raises:
            RuntimeError: 既未提供 command 也未提供 url
        """
        if not _MCP_AVAILABLE:
            raise RuntimeError(
                "mcp SDK is not installed. Run: pip install mcp>=1.0.0"
            )
        if self.url:
            return await self._init_sse()
        if self.command:
            return await self._init_stdio()
        raise RuntimeError(
            f"Server '{self.server_name}': must provide either 'command' (stdio) or 'url' (SSE)"
        )

    async def _init_stdio(self) -> list[dict]:
        """通过 stdio 协议初始化 MCP 会话。"""

        # 异步资源栈管理异步资源，保障资源释放
        self._exit_stack = AsyncExitStack()

        # 合并环境变量
        merged_env = None
        # **self.env覆盖掉**os.environ（继承环境变量的同时获取MCP配置）
        if self.env:
            merged_env = {**os.environ, **self.env}

        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=merged_env,
        )

        # 建立stdio传输通道
        transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self._read, self._write = transport

        # 创建MCP客户端会话 核心为ClientSession
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(self._read, self._write)
        )

        # 初始化握手
        await self._session.initialize()
        self._initialized = True

        # 获取工具列表
        result = await self._session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema or {"type": "object", "properties": {}},
            }
            for tool in result.tools
        ]

    async def _init_sse(self) -> list[dict]:
        """通过 SSE 协议初始化 MCP 会话。"""
        self._exit_stack = AsyncExitStack()

        transport = await self._exit_stack.enter_async_context(
            sse_client(url=self.url)
        )
        self._read, self._write = transport

        self._session = await self._exit_stack.enter_async_context(
            ClientSession(self._read, self._write)
        )
        await self._session.initialize()
        self._initialized = True

        # 获取工具列表
        result = await self._session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema or {"type": "object", "properties": {}},
            }
            for tool in result.tools
        ]

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用 MCP 服务器上的一个工具。

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果文本
        """
        if not self._initialized or self._session is None:
            raise RuntimeError(f"MCP session '{self.server_name}' not initialized")
        
        # 发送 MCP 协议的 JSON-RPC 请求
        result = await self._session.call_tool(tool_name, arguments)
        # 处理返回结果（兼容不同版本的 mcp SDK）
        if hasattr(result, "content") and result.content:
            texts = []
            for item in result.content:
                if hasattr(item, "text"):
                    texts.append(item.text)
                elif isinstance(item, dict):
                    texts.append(json.dumps(item, ensure_ascii=False))
                else:
                    texts.append(str(item))
            return "\n".join(texts)
        return str(result)

    async def close(self):
        """关闭连接，清理资源。"""
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass
        self._session = None
        self._initialized = False


class MCPTool(Tool):
    """适配 MCP 工具到 CoreCoder 的 Tool 接口。

    MCPTool(异步) --包装为--> Function Calling(同步)
    每个 MCP 工具被包装为一个 MCPTool 实例，可以像原生工具一样被 Agent 调用。
    工具名使用 ``{server_name}_{tool_name}`` 格式以避免不同服务器间的命名冲突。
    """

    def __init__(
        self,
        session: MCPSession,
        name: str,
        description: str,
        parameters: dict,
    ):
        self._session = session
        self._original_name = name  # 原始工具名（无服务器前缀）
        self.name = f"{session.server_name}_{name}"  # 带命名空间，避免冲突
        self.description = description
        self.parameters = parameters

    def execute(self, **kwargs) -> str:
        """同步执行 MCP 工具调用。通过持久化事件循环调度。"""

        # 获取事件循环
        loop = self._session._loop
        if loop is None or not loop.is_running():
            return "Error: MCP session not connected"
        # 异步调用包装为同步
        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(self._original_name, kwargs), loop
        )
        try:
            # 允许阻塞等待120s
            return future.result(timeout=120)
        except asyncio.TimeoutError:
            return f"Error: MCP tool '{self._original_name}' timed out"
        except Exception as e:
            return f"Error: MCP tool '{self._original_name}' failed: {e}"


class MCPManager:
    """管理所有 MCP 服务器连接。

    在后台线程中维护一个持久化事件循环，确保所有 MCP 操作
    （初始化、工具调用、关闭）都在同一个循环中执行。
    负责初始化所有配置的 MCP 服务器、聚合工具列表，以及在退出时清理资源。
    """

    def __init__(self, servers: dict[str, dict]):
        """初始化 MCP 管理器。

        Args:
            servers: MCP 服务器配置字典，格式为
                {"server_name": {"command": "...", "args": [...], "env": {...}}}
                或 {"server_name": {"url": "http://..."}}
        """
        self.tools: list[MCPTool] = [] # 聚合后的工具列表
        self._errors: list[tuple[str, str]] = [] # 初始化错误列表
        self._sessions: list[MCPSession] = [] # MCP会话列表（一个会话对应一个MCP Server）
        self._loop: asyncio.AbstractEventLoop | None = None # 所有MCP操作共享的后台事件循环(类比：java的单例线程池，相关任务都丢到这里边执行，不会东创一个线程西创一个线程导致内存爆炸；上文的run_coroutine_threadsafe()就相当于submit提交了一个任务)
        self._thread: threading.Thread | None = None # 运行上面那个事件循环的后台线程

        for name, config in servers.items():
            session = MCPSession(
                server_name=name,
                command=config.get("command"),
                args=config.get("args"),
                url=config.get("url"),
                env=config.get("env"),
            )
            self._sessions.append(session)

    def initialize_all(self) -> list[MCPTool]:
        """初始化所有 MCP 连接，返回聚合的工具列表。"""
        # 启动后台事件循环线程
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # 在后台循环上执行初始化
        future = asyncio.run_coroutine_threadsafe(self._init_async(), self._loop)
        try:
            self.tools = future.result(timeout=30)
        except asyncio.TimeoutError:
            print("[MCP] initialization timed out", file=sys.stderr)
        except Exception as e:
            print(f"[MCP] initialization failed: {e}", file=sys.stderr)
        return self.tools

    def _run_loop(self):
        """后台线程：运行事件循环直到被停止。"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _init_async(self) -> list[MCPTool]:
        """异步初始化所有 MCP 连接。"""
        all_tools: list[MCPTool] = []
        for session in self._sessions:
            try:
                # 所有MCPSession提交任务的循环都是当前这个事件循环
                session._loop = self._loop
                tools_meta = await session.initialize()
                for meta in tools_meta:
                    tool = MCPTool(
                        session=session,
                        name=meta["name"],
                        description=meta.get("description", ""),
                        parameters=meta.get("inputSchema", {}),
                    )
                    all_tools.append(tool)
            except Exception as e:
                msg = f"Failed to initialize server '{session.server_name}': {e}"
                print(f"[MCP] {msg}", file=sys.stderr)
                self._errors.append((session.server_name, str(e)))
        return all_tools

    def get_errors(self) -> list[tuple[str, str]]:
        return self._errors

    def close_all(self):
        """关闭所有 MCP 连接，停止后台事件循环。"""
        if self._loop and self._loop.is_running():
            # 在后台循环上执行关闭
            future = asyncio.run_coroutine_threadsafe(
                self._close_async(), self._loop
            )
            try:
                future.result(timeout=10)
            except Exception:
                pass
            finally:
                self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    async def _close_async(self):
        """异步关闭所有 MCP 连接。"""
        for session in self._sessions:
            try:
                await session.close()
            except Exception:
                pass
