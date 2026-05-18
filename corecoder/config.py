"""配置 - 环境变量和默认值。"""

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv():
    """从 cwd 加载 .env，向上遍历到 home 目录。若无 python-dotenv 则无操作。"""
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
        # cls：cls 是类方法的第一个参数，表示类本身
        return cls(
            model=os.getenv("CORECODER_MODEL", "gpt-4o"),
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("CORECODER_BASE_URL"),
            max_tokens=int(os.getenv("CORECODER_MAX_TOKENS", "4096")),
            temperature=float(os.getenv("CORECODER_TEMPERATURE", "0")),
            max_context_tokens=int(os.getenv("CORECODER_MAX_CONTEXT", "128000")),
            provider=os.getenv("CORECODER_PROVIDER", "openai"),
        )
