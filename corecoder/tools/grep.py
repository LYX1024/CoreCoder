"""支持正则表达式的内容搜索。"""

import re
from pathlib import Path
from .base import Tool

# 跳过这些目录以避免干扰
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build"}


class GrepTool(Tool):
    name = "grep"
    description = (
        "使用正则表达式搜索文件内容。"
        "返回匹配行及其文件路径和行号。"
    )
    parameters = {
        "type": "object",
        "properties": {
            # 用于搜索的正则表达式
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search (default: cwd)",
            },
            "include": {
                "type": "string",
                "description": "Only search files matching this glob (e.g. '*.py')",
            },
        },
        "required": ["pattern"],
    }

    def execute(self, pattern: str, path: str = ".", include: str | None = None) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Invalid regex: {e}"

        base = Path(path).expanduser().resolve()
        if not base.exists():
            return f"Error: {path} not found"

        if base.is_file():
            files = [base] # 单文件模式
        else:
            files = self._walk(base, include) # 目录模式，递归收集文件

        matches = []
        for fp in files:
            try:
                text = fp.read_text(errors="ignore")
            except OSError:
                continue
            # enumerate：遍历同时标行号
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{fp}:{lineno}: {line.rstrip()}")
                    if len(matches) >= 200:
                        matches.append("... (200 match limit reached)")
                        return "\n".join(matches)

        return "\n".join(matches) if matches else "No matches found."

    @staticmethod
    def _walk(root: Path, include: str | None) -> list[Path]:
        """遍历目录树，跳过无用的目录。"""
        results = []
        for item in root.rglob(include or "*"):
            # 跳过隐藏/无用的目录
            if any(part in _SKIP_DIRS for part in item.parts):
                continue
            if item.is_file():
                results.append(item)
            if len(results) >= 5000:
                break
        return results
