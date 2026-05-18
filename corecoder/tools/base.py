"""所有工具的基础类。"""

from abc import ABC, abstractmethod


class Tool(ABC):
    """极简工具接口。继承此类以添加新能力。"""

    """
        工具名称
        工具描述
        工具参数
    """
    name: str
    description: str
    parameters: dict  # 函数参数的 JSON 模板语法

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """运行工具并返回文本结果。"""
        ...

    def schema(self) -> dict:
        """OpenAI 的function calling。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
