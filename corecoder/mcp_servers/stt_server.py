"""语音转文字 MCP 服务器。

通过 SpeechRecognition（Google Web Speech API）将麦克风输入转为文字。
免费、无需 API Key、需联网。

暂停检测逻辑：说话停顿超过 silence_timeout 秒即视为一句话结束。
"""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("stt")

# 从 .env 加载（若需要配置 STT 后端时可扩展）
_dotenv_path = Path(__file__).parent.parent / ".env"
if _dotenv_path.exists():
    for line in _dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            val = val.strip().strip("\"'")
            os.environ.setdefault(key, val)


@server.tool(description="通过麦克风录制语音并转写为文字。支持智能暂停检测，说话停顿超过 2 秒自动结束。")
def transcribe(
    language: str = "zh-CN",
    silence_timeout: float = 2.0,
    phrase_limit: int = 30,
) -> str:
    """录制并转写语音。

    Args:
        language: 识别语言，默认 zh-CN（中文）。英文可选 en-US。
        silence_timeout: 停顿多少秒视为说话结束，默认 2.0
        phrase_limit: 单次最长录音秒数，默认 30
    """
    try:
        import speech_recognition as sr
    except ImportError:
        return "SpeechRecognition 未安装，请执行：pip install SpeechRecognition"

    recognizer = sr.Recognizer()

    # 设置能量阈值和暂停检测
    recognizer.energy_threshold = 300
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = silence_timeout

    try:
        with sr.Microphone() as source:
            # 自适应环境噪音
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=phrase_limit)
    except sr.WaitTimeoutError:
        return "未检测到语音输入（超时）"
    except OSError as e:
        return f"麦克风访问失败：{e}"

    try:
        text = recognizer.recognize_google(audio, language=language)
        return text.strip()
    except sr.UnknownValueError:
        return "未能识别语音内容"
    except sr.RequestError as e:
        return f"语音识别服务请求失败：{e}"
    except Exception as e:
        return f"识别出错：{e}"


if __name__ == "__main__":
    server.run()
