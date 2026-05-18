"""核心代理循环。

这是 CoreCoder 的心脏。模式很简单：

    用户消息 -> LLM（带工具）-> 工具调用？-> 执行 -> 循环
                                -> 文本回复？-> 返回给用户

它不断循环，直到 LLM 返回纯文本（无工具调用），
此时表示工作完成，准备向用户报告。
"""

import json
import concurrent.futures
from .llm import LLM
from .tools import ALL_TOOLS, get_tool
from .tools.base import Tool
from .tools.agent import AgentTool
from .prompt import system_prompt
from .context import ContextManager


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool] | None = None,
        max_context_tokens: int = 128_000,
        max_rounds: int = 50,
    ):
        self.llm = llm
        self.tools = tools if tools is not None else ALL_TOOLS
        self.messages: list[dict] = []
        self.context = ContextManager(max_tokens=max_context_tokens)
        self.max_rounds = max_rounds
        self._system = system_prompt(self.tools)

        # 将创建子agent封装为一个tool，在此处指定子agent的父为self
        for t in self.tools:
            if isinstance(t, AgentTool):
                t._parent_agent = self

    # 拼接系统prompt与用户请求上下文
    def _full_messages(self) -> list[dict]:
        return [{"role": "system", "content": self._system}] + self.messages

    # 遍历工具搜索schema()，即符合openAI格式的function calling
    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools]

    def chat(self, user_input: str, on_token=None, on_tool=None) -> str:
        """处理一条用户消息。可能涉及多个 LLM/工具轮次。"""
        self.messages.append({"role": "user", "content": user_input})
        # 处理消息前检查是否需要压缩上下文
        self.context.maybe_compress(self.messages, self.llm)

        for _ in range(self.max_rounds):
            resp = self.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token,
            )

            # 无工具调用 -> LLM 完成，返回文本
            if not resp.tool_calls:
                self.messages.append(resp.message)
                return resp.content

            # 检测重复工具调用（在追加 assistant 消息之前检测，避免遗留孤立 tool_calls）
            warning = self._check_repetition(resp.tool_calls)
            if warning:
                self.messages.append({
                    "role": "user",
                    "content": warning,
                })
                continue

            self.messages.append(resp.message)

            # 工具调用 -> 执行（多个时并行，类似 Claude Code 的StreamingToolExecutor，并发运行独立工具）
            if len(resp.tool_calls) == 1:
                # 单个工具执行
                tc = resp.tool_calls[0]
                if on_tool:
                    on_tool(tc.name, tc.arguments)
                result = self._exec_tool(tc)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            else:
                # 多个工具调用的并行执行
                results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                for tc, result in zip(resp.tool_calls, results):
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

            # 如果工具输出较大则压缩
            self.context.maybe_compress(self.messages, self.llm)

        return "(reached maximum tool-call rounds)"

    def _exec_tool(self, tc) -> str:
        """执行单个工具调用，返回结果字符串。"""
        tool = get_tool(tc.name)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'"
        try:
            # **：字典解包 -> 键变成参数名，值变成参数值
            return tool.execute(**tc.arguments)
        except TypeError as e:
            return f"Error: bad arguments for {tc.name}: {e}"
        except Exception as e:
            return f"Error executing {tc.name}: {e}"

    def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[str]:
        """使用线程并发运行多个工具调用。

        灵感来自 Claude Code 的 StreamingToolExecutor，它在模型仍在生成时
        就开始执行工具。我们简化为：当模型一次返回 N 个工具调用时，并行运行它们。
        """
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self._exec_tool, tc) for tc in tool_calls]
            return [f.result() for f in futures]

    def _check_repetition(self, tool_calls: list) -> str | None:
        """检查最近的历史中是否有重复工具调用，有则返回警告消息。"""
        history = self.messages[-10:]  # 只看最近 10 条
        recent_keys: list[tuple[str, set]] = []
        for m in history:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc_data in m["tool_calls"]:
                    fn = tc_data.get("function", {})
                    name = fn.get("name", "")
                    # 历史中的 arguments 是 JSON 字符串，转 dict 取 key 集合
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    recent_keys.append((name, set(args.keys())))

        for tc in tool_calls:
            current_keys = set(tc.arguments.keys())
            count = sum(
                1 for name, keys in recent_keys
                if name == tc.name and keys == current_keys
            )
            if count >= 2:
                return (
                    f"[系统检测到重复调用] 工具 '{tc.name}' 已调用 {count+1} 次（含本次），"
                    f"参数模式相同。请先检查已有结果是否满足需求，"
                    f"避免做无用功。如需继续，请说明理由。"
                )
        return None

    def reset(self):
        """清除对话历史。"""
        self.messages.clear()
