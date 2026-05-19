"""多层上下文压缩。

Claude Code 使用 4 层策略：
  1. HISTORY_SNIP   - 将旧工具输出修剪为一行摘要
  2. Microcompact   - LLM 驱动的旧轮次摘要（缓存）
  3. CONTEXT_COLLAPSE - 接近硬限制时的激进压缩
  4. Autocompact    - 定期后台压缩

CoreCoder 以 3 层实现相同思想：
  第 1 层 (tool_snip)   - 将冗长的工具结果替换为截断版本
  第 2 层 (summarize)   - LLM 驱动的旧对话摘要
  第 3 层 (hard_collapse) - 最后手段：只保留摘要 + 最近消息
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm import LLM


def _approx_tokens(text: str) -> int:
    """粗略的 token 计数。中英文混合内容约 3.5 字符/token。"""
    return len(text) // 3


def estimate_tokens(messages: list[dict]) -> int:
    """估计当前上下文的 token 计数"""
    total = 0
    for m in messages:
        if m.get("content"):
            total += _approx_tokens(m["content"])
        if m.get("tool_calls"):
            total += _approx_tokens(str(m["tool_calls"]))
    return total


class ContextManager:
    def __init__(self, max_tokens: int = 128_000):
        self.max_tokens = max_tokens
        # 层级阈值（占 max_tokens 的比例）
        self._snip_at = int(max_tokens * 0.50)    # 50% -> 裁剪工具输出
        self._summarize_at = int(max_tokens * 0.70)  # 70% -> LLM 摘要
        self._collapse_at = int(max_tokens * 0.90)   # 90% -> 硬折叠

    def maybe_compress(self, messages: list[dict], llm: LLM | None = None) -> bool:
        """根据需要应用压缩层级。如果执行了压缩则返回 True。"""
        current = estimate_tokens(messages)
        compressed = False

        # 第 1 层：裁剪冗长的工具输出
        if current > self._snip_at:
            if self._snip_tool_outputs(messages):
                compressed = True
                current = estimate_tokens(messages)

        # 第 2 层：LLM 驱动的旧轮次摘要
        if current > self._summarize_at and len(messages) > 10:
            if self._summarize_old(messages, llm, keep_recent=8):
                compressed = True
                current = estimate_tokens(messages)

        # 第 3 层：硬折叠 - 最后手段
        if current > self._collapse_at and len(messages) > 4:
            self._hard_collapse(messages, llm)
            compressed = True

        return compressed

    # @staticmethod：静态方法，不依赖类或实例
    @staticmethod
    def _snip_tool_outputs(messages: list[dict]) -> bool:
        """第 1 层：将超过 1500 字符的工具结果截断为前/后几行。

        这模仿了 Claude Code 的 HISTORY_SNIP，它将旧的工具输出
        替换为单行摘要以回收上下文空间。
        """
        changed = False
        for m in messages:
            # 判断需要截断的信息：1.role为tool，即工具调用信息    2.长度大于1500  3.行数大于6
            if m.get("role") != "tool":
                continue
            content = m.get("content", "")
            if len(content) <= 1500:
                continue
            lines = content.splitlines()
            if len(lines) <= 6:
                continue
            # 保留前 3 + 后 3 行
            snipped = (
                "\n".join(lines[:3])
                + f"\n... ({len(lines)} lines, snipped to save context) ...\n"
                + "\n".join(lines[-3:])
            )
            m["content"] = snipped
            changed = True
        return changed

    def _summarize_old(self, messages: list[dict], llm: LLM | None,
                       keep_recent: int = 8) -> bool:
        """第 2 层：摘要旧对话，保留最近消息不变。"""
        if len(messages) <= keep_recent:
            return False

        # 默认：将倒数第八条之前的信息标记为旧信息进行摘要
        old = messages[:-keep_recent]
        tail = messages[-keep_recent:]

        summary = self._get_summary(old, llm)

        messages.clear()
        messages.append({
            "role": "user",
            "content": f"[上下文已压缩 - 对话摘要]\n{summary}",
        })
        messages.append({
            "role": "assistant",
            "content": "收到，我已拥有此前对话的上下文。",
        })
        # extend：将 tail 列表中的每个元素逐个追加
        messages.extend(tail)
        return True

    def _hard_collapse(self, messages: list[dict], llm: LLM | None):
        """第 3 层：紧急压缩。只保留最后 4 条消息 + 摘要。"""
        tail = messages[-4:] if len(messages) > 4 else messages[-2:]
        summary = self._get_summary(messages[:-len(tail)], llm)

        messages.clear()
        messages.append({
            "role": "user",
            "content": f"[硬重置上下文]\n{summary}",
        })
        messages.append({
            "role": "assistant",
            "content": "上下文已恢复。从断点处继续。",
        })
        messages.extend(tail)

    def _get_summary(self, messages: list[dict], llm: LLM | None) -> str:
        """通过 LLM 生成摘要，或降级为信息提取。"""
        flat = self._flatten(messages)

        if llm:
            try:
                resp = llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "将这段对话压缩为简短摘要。"
                                "保留：编辑过的文件路径、关键决策、"
                                "遇到的错误、当前任务状态、"
                                "类的属性、方法名。"
                                "丢弃：冗长的命令输出、代码清单、"
                                "重复的来回对话。"
                            ),
                        },
                        {"role": "user", "content": flat[:15000]},
                    ],
                )
                return resp.content
            except Exception:
                pass

        # 降级：提取关键行
        return self._extract_key_info(messages)

    @staticmethod
    def _flatten(messages: list[dict]) -> str:
        """ 将消息列表平铺为长字符串 """
        parts = []
        for m in messages:
            role = m.get("role", "?")
            text = m.get("content", "") or ""
            if text:
                parts.append(f"[{role}] {text[:400]}")
        return "\n".join(parts)

    @staticmethod
    def _extract_key_info(messages: list[dict]) -> str:
        """降级方案：无需 LLM，提取文件路径、错误和决策。"""
        import re
        files_seen = set()
        errors = []
        decisions = []

        for m in messages:
            text = m.get("content", "") or ""
            # 提取文件路径
            for match in re.finditer(r'[\w./\-]+\.\w{1,5}', text):
                files_seen.add(match.group())
            # 提取错误行
            for line in text.splitlines():
                if 'error' in line.lower() or 'Error' in line:
                    errors.append(line.strip()[:150])

        parts = []
        if files_seen:
            parts.append(f"Files touched: {', '.join(sorted(files_seen)[:20])}")
        if errors:
            parts.append(f"Errors seen: {'; '.join(errors[:5])}")
        return "\n".join(parts) or "(no extractable context)"
