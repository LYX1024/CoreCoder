"""语法树解析与结构查询工具。
使用 tree-sitter 将源代码解析为语法树（CST），
支持按函数名/类名查找定义、按结构读取代码块。
解析结果缓存在磁盘上（含 git hash 检验），避免重复解析。
"""

import hashlib
import os
import pickle
import time
from pathlib import Path

from .base import Tool

# ── 语法语言映射 ────────────────────────────────────────────
# 扩展点：在此处添加新语言
_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    # ".js": "javascript",
    # ".ts": "typescript",
    # ".go": "go",
    # ".rs": "rust",
}

# 各语言的定义节点类型映射
_DEF_NODE_TYPES: dict[str, tuple[str, ...]] = {
    "python": ("function_definition", "class_definition"),
    "java": ("method_declaration", "class_declaration"),
}

# 查询语句模板：按语言生成不同的查询
_QUERY_TEMPLATES: dict[str, str] = {
    "python": (
        "["
        "(function_definition name: (identifier) @name)"
        "(class_definition name: (identifier) @name)"
        "]"
    ),
    "java": (
        "["
        "(method_declaration name: (identifier) @name)"
        "(class_declaration name: (identifier) @name)"
        "]"
    ),
}

# tree-sitter 语言模块的延迟加载
_LANGUAGE_MODULES: dict[str, object] = {}
_PARSER_CACHE: dict[str, object] = {}

# ── 缓存目录 ────────────────────────────────────────────────
_CACHE_DIR = Path.home() / ".corecoder" / "ast_cache"
_CACHE_TTL = 3 * 86400  # 3 天


""" 
tree-sitter使用说明(https://github.com/tree-sitter/py-tree-sitter)
1.根据语言获取其对应tree-sitter的Language对象    _get_language(ext: str)
2.获取基础解析器实例    _get_parser(ext: str)
3.解析代码获得tree对象，格式如下
"""
# # tree是c扩展，转化为字节流
# tree = parser.parse(
#     bytes(
#         """
# def foo():
#     if bar:
#         baz()
# """,
#         "utf8"
#     )
# )
""" 
4.Node的导航：
    4.1：child_by_field_name传固定字段精准跳转函数节点
    4.2：TreeCursor高效遍历
5.S-表达式：理解树的结构
    print(str(root_node))能输出整个语法树
6.Query：声明式搜索——支持类似SQL语句搜索数据库的搜索方式，如下
"""
# query = Query(
#     PY_LANGUAGE,
#     """
# (function_definition          # 匹配函数定义
#   name: (identifier) @func.def  # 捕获函数名，命名为 @func.def
#   body: (block) @func.body)     # 捕获函数体，命名为 @func.body

# (call                          # 匹配函数调用
#   function: (identifier) @func.call # 捕获调用名，命名为 @func.call
#   arguments: (argument_list) @func.args)
# """,
# )

# # 执行查询
# captures = query_cursor.captures(tree.root_node)

# # captures["func.def"] → [function_name_node]   函数定义名节点
# # captures["func.body"] → [function_body_node]   函数体节点
# # captures["func.call"] → [function_call_name_node] 函数调用名节点
# # captures["func.args"] → [function_call_args_node] 调用参数节点
"""
7.增量解析 略
8.read callable处理超大文件 略
9.root_node.has_error
"""


def _get_language(ext: str):
    """按文件扩展名加载对应的 tree-sitter 语法模块。"""
    lang_name = _LANGUAGE_MAP.get(ext)
    if not lang_name:
        return None
    if lang_name in _LANGUAGE_MODULES:
        return _LANGUAGE_MODULES[lang_name]

    try:
        if lang_name == "python":
            import tree_sitter_python as mod
        elif lang_name == "java":
            import tree_sitter_java as mod
        else:
            return None
        from tree_sitter import Language
        lang = Language(mod.language())
        _LANGUAGE_MODULES[lang_name] = lang
        return lang
    except ImportError:
        return None


