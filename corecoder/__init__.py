"""CoreCoder - 受 Claude Code 架构启发的极简 AI 编码代理。"""

__version__ = "0.3.0"

from .agent import Agent
from .llm import LLM
from .config import Config
from .tools import ALL_TOOLS

__all__ = ["Agent", "LLM", "Config", "ALL_TOOLS", "__version__"]
