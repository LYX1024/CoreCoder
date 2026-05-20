# MCP 支持计划

## 1. 概述

### 什么是 MCP

MCP（Model Context Protocol，模型上下文协议）是由 Anthropic 提出的开放协议，用于标准化 AI 模型与外部工具/数据源的交互方式。通过 MCP，CoreCoder 可以动态发现并调用任何实现了 MCP 协议的服务器提供的工具，极大扩展 Agent 的能力边界。

### 目标

为 CoreCoder 添加 MCP 客户端支持，使其能够：
1. 通过配置文件声明式地连接一个或多个 MCP 服务器
2. 自动发现 MCP 服务器提供的工具列表
3. 将 MCP 工具无缝集成到 Agent 的工具集中，像原生工具一样被 LLM 调用
4. 支持 stdio 和 SSE 两种 MCP 传输协议

### 设计原则

- **最小侵入**：不改变现有 Tool 基类和 Agent 核心循环，MCP 工具适配为标准的 `Tool` 子类
- **配置驱动**：MCP 服务器连接信息通过环境变量或配置文件声明
- **懒加载**：MCP 工具在 Agent 初始化时发现并注册，运行时无需额外开销
- **优雅降级**：MCP 服务器连接失败不影响 CoreCoder 自身运行

---

## 2. 项目结构变更

```
corecoder/
├── __init__.py
├── __main__.py
├── agent.py              # 无变更
├── cli.py                # 在 Agent 初始化时注入 MCP 工具
├── config.py             # 新增 mcp_servers 配置项
├── context.py
├── llm.py
├── prompt.py             # 无变更
├── session.py
├── multi_agents.py       # 无变更
├── tools/
│   ├── __init__.py       # 导出 MCPTool, MCPManager（可选）
│   ├── base.py           # 无变更
│   ├── mcp.py            # [新增] MCP 客户端核心实现
│   ├── bash.py
│   ├── read.py
│   └── ...
└── skills/
```

```
tests/
└── test_mcp.py           # [新增] MCP 相关测试
```

---

## 3. 核心实现：`tools/mcp.py`

### 3.1 架构设计

