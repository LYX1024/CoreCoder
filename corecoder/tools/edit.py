"""搜索替换式文件编辑（Claude Code 的关键创新）。

核心思想：LLM 不发送整个文件重写或行号补丁，
而是指定一个*精确*的子串来查找及其替换内容。
子串必须在文件中恰好出现一次，这消除了歧义，
使编辑安全且可审查。
"""

import difflib
from pathlib import Path

from .base import Tool

# 追踪本次会话中修改的文件，用于 /diff 命令
_changed_files: set[str] = set()


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "通过精确字符串匹配编辑文件。"
        "为安全起见，old_string 必须在文件中恰好出现一次。"
        "包含足够的周围上下文以确保唯一性。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to edit",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to find (must be unique in file)",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def execute(self, file_path: str, old_string: str, new_string: str) -> str:
        try:
            p = Path(file_path).expanduser().resolve()
            # Path.expanduser()  — 把路径开头的 ~ 换成用户家目录，如 "~/doc" -> "C:/Users/xxx/doc"
            # Path.resolve()     — 把相对路径换成绝对路径，去掉 ".." 和 "."，如 "./foo/../bar" -> "D:/xx/bar"
            if not p.exists():
                return f"Error: {file_path} not found"

            content = p.read_text()
            # str.count(sub)  — 统计 sub 在字符串中出现的次数，不重叠计数
            occurrences = content.count(old_string)

            if occurrences == 0:
                preview = content[:500] + ("..." if len(content) > 500 else "")
                return (
                    f"错误：在 {file_path} 中未找到 old_string。\n"
                    f"文件开头为：\n{preview}"
                )
            if occurrences > 1:
                return (
                    f"错误：old_string 在 {file_path} 中出现了 {occurrences} 次。"
                    f"请包含更多周围行以确保唯一性。"
                )

            # 替换内容
            new_content = content.replace(old_string, new_string, 1)
            p.write_text(new_content)
            _changed_files.add(str(p))

            # 生成统一差异格式，让用户/LLM 能看到具体变化
            diff = _unified_diff(content, new_content, str(p))
            return f"Edited {file_path}\n{diff}"
        except Exception as e:
            return f"Error: {e}"


def _unified_diff(old: str, new: str, filename: str, context: int = 3) -> str:
    """生成旧文件和新文件内容之间的紧凑统一差异。"""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    # difflib.unified_diff(old, new, fromfile, tofile, n=context)
    #   比较两段文本的差异，输出类 git diff 的统一格式：
    #   --- a/file.py       ← 旧文件标记
    #   +++ b/file.py       ← 新文件标记
    #   @@ -行号,范围 +行号,范围 @@
    #   -被删的行          ← 前面带 - 的表示被删除
    #   +新增的行          ← 前面带 + 的表示被添加
    #   n=context 参数控制每个差异块上下各保留几行上下文，默认 3 行
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}", tofile=f"b/{filename}",
        n=context,
    )
    result = "".join(diff)
    # 截断过大的 diff
    if len(result) > 3000:
        result = result[:2500] + "\n...（差异已截断）\n"
    return result
