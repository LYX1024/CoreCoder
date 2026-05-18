"""子代理生成（灵感来自 Claude Code 的 AgentTool，1397 行）。

思路：对于复杂的子任务，生成一个独立的代理，拥有自己的
对话历史记录和工具访问权限。这让主代理可以委派像
"去研究这个代码库并报告"这样的工作，而不会污染
自己的上下文窗口。

子代理运行到完成并返回文本摘要。
"""

from .base import Tool


class AgentTool(Tool):
    name = "agent"
    description = (
        # 生成一个子agent去处理一个复杂子任务
        "Spawn a sub-agent to handle a complex sub-task independently. "
        # 子agent有他自己的上下文和工具列表。使用这个机制来实现：
        "The sub-agent has its own context and tool access. Use this for: "
        # 研究代码库，独立实现一个多步骤的更新操作
        "researching a codebase, implementing a multi-step change in isolation, "
        # 或者一些适合创一个新上下文窗口的任务
        "or any task that would benefit from a fresh context window."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                # 描述：这个子agent需要干什么
                "description": "What the sub-agent should accomplish",
            },
        },
        # 必要的参数task
        "required": ["task"],
    }

    # 由 Agent.__init__ 在构造后设置
    _parent_agent = None

    def execute(self, task: str) -> str:
        if self._parent_agent is None:
            return "Error: agent tool not initialized (no parent agent)"

        # 在此处导入以避免循环依赖
        from ..agent import Agent

        parent = self._parent_agent
        sub = Agent(
            llm=parent.llm,
            # 工具列表参数显式禁止传入agent，禁止递归代理
            tools=[t for t in parent.tools if t.name != "agent"],
            max_context_tokens=parent.context.max_tokens,
            max_rounds=20,
        )

        try:
            result = sub.chat(task)
            # 截断长结果以避免撑爆父代理的上下文
            if len(result) > 5000:
                result = result[:4500] + "\n...（子代理输出已截断）"
            return f"[Sub-agent completed]\n{result}"
        except Exception as e:
            return f"Sub-agent error: {e}"


class SpecializedAgentTool(Tool):
    """可配置的专业子代理工具。每个实例是一个独立角色的 agent。

    用法：
        review_agent = SpecializedAgentTool(
            name="review",
            description="审查代码质量",
            system_prompt="你是代码审查专家...",
            tools=[GlobTool(), GrepTool(), ReadFileTool()],
            model="deepseek-chat",       # 可选，默认复用主 agent 模型
        )
    """

    def __init__(
        self,
        name: str,
        description: str,
        system_prompt: str,
        tools: list[Tool] | None = None,
        model: str | None = None,
        max_rounds: int = 20,
    ):
        self.name = name
        self.description = description
        self._system_prompt = system_prompt
        self._tools = tools or []
        self._model = model
        self._max_rounds = max_rounds
        self._parent_agent = None
        self.parameters = {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": f"交给 {name} 的任务描述",
                },
            },
            "required": ["task"],
        }

    def execute(self, task: str) -> str:
        if self._parent_agent is None:
            return f"Error: {self.name} agent not initialized (no parent agent)"

        from ..agent import Agent
        from ..llm import LLM

        parent = self._parent_agent

        # 如果指定了不同模型，创建独立 LLM（继承父 agent 的 API 凭据）
        if self._model:
            llm = LLM(
                model=self._model,
                api_key=parent.llm.client.api_key if hasattr(parent.llm, 'client') else getattr(parent.llm, 'api_key', ''),
                base_url=parent.llm.client.base_url if hasattr(parent.llm, 'client') else getattr(parent.llm, 'base_url', None),
            )
        else:
            llm = parent.llm

        sub = Agent(
            llm=llm,
            tools=self._tools,
            max_context_tokens=parent.context.max_tokens,
            max_rounds=self._max_rounds,
            custom_prompt=self._system_prompt,
        )

        try:
            # 从父 agent 透传回调，让子 agent 的流式输出对用户可见
            on_token = getattr(parent, '_current_on_token', None)
            on_tool = getattr(parent, '_current_on_tool', None)
            result = sub.chat(task, on_token=on_token, on_tool=on_tool)
            if len(result) > 5000:
                result = result[:4500] + "\n...（子代理输出已截断）"
            return f"[{self.name} completed]\n{result}"
        except Exception as e:
            return f"{self.name} agent error: {e}"
