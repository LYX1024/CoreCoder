"""工具注册表。"""

from .bash import BashTool
from .read import ReadFileTool
from .write import WriteFileTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .agent import AgentTool
from .tree_sitter_tool import CodeQueryTool, StructReadTool

ALL_TOOLS = [
    BashTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    GlobTool(), # 文件名匹配
    GrepTool(), # 文件内容搜索
    CodeQueryTool(), # 语法树结构搜索
    StructReadTool(), # 语法树结构读取
    AgentTool(),
]


def get_tool(name: str):
    """按名称查找工具。"""
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    return None
