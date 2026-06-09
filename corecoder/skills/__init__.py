"""Skills 加载器——带版本控制和路由摘要。

架构：
  扫描 skills/*.md → 计算内容 hash → 对比缓存 → 增量摘要
  → skills ≤ 10：全部注入系统提示词
  → skills > 10：生成路由摘要（每个 skill 一行概括），按需读完整内容

使用方式：
    from .skills import load_skills
    route_skills, full_skills = load_skills(llm=agent.llm)
    # route_skills → [("python-dev", "一行摘要"), ...]  注入系统提示词
    # full_skills  → {"python-dev": "完整内容"}          按需加载
"""

import hashlib
import json
import time
from pathlib import Path


_CACHE_FILE = ".skills_cache.json"
_ROUTE_FILE = "_route.md"
_ROUTE_THRESHOLD = 10


def _skill_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache_path: Path, cache: dict):
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _generate_summary(name: str, content: str, llm=None) -> str:
    """生成 skill 摘要。有 LLM 时调用模型，否则截取首行。"""
    if llm and hasattr(llm, "chat"):
        try:
            resp = llm.chat(
                messages=[{
                    "role": "user",
                    "content": f"请用一句话概括以下技能的核心指令（20字以内）：\n\n{content[:500]}",
                }],
            )
            if resp.content:
                return resp.content.strip().rstrip("。")
        except Exception:
            pass
    # 降级：截取首行非空文字
    for line in content.splitlines():
        line = line.strip().strip("#").strip()
        if line:
            return line[:50]
    return name


def _build_route(skills_data: list[tuple[str, str, str]]) -> str:
    """从 (name, summary, hash) 列表生成路由文件内容。"""
    lines = ["# Skills 路由索引\n", f"共 {len(skills_data)} 个技能，完整内容见对应 .md 文件\n"]
    for name, summary, _ in skills_data:
        lines.append(f"- **{name}**: {summary}")
    return "\n".join(lines)


def load_skills(skills_dir: str | Path | None = None, llm=None):
    """加载 skills 目录下的所有技能，返回 (route_skills, full_skills)。

    Args:
        skills_dir: skills 目录路径，默认本文件所在目录。
        llm: LLM 实例（用于摘要生成），为 None 时降级为首行截取。

    Returns:
        route_skills: [(name, summary_or_content), ...] 注入系统提示词。
            数量 ≤ ROUTE_THRESHOLD 时返回完整内容，否则返回摘要。
        full_skills: {name: content, ...} 所有技能完整内容，按需读取。
    """
    base = Path(skills_dir or Path(__file__).parent).expanduser().resolve()
    if not base.is_dir():
        return [], {}

    cache_path = base / _CACHE_FILE
    cache = _load_cache(cache_path)
    skills_meta = cache.get("skills", {})
    changed = False

    # 第 1 遍：扫描文件，检测变化
    md_files: list[tuple[str, str]] = []
    for f in sorted(base.glob("*.md")):
        if f.name.startswith("_"):
            continue  # 跳过 _route.md 等内部文件
        content = f.read_text(encoding="utf-8").strip()
        if not content:
            continue
        md_files.append((f.stem, content))

    # 第 2 遍：增量摘要
    skills_data: list[tuple[str, str, str]] = []  # (name, content, summary)
    for name, content in md_files:
        h = _skill_hash(content)
        cached = skills_meta.get(name, {})
        if cached.get("hash") == h:
            summary = cached.get("summary", "")
        else:
            summary = _generate_summary(name, content, llm)
            skills_meta[name] = {
                "hash": h, "summary": summary,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            changed = True
        skills_data.append((name, content, summary))

    # 清理缓存中已删除的 skill
    active_names = {name for name, _ in md_files}
    deleted = [k for k in skills_meta if k not in active_names]
    if deleted:
        for k in deleted:
            del skills_meta[k]
        changed = True

    # 写回缓存
    if changed:
        cache["version"] = 2
        cache["skills"] = skills_meta
        _save_cache(cache_path, cache)

    # 决定返回路由还是完整内容
    if len(skills_data) > _ROUTE_THRESHOLD:
        # 路由模式：只注入摘要到系统提示词
        route_items = [(name, summary) for name, _, summary in skills_data]
        # 同步 _route.md
        route_content = _build_route(skills_data)
        (base / _ROUTE_FILE).write_text(route_content, encoding="utf-8")
    else:
        # 常规模式：全部注入
        route_items = [(name, content) for name, content, _ in skills_data]

    full_skills = {name: content for name, content, _ in skills_data}
    return route_items, full_skills