def _get_parser(ext: str):
    """获取（或创建）对应语言的 parser 实例。"""
    if ext in _PARSER_CACHE:
        return _PARSER_CACHE[ext]

    lang = _get_language(ext)
    if lang is None:
        return None

    from tree_sitter import Parser
    parser = Parser(lang)
    _PARSER_CACHE[ext] = parser
    return parser


"""
缓存建立与检验策略（以及策略迭代）
Plan A: 建立缓存时对项目全文件做哈希，校验时检验哈希。支持文件粒度的缓存重建，但在大型项目效率差。
Plan B: 基础git commit哈希值。仅支持项目粒度的缓存重建，微小修改也是一次全新commit。
Plan C (Now): 关心修改时间和文件大小判断文件是否修改，不考虑文件内容。支持文件粒度的缓存重建，有极小误判风险，权衡之计。
"""
def _file_stamp(file_path: str | Path) -> tuple[int, int]:
    """读取 mtime（纳秒）+ 文件大小，生成文件变更检测标记。"""
    s = Path(file_path).stat()
    return (s.st_mtime_ns, s.st_size)


def _cache_path(file_path: str | Path) -> Path:
    """根据文件路径计算缓存文件路径。"""
    abs_path = str(Path(file_path).resolve())
    path_hash = hashlib.sha256(abs_path.encode()).hexdigest()
    return _CACHE_DIR / f"{path_hash}.pkl"


# ── AST 缓存 ────────────────────────────────────────────────

class AstCache:
    """语法树磁盘缓存（mtime + 预计算索引）。

    tree_sitter生成的Tree是 C 扩展对象，无法pickle（序列化），因此缓存：
    - 文件 (mtime, size) —— 检测文件是否变更
    - 预计算的函数/类定义索引(解析Tree对象并处理为便于解析和查找的结构) —— 避免重复查询
    """

    @staticmethod
    def get(file_path: str | Path):
        """查询缓存：返回 (index, stamp) 或 (None, None)。"""

        # 查询缓存文件
        cp = _cache_path(file_path)
        if not cp.exists():
            return None, None

        # 缓存是否过期
        age = time.time() - cp.stat().st_mtime
        if age > _CACHE_TTL:
            cp.unlink(missing_ok=True)
            return None, None

        try:
            with open(cp, "rb") as f:
                cached_mtime_ns: int = pickle.load(f)
                cached_size: int = pickle.load(f)
                index = pickle.load(f)

            # 缓存是否变化
            current_mtime_ns, current_size = _file_stamp(file_path)
            if current_mtime_ns == cached_mtime_ns and current_size == cached_size:
                return index, (cached_mtime_ns, cached_size)
        except Exception:
            pass

        return None, None

    @staticmethod
    def put(file_path: str | Path, stamp: tuple[int, int], index: list):
        """建立缓存：将文件标记 + 预计算索引写入缓存。"""
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cp = _cache_path(file_path)
        try:
            with open(cp, "wb") as f:
                # 写入顺序：时间戳 -> 文件大小 -> 索引，与读取顺序严格一致
                pickle.dump(stamp[0], f)  # mtime_ns
                pickle.dump(stamp[1], f)  # size
                pickle.dump(index, f)
        except Exception:
            pass

    @staticmethod
    def clear(older_than: int = _CACHE_TTL):
        """手动清理缓存：清理过期缓存文件。"""
        if not _CACHE_DIR.exists():
            return
        now = time.time()
        for f in _CACHE_DIR.iterdir():
            if f.suffix == ".pkl" and now - f.stat().st_mtime > older_than:
                f.unlink(missing_ok=True)


# ── 解析入口 ────────────────────────────────────────────────

