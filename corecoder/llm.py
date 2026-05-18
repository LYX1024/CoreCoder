"""LLM 提供者层 - 对 OpenAI 兼容 API 的轻量封装。

由于大多数提供者（DeepSeek、Qwen、Kimi、GLM、Ollama 等）都提供
OpenAI 兼容的端点，我们直接使用 openai SDK。通过更改
OPENAI_BASE_URL + OPENAI_API_KEY 切换提供者。仅此而已。

对于不兼容 OpenAI 的提供者（AWS Bedrock、Google Vertex 等），
使用 LiteLLM 后端，它通过统一的接口路由到 100+ 提供者。
设置 CORECODER_PROVIDER=litellm。
"""

import json
import time
from dataclasses import dataclass, field

from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    reasoning_content: str = "" # 适配deepseek推理模型
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    # @property：类方法转为属性式访问     obj.attr() -> obj.attr
    @property
    def message(self) -> dict:
        """转换为 OpenAI 消息格式以便追加到历史记录。"""
        msg: dict = {"role": "assistant", "content": self.content or None}
        # DeepSeek 推理模型需要保留 reasoning_content 回传
        if self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        # 列表推导式：规范function calling格式
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg


# 常见llm价格映射表
# pricing per million tokens: (input, output)
# sources: openai.com/api/pricing, api-docs.deepseek.com, platform.claude.com,
#          platform.moonshot.ai, alibabacloud.com/help/en/model-studio
_PRICING = {
    # OpenAI - current flagships
    "gpt-5.4": (2.5, 15),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4-nano": (0.2, 1.25),
    "o4-mini": (1.1, 4.4),
    # OpenAI - previous gen (still widely used)
    "gpt-4.1": (2, 8),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10),
    "gpt-4o-mini": (0.15, 0.6),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # Anthropic Claude
    "claude-opus-4-6": (5, 25),
    "claude-sonnet-4-6": (3, 15),
    "claude-haiku-4-5": (1, 5),
    # Alibaba Qwen
    "qwen3-max": (0.78, 3.9),
    "qwen3-plus": (0.26, 0.78),
    "qwen-max": (0.78, 3.9),
    # Moonshot Kimi
    "kimi-k2.5": (0.6, 3),
}


