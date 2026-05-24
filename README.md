# CoreCoder - 二次开发版

原项目 [he-yufeng/CoreCoder](https://github.com/he-yufeng/CoreCoder)

## 新增功能

- 多 Agent 编排（plan → execute → review 协作流程）
- Skills 支持（`skills/*.md` 自动注入系统提示词）
- Tree-sitter 语法树工具（`code_query` / `struct_read`，精确定位函数/类定义）
- MCP 工具集成（weather 天气查询、vision 多模态视觉、stt 语音输入）
- Windows 危险命令保护

## 可选依赖

按需安装，不影响基础功能：

```bash
# MCP 工具支持（天气、视觉、语音等）
pip install mcp>=1.0.0

# Tree-sitter 语法树分析（Python + Java）
pip install tree-sitter tree-sitter-python tree-sitter-java

# 多模态视觉识别（Vision MCP 服务器）
# 需要 DashScope API Key，在 .env 中配置 VISION_API_KEY

# 语音输入（/stt 命令）
# 主服务：Google Web Speech API（免费、在线、无需 Key）
pip install SpeechRecognition pyaudio
# 降级服务：Vosk 离线识别（无需联网），额外安装：
pip install vosk
# 并下载中文模型：https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip
# 解压至 corecoder/resources/vosk-model-small-cn-0.22/
# .env 中配置：STT_MODEL_PATH="resources/vosk-model-small-cn-0.22"

# LiteLLM 后端的额外提供者支持
pip install corecoder[litellm]
```

## 环境变量配置（.env）

```ini
# 主 Agent LLM
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.deepseek.com
CORECODER_MODEL=deepseek-v4-flash

# 多模态视觉（可选）
VISION_API_KEY=sk-xxx
VISION_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
VISION_MODEL=qwen3-vl-plus
```

## TODO

* 多模态
* 更复杂的记忆管理
* 更多