def _parse_file(file_path: str | Path):
    """解析文件，返回 (tree, language_name, index)。

    index 是预计算的函数/类定义列表，缓存命中时无需再查询。
    使用 (mtime, size) 校验文件是否变更，避免重复解析。
    """

    # 转化为标准绝对路径
    p = Path(file_path).expanduser().resolve()
    if not p.is_file():
        return None, None, []

    # 提取文件后缀，建立语言映射
    ext = p.suffix.lower()
    if ext not in _LANGUAGE_MAP:
        return None, None, []

    parser = _get_parser(ext)
    if parser is None:
        return None, None, []

    lang_name = _LANGUAGE_MAP[ext]

    # 尝试从缓存加载（mtime + size 匹配则命中）
    cached_index, cached_stamp = AstCache.get(p)
    if cached_index is not None:
        tree = parser.parse(p.read_bytes())
        return tree, lang_name, cached_index

    # 缓存未命中，全量解析
    try:
        source = p.read_bytes()
        tree = parser.parse(source)

        index = _query_definitions(tree, _QUERY_TEMPLATES.get(lang_name, ""), lang_name)
        stamp = _file_stamp(p)
        AstCache.put(p, stamp, index) # 建立缓存

        return tree, lang_name, index
    except Exception:
        return None, None, []


def _query_definitions(tree, query_source: str, lang_name: str):
    """执行 tree-sitter 查询，返回 [(name, type, start_row, end_row, text), ...]。"""
    from tree_sitter import Query, QueryCursor

    lang = tree.language
    if lang is None:
        return []

    try:
        q = Query(lang, query_source)
    except Exception:
        return []

    def_types = _DEF_NODE_TYPES.get(lang_name, (
        "function_definition", "class_definition", "method_definition"
    ))

    cursor = QueryCursor(q) # 方便查询的游标
    results: list[tuple[str, str, int, int, str]] = [] # 结果
    seen_names: set[str] = set() # 字段去重

    # 核心解析循环
    for pattern_idx, captures in cursor.matches(tree.root_node):
        name_nodes = captures.get("name", [])
        if not name_nodes:
            continue

        name_node = name_nodes[0]
        name = name_node.text.decode()

        if name in seen_names:
            continue
        seen_names.add(name)

        # 找到其父定义节点
        node = name_node
        while node.parent and node.parent.type not in def_types:
            node = node.parent
        def_node = node.parent if node.parent else node

        text_lines = def_node.text.decode().splitlines()

        # 首行签名
        signature = text_lines[0] if text_lines else ""
        results.append((
            name,
            def_node.type,
            def_node.start_point[0],
            def_node.end_point[0],
            signature,
        ))

    return results


# ── CodeQueryTool 语法树结构搜索 ────────────────────────────────────────────

class CodeQueryTool(Tool):
    """按语法结构搜索代码定义（函数、类等）。"""

    name = "code_query"
    description = (
        "按名称或类型查找代码中的函数、类定义。"
        "使用 tree-sitter 语法分析，比 grep 更精确。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要搜索的文件路径",
            },
            "query": {
                "type": "string",
                "description": "查询内容。支持：\n"
                               "- `type:function_def` 列出所有函数\n"
                               "- `type:class_def` 列出所有类\n"
                               "- `name:函数名` 查找特定函数定义\n"
                               "- `name:类名` 查找特定类定义",
            },
        },
        "required": ["file_path", "query"],
    }

    def execute(self, file_path: str, query: str) -> str:
        # 解析路径
        tree, lang_name, _ = _parse_file(file_path)
        if tree is None:
            return f"Error: could not parse {file_path}"

        # 获取节点名称
        func_type, class_type = _DEF_NODE_TYPES.get(lang_name, ("function_definition", "class_definition"))

        # 解析查询参数
        q = query.strip().lower()
        query_src: str | None = None
        # 类型搜索
        if q.startswith("type:"):
            target_type = q[5:].strip()
            type_map = {
                "function_def": func_type,
                "class_def": class_type,
                "method_def": func_type,
            }
            ts_type = type_map.get(target_type, target_type)
            query_src = f"({ts_type} name: (identifier) @name)"
            # 名称搜索
        elif q.startswith("name:"):
            query_src = f"[({func_type} name: (identifier) @name)({class_type} name: (identifier) @name)]"
        else:
            return (f"Error: unsupported query format '{query}'. "
                    f"Use 'type:function_def', 'type:class_def', 'name:<名称>'")

        results = _query_definitions(tree, query_src, lang_name)
        if not results:
            return f"No definitions found matching '{query}' in {file_path}"

        # 对name查询做特殊处理：发请求时已知name，需要返回详细信息
        if q.startswith("name:"):
            target_name = q[5:].strip()
            results = [r for r in results if target_name in r[0]]
            if not results:
                return f"'{target_name}' not found in {file_path}"

            name, dtype, srow, erow, sig = results[0]
            # 从 tree 获取源代码（tree.root_node.text 是整个文件的 bytes）
            source_lines = tree.root_node.text.decode(errors="replace").splitlines()
            chunk = source_lines[srow:erow + 1]
            numbered = "\n".join(f"{srow + i + 1}\t{line}" for i, line in enumerate(chunk))
            return f"Found {dtype} '{name}' ({file_path}:{srow + 1}-{erow + 1}):\n{numbered}"

        lines = [f"Definitions in {file_path} ({len(results)}):"]
        for name, dtype, srow, erow, sig in results:
            lines.append(f"  {srow + 1}:{erow + 1}  {sig[:80]}")
        return "\n".join(lines)