class LLM:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        **kwargs,
    ):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=300)
        self.extra = kwargs  # temperature, max_tokens, etc.
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    @property
    def estimated_cost(self) -> float | None:
        """大致的费用估算（美元）。如果模型不在价格表中则返回 None。"""
        pricing = _PRICING.get(self.model)
        if not pricing:
            return None
        input_rate, output_rate = pricing
        return (
            self.total_prompt_tokens * input_rate / 1_000_000
            + self.total_completion_tokens * output_rate / 1_000_000
        )

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
    ) -> LLMResponse:
        """发送消息，流式返回响应，处理工具调用。"""
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.extra,
        }
        if tools:
            params["tools"] = tools

        # stream_options 是 OpenAI 的扩展；并非所有提供者都支持
        # 尝试从流式输出获取用量信息
        try:
            params["stream_options"] = {"include_usage": True}
            stream = self._call_with_retry(params)
        except Exception:
            params.pop("stream_options", None)
            stream = self._call_with_retry(params)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_map: dict[int, dict] = {}  # 索引 -> {id, name, arguments_str}
        prompt_tok = 0
        completion_tok = 0

        # 调用openAI获取的是一个惰性生成器（此处为stream），将SSE回复转化为Python的ChatCompletionChunk对象
        for chunk in stream:
            # 用量信息在最后一个块中提供
            if chunk.usage:
                prompt_tok = chunk.usage.prompt_tokens
                completion_tok = chunk.usage.completion_tokens

            # 跳过空块
            if not chunk.choices:
                continue

            # 获取增量字段
            delta = chunk.choices[0].delta
            # 文本增量，直接加到输出
            if delta.content:
                content_parts.append(delta.content)
                if on_token:
                    on_token(delta.content)

            # DeepSeek 推理模型特有字段，逐块累积
            rc = getattr(delta, "reasoning_content", None) or (delta.model_extra or {}).get("reasoning_content")
            if rc:
                reasoning_parts.append(rc)

            # 解析工具调用增量
            if delta.tool_calls:
                # 创建一个tc增量
                for tc_delta in delta.tool_calls:
                    # 创建tc增量索引
                    idx = tc_delta.index
                    # 规范tc增量格式
                    if idx not in tc_map:
                        tc_map[idx] = {"id": "", "name": "", "args": ""}
                    # 填充tc id
                    if tc_delta.id:
                        tc_map[idx]["id"] = tc_delta.id
                    # 填充tc方法名与参数
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_map[idx]["args"] += tc_delta.function.arguments

        # 解析累积的工具调用
        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                # 解析累加得到的参数    JSON 字符串 -> 字典
                args = json.loads(raw["args"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok

        return LLMResponse(
            content="".join(content_parts),
            reasoning_content="".join(reasoning_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
        )

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        """重试机制 """
        for attempt in range(max_retries):
            try:
                return self.client.chat.completions.create(**params)
            # 临时性错误：采用指数重试
            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                time.sleep(wait)
            # 调用错误：根据状态码决定策略
            except APIError as e:
                # 5xx = 服务器错误，重试
                if e.status_code and e.status_code >= 500 and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                # 4xx = 客户端错误，不重试，直接抛出
                else:
                    raise


class LiteLLM(LLM):
    """通过 LiteLLM 的 LLM 后端，支持 100+ 提供者。

    当目标提供者不兼容 OpenAI 时使用此后端
    （AWS Bedrock、Google Vertex、Cohere 等），或者想要
    通过更改模型字符串在任意提供者间切换的统一接口。

    设置 CORECODER_PROVIDER=litellm 并使用 LiteLLM 模型字符串，
    例如 ``anthropic/claude-3-haiku``、``bedrock/anthropic.claude-v2``、
    ``vertex_ai/gemini-pro`` 等。
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        **kwargs,
    ):
        # 跳过创建 OpenAI 客户端的 LLM.__init__
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.extra = kwargs
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
    ) -> LLMResponse:
        """通过 litellm 发送消息，流式返回响应，处理工具调用。"""
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.extra,
        }
        if tools:
            params["tools"] = tools

        stream = self._call_with_retry(params)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_map: dict[int, dict] = {}
        prompt_tok = 0
        completion_tok = 0

        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                prompt_tok = getattr(usage, "prompt_tokens", 0) or 0
                completion_tok = getattr(usage, "completion_tokens", 0) or 0

            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta

            if getattr(delta, "content", None):
                content_parts.append(delta.content)
                if on_token:
                    on_token(delta.content)

            # DeepSeek 推理模型特有字段，逐块累积
            rc = getattr(delta, "reasoning_content", None) or (getattr(delta, "model_extra", None) or {}).get("reasoning_content")
            if rc:
                reasoning_parts.append(rc)

            if getattr(delta, "tool_calls", None):
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_map:
                        tc_map[idx] = {"id": "", "name": "", "args": ""}
                    if tc_delta.id:
                        tc_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_map[idx]["args"] += tc_delta.function.arguments

        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                args = json.loads(raw["args"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            parsed.append(ToolCall(id=raw["id"], name=raw["name"], arguments=args))

        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok

        return LLMResponse(
            content="".join(content_parts),
            reasoning_content="".join(reasoning_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
        )

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        """通过 litellm 以指数退避重试临时错误。"""
        import litellm

        params["drop_params"] = True
        if self.api_key:
            params["api_key"] = self.api_key
        if self.base_url:
            params["api_base"] = self.base_url

        for attempt in range(max_retries):
            try:
                return litellm.completion(**params)
            except Exception as e:
                err = str(e).lower()
                is_transient = any(
                    kw in err
                    for kw in ["rate_limit", "timeout", "connection", "502", "503", "529"]
                )
                is_server = any(kw in err for kw in ["500", "502", "503", "504"])
                if (is_transient or is_server) and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise
