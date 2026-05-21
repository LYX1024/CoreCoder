"""配置 - 环境变量和默认值。"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv():
    """
    加载环境变量
    从 cwd 加载 .env，向上遍历到 home 目录。若无 python-dotenv 则无操作。
    """
    try:
        from dotenv import load_dotenv
        # 先搜索 cwd，然后向上搜索父目录直到 ~
        env_path = Path(".env")
        if not env_path.exists():
            cur = Path.cwd()
            home = Path.home()
            while cur != home and cur != cur.parent:
                candidate = cur / ".env"
                if candidate.exists():
                    env_path = candidate
                    break
                cur = cur.parent
        load_dotenv(env_path, override=False)
    except ImportError:
        pass  # python-dotenv 未安装，静默跳过


def _load_mcp_config_file() -> dict[str, dict]:
    """加载 MCP 服务器配置。

    查找顺序（先到先用）：
    1. 项目本地：corecoder/mcp_servers/mcp.json
    2. 全局配置：~/.corecoder/mcp.json
    3. 环境变量：CORECODER_MCP_SERVERS（调用方处理）

    Returns:
        MCP 服务器配置字典，格式为 {"server_name": {command/args/url}}
    """
    # 项目本地路径
    local_path = Path(__file__).parent / "mcp_servers" / "mcp.json"
    if local_path.exists():
        try:
            with open(local_path, encoding="utf-8") as f:
                data = json.load(f)

            # 将 args 中的相对路径解析为绝对路径，避免运行目录不同导致找不到文件
            corecoder_dir = Path(__file__).parent.resolve()
            for server_cfg in data.get("mcpServers", {}).values():
                args = server_cfg.get("args", [])
                resolved = []
                for arg in args:
                    arg_path = Path(arg)
                    if not arg_path.is_absolute() and arg_path.suffix:
                        # 相对路径 -> 相对于 corecoder/ 目录解析
                        resolved.append(str(corecoder_dir / arg))
                    else:
                        resolved.append(arg)
                server_cfg["args"] = resolved

            return data.get("mcpServers", {})
        except (json.JSONDecodeError, OSError) as e:
            print(f"[Config] Warning: Failed to load {local_path}: {e}")

    # 全局配置路径
    home_path = Path.home() / ".corecoder" / "mcp.json"
    if home_path.exists():
        try:
            with open(home_path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("mcpServers", {})
        except (json.JSONDecodeError, OSError) as e:
            print(f"[Config] Warning: Failed to load {home_path}: {e}")
    return {}


# @dataclass：相当于Java库Lombok的@Data
@dataclass
class Config:
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    max_context_tokens: int = 128_000
    provider: str = "openai"
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    mcp_enabled: bool = True

    # @classmethod：将方法转为类方法（不依赖实例，但需要依赖类，第一个参数cls是类本身）
    @classmethod
    def from_env(cls) -> "Config":
        # 如果存在则加载 .env（不会覆盖已有环境变量）
        _load_dotenv()
        # 自动拾取常见的环境变量
        api_key = (
            os.getenv("CORECODER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or ""
        )
        # 从环境变量或配置文件加载 MCP 配置
        mcp_servers: dict[str, dict] = {}
        mcp_json = os.getenv("CORECODER_MCP_SERVERS", "")
        if mcp_json:
            try:
                mcp_servers = json.loads(mcp_json)
            except json.JSONDecodeError as e:
                print(f"[Config] Warning: CORECODER_MCP_SERVERS is not valid JSON: {e}")
        else:
            mcp_servers = _load_mcp_config_file()

        # cls：cls 是类方法的第一个参数，表示类本身
        return cls(
            model=os.getenv("CORECODER_MODEL", "gpt-4o"),
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("CORECODER_BASE_URL"),
            max_tokens=int(os.getenv("CORECODER_MAX_TOKENS", "4096")),
            temperature=float(os.getenv("CORECODER_TEMPERATURE", "0")),
            max_context_tokens=int(os.getenv("CORECODER_MAX_CONTEXT", "128000")),
            provider=os.getenv("CORECODER_PROVIDER", "openai"),
            mcp_servers=mcp_servers,
            mcp_enabled=os.getenv("CORECODER_MCP_ENABLED", "true").lower() == "true",
        )
