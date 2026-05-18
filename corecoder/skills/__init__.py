"""Skills 加载器。

扫描 skills/ 目录下的 .md 文件作为 prompt skills，
文件名成为 skill 名称，文件内容作为指令文本注入到系统提示词。

使用方式：
    from .skills import load_skills
    skills = load_skills()  # → [("python-dev", "## Python..."), ...]
"""

from pathlib import Path


def load_skills(skills_dir: str | Path | None = None) -> list[tuple[str, str]]:
    """扫描 skills 目录，加载所有 .md 文件作为 skill。

    Args:
        skills_dir: skills 目录路径。默认使用本文件所在目录。

    Returns:
        [(name, content), ...] 按文件名排序。
    """
    if skills_dir is None:
        skills_dir = Path(__file__).parent

    base = Path(skills_dir).expanduser().resolve()
    if not base.is_dir():
        return []

    skills: list[tuple[str, str]] = []
    for md_file in sorted(base.glob("*.md")):
        name = md_file.stem  # 文件名不含后缀
        content = md_file.read_text(encoding="utf-8").strip()
        if content:
            skills.append((name, content))

    return skills