# ── StructReadTool 语法树结构读取 ──────────────────────────────────────────

class StructReadTool(Tool):
    """按语法结构读取代码块（函数体、类定义等）。"""

    name = "struct_read"
    description = (
        "按函数名或类名读取代码块。"
        "在已有 read_file 的基础上提供结构化读取，减少 token 消耗。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "文件路径",
            },
            "focus": {
                "type": "string",
                "description": "聚焦读取的目标，格式：\n"
                               "- `function:函数名` 读取指定函数体\n"
                               "- `class:类名` 读取指定类定义\n"
                               "- 不提供此参数时退化为普通 read_file",
            },
            "offset": {
                "type": "integer",
                "description": "起始行号（1-based），仅无 focus 时有效",
            },
            "limit": {
                "type": "integer",
                "description": "最大行数，仅无 focus 时有效",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, focus: str | None = None,
                offset: int = 1, limit: int = 2000) -> str:
        # 无 focus -> 退化到普通行号读取
        if not focus:
            p = Path(file_path).expanduser().resolve()
            return self._fallback_read(p, offset, limit)

        tree, lang_name, _ = _parse_file(file_path)
        if tree is None:
            return f"Error: could not parse {file_path}"

        # 解析focus参数
        focus = focus.strip()
        focus_lower = focus.lower()

        func_type, class_type = _DEF_NODE_TYPES.get(lang_name, ("function_definition", "class_definition"))

        # 查找function
        if focus_lower.startswith("function:") or focus_lower.startswith("func:"):
            target_type, target_name = func_type, focus.split(":", 1)[1].strip()
        # 查找class
        elif focus_lower.startswith("class:"):
            target_type, target_name = class_type, focus.split(":", 1)[1].strip()
        else:
            return f"Error: unsupported focus format '{focus}'. Use 'function:X' or 'class:X'"

        if not target_name:
            return f"Error: no name specified in '{focus}'"

        # 解析文件+执行查询
        results = _query_definitions(tree, f"({target_type} name: (identifier) @name)", lang_name)
        matches = [r for r in results if target_name in r[0]]
        if not matches:
            return f"'{target_name}' not found in {file_path}"

        # 格式化输出
        name, _, srow, erow, _ = matches[0]
        source_lines = tree.root_node.text.decode(errors="replace").splitlines()
        chunk = source_lines[srow:erow + 1]
        numbered = "\n".join(f"{srow + i + 1}\t{line}" for i, line in enumerate(chunk))
        return f"{name} ({file_path}:{srow + 1}-{erow + 1}, {len(chunk)} lines):\n{numbered}"

    @staticmethod
    def _fallback_read(p: Path, offset: int, limit: int) -> str:
        """降级：按行号读取（同 read_file）。"""
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        start = max(0, offset - 1)
        chunk = lines[start: start + limit]
        numbered = [f"{start + i + 1}\t{ln}" for i, ln in enumerate(chunk)]
        result = "\n".join(numbered)
        if total > start + limit:
            result += f"\n... ({total} lines total, showing {start + 1}-{start + len(chunk)})"
        return result or "(empty file)"
