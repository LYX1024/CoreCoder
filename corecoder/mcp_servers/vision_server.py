"""多模态视觉分析 MCP 服务器。

通过 DashScope（阿里通义千问 VL API）分析图片内容。
支持 URL、本地文件路径、Base64 三种传入方式。
未配置 VISION_API_KEY 时服务器空启动（连接正常但无工具注册）。

环境变量（.env）：
    VISION_API_KEY     — DashScope API Key
    VISION_API_BASE    — API 地址
    VISION_MODEL       — 模型名（默认 qwen3-vl-plus）
"""

import base64
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("vision")

# 从 .env 加载配置
_dotenv_path = Path(__file__).parent.parent / ".env"
if _dotenv_path.exists():
    for line in _dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            val = val.strip().strip("\"'")
            os.environ.setdefault(key, val)


# 仅在配置了 API Key 时注册工具
if os.getenv("VISION_API_KEY"):

    def _get_client():
        from openai import OpenAI
        return OpenAI(
            api_key=os.getenv("VISION_API_KEY", ""),
            base_url=os.getenv("VISION_API_BASE",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )

    # 图片格式映射（Qwen3-VL-Plus 官方支持）
    # 来源：阿里云百炼 https://help.aliyun.com/zh/model-studio/vision-model/
    _FORMAT_MAP = {
        "png": "png", "jpg": "jpeg", "jpeg": "jpeg",
        "webp": "webp", "gif": "gif", "bmp": "bmp",
        "tiff": "tiff", "tif": "tiff", "ico": "x-icon",
        "icns": "icns", "dib": "dib", "sgi": "sgi",
    }

    @server.tool(description="分析图片内容。支持 URL、本地文件路径、Base64 三种方式传入")
    def analyze_image(
        image_url: str | None = None,
        image_path: str | None = None,
        image_base64: str | None = None,
        image_format: str | None = None,
        prompt: str = "请详细描述这张图片的内容",
    ) -> str:
        if not any([image_url, image_path, image_base64]):
            return "请提供 image_url、image_path（本地路径）或 image_base64"

        if image_url:
            img = {"type": "image_url", "image_url": {"url": image_url}}
        elif image_path:
            p = Path(image_path).expanduser().resolve()
            if not p.is_file():
                return f"文件不存在：{image_path}"
            ext = p.suffix.lstrip(".").lower()
            mime = _FORMAT_MAP.get(ext) or _FORMAT_MAP.get(image_format or "", "png")
            try:
                b64 = base64.b64encode(p.read_bytes()).decode()
                img = {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}}
            except Exception as e:
                return f"读取图片失败：{e}"
        else:
            mime = _FORMAT_MAP.get((image_format or "png").lower(), "png")
            img = {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{image_base64}"}}

        try:
            resp = _get_client().chat.completions.create(
                model=os.getenv("VISION_MODEL", "qwen3-vl-plus"),
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, img]}],
                max_tokens=2048,
            )
            return resp.choices[0].message.content or "(空回复)"
        except Exception as e:
            return f"分析失败：{e}"

if __name__ == "__main__":
    server.run()
