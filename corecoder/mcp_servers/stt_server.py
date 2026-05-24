"""语音转文字 MCP 服务器。

优先使用 Google Web Speech API（免费、在线、无需 Key），
网络不可用时降级到 Vosk 离线识别（无需联网）。

模型路径从 .env 的 STT_MODEL_PATH 读取，相对于 corecoder/ 目录。
"""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("stt")

# 从 .env 加载配置
_dotenv_path = Path(__file__).parent.parent / ".env"
if _dotenv_path.exists():
    for line in _dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            val = val.strip().strip("\"'")
            os.environ.setdefault(key, val)


def _get_model_path() -> Path | None:
    """获取 Vosk 模型路径，未配置或不存在时返回 None。"""
    rel = os.getenv("STT_MODEL_PATH", "")
    if not rel:
        return None
    p = (Path(__file__).parent.parent / rel).resolve()
    return p if p.is_dir() else None


def _transcribe_vosk(audio_data) -> str | None:
    """用 Vosk 离线识别（无需联网，无需 API Key）。"""
    model_path = _get_model_path()
    if model_path is None:
        return None

    try:
        from vosk import Model, KaldiRecognizer
    except ImportError:
        return None

    try:
        model = Model(str(model_path))
        recognizer = KaldiRecognizer(model, 16000)

        # speech_recognition 的 AudioData → 16kHz PCM 原始数据
        raw = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
        recognizer.AcceptWaveform(bytes(raw))

        result = json.loads(recognizer.FinalResult())
        text = result.get("text", "").strip()
        return text if text else None
    except Exception:
        return None


def _transcribe_google(audio_data) -> str | None:
    """用 Google Web Speech API 识别"""
    try:
        import speech_recognition as sr
        text = sr.Recognizer().recognize_google(audio_data, language="zh-CN")
        return text.strip()
    except Exception:
        return None


@server.tool(description="通过麦克风录制语音并转写为文字。支持智能暂停检测，说话停顿超过 2 秒自动结束。")
def transcribe(
    silence_timeout: float = 2.0,
    phrase_limit: int = 30,
) -> str:
    """录制并转写语音。

    Args:
        silence_timeout: 停顿多少秒视为说话结束，默认 2.0
        phrase_limit: 单次最长录音秒数，默认 30
    """
    try:
        import speech_recognition as sr
    except ImportError:
        return "SpeechRecognition 未安装，请执行：pip install SpeechRecognition"

    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = silence_timeout

    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=phrase_limit)
    except sr.WaitTimeoutError:
        return "未检测到语音输入（超时）"
    except OSError as e:
        return f"麦克风访问失败：{e}"

    # 优先 Google Web Speech（免费、在线、无需 Key）
    text = _transcribe_google(audio)
    if text:
        return text

    # 降级 Vosk 离线识别
    text = _transcribe_vosk(audio)
    if text:
        return text

    # 都失败
    model_path = _get_model_path()
    if model_path:
        return "语音识别失败：Vosk 离线识别与 Google 在线识别均未返回结果。" + (
            f"\n请检查模型路径：{model_path}" if not model_path.exists() else ""
        )
    return ("语音识别失败：未配置 Vosk 模型路径且 Google 在线识别不可用（大陆网络受限）。"
            "\n在 .env 中设置 STT_MODEL_PATH 指向 Vosk 中文模型即可离线使用。")


if __name__ == "__main__":
    server.run()