```
┌─────────────────────────────────┐
│         MCPManager              │  ← 管理多个 MCP 服务器连接
│  ┌───────────────────────────┐  │
│  │  MCPSession (server1)     │  │  ← 每个服务器一个会话
│  │  ├─ transport: stdio/SSE  │  │
│  │  └─ tools: [MCPTool, ...] │  │
│  ├───────────────────────────┤  │
│  │  MCPSession (server2)     │  │
│  │  ├─ transport: stdio/SSE  │  │
│  │  └─ tools: [MCPTool, ...] │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

### 3.2 MCPSession 类

管理单个 MCP 服务器的生命周期：

```python
class MCPSession:
    """管理单个 MCP 服务器连接的生命周期。"""
    
    def __init__(self, server_name: str, command: str, args: list[str] | None = None,
                 url: str | None = None, env: dict[str, str] | None = None):
        self.server_name = server_name
        self.command = command       # stdio 模式：可执行文件路径
        self.args = args or []       # stdio 模式：命令行参数
        self.url = url               # SSE 模式：服务器 URL
        self.env = env               # 额外环境变量
        self._process: subprocess.Popen | None = None  # stdio 模式
        self._session: Any | None = None               # mcp 客户端会话
    
    async def initialize(self) -> list[dict]:
        """连接 MCP 服务器，获取工具列表。"""
        ...
    
    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用 MCP 服务器上的一个工具。"""
        ...
    
    async def close(self):
        """关闭连接，清理资源。"""
        ...
```

### 3.3 MCPTool 类

将 MCP 工具适配为 CoreCoder 的 Tool 接口：

```python
class MCPTool(Tool):
    """适配 MCP 工具到 CoreCoder 的 Tool 接口。"""
    
    def __init__(self, session: MCPSession, name: str, description: str, 
                 parameters: dict):
        self.session = session
        self.name = f"{session.server_name}_{name}"  # 命名空间避免冲突
        self.description = description
        self.parameters = parameters
    
    def execute(self, **kwargs) -> str:
        """同步执行 MCP 工具调用。内部通过 asyncio.run 运行异步逻辑。"""
        ...
```

### 3.4 MCPManager 类

全局 MCP 连接管理器（注册在 tools/\_\_init\_\_.py 中）：

```python
class MCPManager:
    """管理所有 MCP 服务器连接。"""
    
    def __init__(self, servers: list[dict]):
        self.sessions: list[MCPSession] = []
        self.tools: list[MCPTool] = []
        for server_config in servers:
            session = MCPSession(**server_config)
            self.sessions.append(session)
    
    def initialize_all(self) -> list[MCPTool]:
        """初始化所有 MCP 连接，返回聚合的工具列表。"""
        ...
    
    def close_all(self):
        """关闭所有 MCP 连接。"""
        ...
```

### 3.5 传输协议支持

#### stdio 协议

通过子进程方式运行 MCP 服务器，通过 stdin/stdout 进行 JSON-RPC 通信：

```python
# 启动子进程
self._process = subprocess.Popen(
    [self.command, *self.args],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env={**os.environ, **(self.env or {})},
)

# 基于 mcp 库的 stdio 客户端
from mcp import StdioServerParameters, stdio_client
```

#### SSE 协议

通过 HTTP 连接 MCP 服务器，使用 Server-Sent Events 进行通信：

```python
# 基于 mcp 库的 SSE 客户端
from mcp import sse_client
```

---

## 4. 配置设计

### 4.1 环境变量

| 变量名 | 类型 | 描述 |
|--------|------|------|
| `CORECODER_MCP_SERVERS` | JSON 字符串 | MCP 服务器配置列表 |

### 4.2 配置文件（`~/.corecoder/mcp.json`）

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"]
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "<YOUR_TOKEN>"
      }
    },
    "custom-server": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

### 4.3 Config 类扩展

在 `config.py` 中新增 `mcp_servers` 字段：

```python
@dataclass
class Config:
    # ... 现有字段 ...
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    mcp_enabled: bool = True
    
    @classmethod
    def from_env(cls) -> "Config":
        # ... 现有逻辑 ...
        # 从环境变量或配置文件加载 MCP 配置
        mcp_json = os.getenv("CORECODER_MCP_SERVERS", "")
        if mcp_json:
            mcp_servers = json.loads(mcp_json)
        else:
            mcp_servers = cls._load_mcp_config_file()
        return cls(
            # ... 现有参数 ...
            mcp_servers=mcp_servers,
            mcp_enabled=os.getenv("CORECODER_MCP_ENABLED", "true").lower() == "true",
        )
```

---

## 5. 集成点

### 5.1 CLI 层（`cli.py`）

在 `main()` 中创建 Agent 时注入 MCP 工具：

```python
def main():
    # ... 现有逻辑 ...
    config = Config.from_env()
    
    # MCP 工具初始化
    mcp_tools: list[Tool] = []
    if config.mcp_enabled and config.mcp_servers:
        from .tools.mcp import MCPManager
        manager = MCPManager(config.mcp_servers)
        mcp_tools = manager.initialize_all()
        # 存入 agent 以便退出时清理
        config._mcp_manager = manager
    
    agent = Agent(
        llm=llm,
        tools=ALL_TOOLS + SPECIALIZED_AGENTS + mcp_tools,
        # ...
    )
```

### 5.2 Agent 层（`agent.py`）

Agent 核心循环无需任何修改。MCPTool 实现了标准的 `Tool` 接口，LLM 通过 function calling 自动发现并使用它们。

### 5.3 Tools 注册表（`tools/__init__.py`）

MCPTool 和 MCPManager 不需要注册到 ALL_TOOLS，而是由 CLI 层动态创建和注入。

---

## 6. 依赖项

### 必需依赖

```toml
# pyproject.toml
dependencies = [
    # ... 现有依赖 ...
    "mcp>=1.0.0",  # MCP 协议 Python SDK
]
```

### 可选依赖（如果不想增加核心包体积）

```toml
[project.optional-dependencies]
mcp = ["mcp>=1.0.0"]
```

---

## 7. 实施步骤

### 阶段 1：基础框架

| 步骤 | 内容 | 涉及文件 |
|------|------|----------|
| 1.1 | 安装 `mcp` Python SDK 到项目依赖 | `pyproject.toml` |
| 1.2 | 实现 `MCPSession` 类（stdio 协议支持） | `tools/mcp.py` |
| 1.3 | 实现 `MCPTool` 适配器类 | `tools/mcp.py` |
| 1.4 | 实现 `MCPManager` 管理类 | `tools/mcp.py` |

### 阶段 2：配置与集成

| 步骤 | 内容 | 涉及文件 |
|------|------|----------|
| 2.1 | `Config` 类新增 `mcp_servers` 字段和配置文件读取 | `config.py` |
| 2.2 | CLI 层注入 MCP 工具到 Agent | `cli.py` |
| 2.3 | 实现 SSE 协议支持 | `tools/mcp.py` |
| 2.4 | 实现 MCP 服务器错误处理和优雅降级 | `tools/mcp.py` |

### 阶段 3：测试与验证

| 步骤 | 内容 | 涉及文件 |
|------|------|----------|
| 3.1 | 编写单元测试：MCPTool 适配器 | `tests/test_mcp.py` |
| 3.2 | 编写单元测试：MCPSession 初始化 | `tests/test_mcp.py` |
| 3.3 | 编写集成测试：使用测试 MCP 服务器 | `tests/test_mcp.py` |
| 3.4 | 手动验证：连接 filesystem 等官方 MCP 服务器 | — |

---

## 8. 注意事项

### 8.1 同步 vs 异步

- MCP 官方 Python SDK 基于 `asyncio`，是异步的
- CoreCoder 的 `Tool.execute()` 是同步接口
- 方案：在 `MCPTool.execute()` 内部使用 `asyncio.run()` 包装异步调用
- 风险：`asyncio.run()` 不能在嵌套事件循环中调用（如 Jupyter/某些测试环境）
- 缓解：使用 `asyncio.get_event_loop().run_until_complete()` 或 `anyio` 兼容方案

### 8.2 工具命名冲突

- 不同 MCP 服务器可能提供同名工具
- 方案：MCPTool 的 `name` 使用 `{server_name}_{tool_name}` 格式
- 示例：`filesystem_read_file`、`github_create_issue`

### 8.3 生命周期管理

- MCP 服务器进程（stdio 模式）需要在 CoreCoder 退出时正确关闭
- 方案：在 Agent 或 CLI 层注册 `atexit` 处理器调用 `MCPManager.close_all()`
- 方案：Agent 新增 `__del__` 或 `close()` 方法清理 MCP 资源

### 8.4 安全考虑

- MCP 服务器拥有与 CoreCoder 相同的文件系统权限
- 用户应当只从可信来源安装 MCP 服务器
- 建议在文档中明确安全警告

### 8.5 向后兼容

- MCP 支持默认启用，但无任何配置时行为完全不变
- 用户可以不安装 `mcp` 包，此时 MCP 功能静默禁用
- 通过 `CORECODER_MCP_ENABLED=false` 可完全禁用

---

## 9. 附录：MCP 协议核心概念

### JSON-RPC 基础

MCP 基于 JSON-RPC 2.0 协议，客户端和服务器通过以下消息交互：

| 消息类型 | 方向 | 用途 |
|----------|------|------|
| `initialize` | 客户端 → 服务器 | 握手，协商协议版本 |
| `initialized` | 客户端 → 服务器 | 通知初始化完成 |
| `tools/list` | 客户端 → 服务器 | 获取工具列表 |
| `tools/call` | 客户端 → 服务器 | 调用指定工具 |
| `notifications/...` | 双向 | 事件通知 |
| `ping` | 双向 | 保活 |

### 工具 Schema 格式

MCP 服务器返回的工具格式与 OpenAI function calling 兼容：

```json
{
  "name": "read_file",
  "description": "读取文件内容",
  "inputSchema": {
    "type": "object",
    "properties": {
      "path": { "type": "string", "description": "文件路径" }
    },
    "required": ["path"]
  }
}
```

---

## 10. 参考资源

- [MCP 官方文档](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP 服务器列表](https://github.com/modelcontextprotocol/servers)
- [MCP 规范](https://spec.modelcontextprotocol.io/)
