"""会话持久化 - 保存和恢复对话。

Claude Code 通过 QueryEngine（1295 行）维护会话状态。
CoreCoder 将其简化为：消息的 JSON 转储 + 模型配置。

sessionID由用户输入，故采用三层校验防止路径攻击
"""

import json
import re
import time
from pathlib import Path

SESSIONS_DIR = Path.home() / ".corecoder" / "sessions"

# 校验1：session命名白名单：大小写英文字母、数字与三种符号._-
_SAFE_SESSION_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _normalize_session_id(session_id: str | None) -> str:
    """ 正规化对话id """
    if not session_id:
        return f"session_{int(time.time())}"

    # 校验2：名称只取最后，抛弃前边的路径信息
    name = session_id.strip().replace("\\", "/").split("/")[-1]
    # 替换非法字符，可能出现的路径符号替换为-
    name = _SAFE_SESSION_RE.sub("-", name).strip(".-_")
    return name or f"session_{int(time.time())}"

# 校验3：path 的父目录必须是 sessions
def _session_path(session_id: str) -> Path:
    path = (SESSIONS_DIR / f"{_normalize_session_id(session_id)}.json").resolve()
    root = SESSIONS_DIR.resolve()
    if root != path.parent:
        raise ValueError("Invalid session id")
    return path


def save_session(messages: list[dict], model: str, session_id: str | None = None) -> str:
    """保存对话到磁盘。返回会话 ID。"""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    session_id = _normalize_session_id(session_id)

    data = {
        "id": session_id,
        "model": model,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "messages": messages,
    }

    path = _session_path(session_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return session_id


def load_session(session_id: str) -> tuple[list[dict], str] | None:
    """加载已保存的会话。返回 (messages, model) 或 None。"""
    path = _session_path(session_id)
    if not path.exists():
        return None

    data = json.loads(path.read_text())
    return data["messages"], data["model"]


def list_sessions() -> list[dict]:
    """列出可用会话，最新的在前。"""
    if not SESSIONS_DIR.exists():
        return []

    sessions = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            # 提取第一条用户消息作为预览
            preview = ""
            for m in data.get("messages", []):
                if m.get("role") == "user" and m.get("content"):
                    preview = m["content"][:80]
                    break
            sessions.append({
                "id": data.get("id", f.stem),
                "model": data.get("model", "?"),
                "saved_at": data.get("saved_at", "?"),
                "preview": preview,
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return sessions[:20]  # 上限 20 条
