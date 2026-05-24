"""交互式 REPL - 面向用户的终端界面。"""

import sys
import os
import argparse
import atexit

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

from .agent import Agent
from .llm import LLM, LiteLLM
from .config import Config
from .session import save_session, load_session, list_sessions
from .tools import ALL_TOOLS
from .multi_agents import SPECIALIZED_AGENTS
from .skills import load_skills
from . import __version__

console = Console()


def _parse_args():
    p = argparse.ArgumentParser(
        prog="corecoder",
        description="Minimal AI coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument("-m", "--model", help="Model name (default: $CORECODER_MODEL or gpt-4o)")
    p.add_argument("--base-url", help="API base URL (default: $OPENAI_BASE_URL)")
    p.add_argument("--api-key", help="API key (default: $OPENAI_API_KEY)")
    p.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    p.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args()


def main():
    args = _parse_args()
    config = Config.from_env()

    # CLI 参数覆盖环境变量
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key

    if not config.api_key:
        console.print("[red bold]No API key found.[/]")
        console.print(
            "Set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY, or CORECODER_API_KEY\n"
            "\nExamples:\n"
            "  # OpenAI\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "\n"
            "  # DeepSeek\n"
            "  export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com\n"
            "\n"
            "  # Ollama (local)\n"
            "  export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 CORECODER_MODEL=qwen2.5-coder\n"
        )
        sys.exit(1)

    llm_cls = LiteLLM if config.provider == "litellm" else LLM
    llm = llm_cls(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
    # 此处给主agent的工具列表有 工具列表+已定义的子agent
    skill_instructions = load_skills()

    # MCP 工具初始化
    mcp_tools: list = []
    mcp_manager = None
    if config.mcp_enabled and config.mcp_servers:
        try:
            from .tools.mcp import MCPManager

            mcp_manager = MCPManager(config.mcp_servers)
            mcp_tools = mcp_manager.initialize_all()
            errors = mcp_manager.get_errors()
            for server_name, err in errors:
                console.print(f"[yellow]MCP [{server_name}]: {err}[/yellow]")
            if mcp_tools:
                tool_names = [t.name for t in mcp_tools]
                console.print(f"[dim]MCP tools loaded: {', '.join(tool_names)}[/dim]")
        except Exception as e:
            console.print(f"[yellow]MCP initialization failed: {e}[/yellow]")

    agent = Agent(
        llm=llm,
        # 工具列表 + 子分工agent + mcp工具列表
        tools=ALL_TOOLS + SPECIALIZED_AGENTS + mcp_tools,
        max_context_tokens=config.max_context_tokens,
        skill_instructions=skill_instructions,
    )

    # 注册退出清理：关闭 MCP 连接
    if mcp_manager is not None:

        def _cleanup_mcp(mgr=mcp_manager):
            mgr.close_all()

        atexit.register(_cleanup_mcp)

    # 恢复已保存的会话
    if args.resume:
        loaded = load_session(args.resume)
        if loaded:
            agent.messages, loaded_model = loaded
            # 除非被 CLI 覆盖，否则从保存的会话恢复模型
            if not args.model:
                agent.llm.model = loaded_model
                config.model = loaded_model
            console.print(f"[green]Resumed session: {args.resume} (model: {agent.llm.model})[/green]")
        else:
            console.print(f"[red]Session '{args.resume}' not found.[/red]")
            sys.exit(1)

    # 单次执行模式
    if args.prompt:
        _run_once(agent, args.prompt)
        return

    # 交互式 REPL
    _repl(agent, config)


def _run_once(agent: Agent, prompt: str):
    """非交互模式：运行一次提示后退出。"""
    def on_token(tok):
        print(tok, end="", flush=True)

    def on_tool(name, kwargs):
        console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

    agent.chat(prompt, on_token=on_token, on_tool=on_tool)
    print()


def _repl(agent: Agent, config: Config):
    """交互式读取-求值-打印循环。"""

    # 打印启动面板
    console.print(Panel(
        f"[bold]CoreCoder[/bold] v{__version__}\n"
        f"Model: [cyan]{config.model}[/cyan]"
        + (f"  Base: [dim]{config.base_url}[/dim]" if config.base_url else "")
        + "\nType [bold]/help[/bold] for commands, [bold]Ctrl+C[/bold] to cancel, [bold]quit[/bold] to exit.",
        border_style="blue",
    ))

    hist_path = os.path.expanduser("~/.corecoder_history")
    history = FileHistory(hist_path)

    # Enter 提交，Escape+Enter 插入换行（用于粘贴代码块等）
    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    while True:
        try:
            # 接收输入信息
            user_input = pt_prompt(
                "You > ",
                history=history,
                multiline=True,
                key_bindings=kb,
                prompt_continuation="...  ",
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break

        if not user_input:
            continue

        """
        内置命令
        /help	显示帮助
        /reset	清空对话历史
        /model	查看当前模型
        /model gpt-4	运行时切换模型
        /tokens	查看 Token 用量
        /compact	压缩上下文
        /save	保存当前会话
        /diff	显示修改过的文件
        /sessions	列出已保存会话
        /stt	语音输入（麦克风→文字）
        """
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            break
        if user_input == "/help":
            _show_help()
            continue
        if user_input == "/reset":
            agent.reset()
            console.print("[yellow]Conversation reset.[/yellow]")
            continue
        if user_input == "/tokens":
            p = agent.llm.total_prompt_tokens
            c = agent.llm.total_completion_tokens
            line = f"Tokens: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p+c}[/bold] total"
            cost = agent.llm.estimated_cost
            if cost is not None:
                line += f"  (~${cost:.4f})"
            console.print(line)
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            new_model = user_input[7:].strip() if user_input.startswith("/model ") else ""
            if new_model:
                agent.llm.model = new_model
                config.model = new_model
                console.print(f"Switched to [cyan]{new_model}[/cyan]")
            else:
                console.print(f"Current model: [cyan]{config.model}[/cyan]")
            continue
        if user_input == "/compact":
            from .context import estimate_tokens
            before = estimate_tokens(agent.messages)
            compressed = agent.context.maybe_compress(agent.messages, agent.llm)
            after = estimate_tokens(agent.messages)
            if compressed:
                console.print(f"[green]Compressed: {before} → {after} tokens ({len(agent.messages)} messages)[/green]")
            else:
                console.print(f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]")
            continue
        if user_input == "/save":
            sid = save_session(agent.messages, config.model)
            console.print(f"[green]Session saved: {sid}[/green]")
            console.print(f"Resume with: corecoder -r {sid}")
            continue
        if user_input == "/diff":
            from .tools.edit import _changed_files
            if not _changed_files:
                console.print("[dim]No files modified this session.[/dim]")
            else:
                console.print(f"[bold]Files modified this session ({len(_changed_files)}):[/bold]")
                for f in sorted(_changed_files):
                    console.print(f"  [cyan]{f}[/cyan]")
            continue
        if user_input == "/sessions":
            sessions = list_sessions()
            if not sessions:
                console.print("[dim]No saved sessions.[/dim]")
            else:
                for s in sessions:
                    console.print(f"  [cyan]{s['id']}[/cyan] ({s['model']}, {s['saved_at']}) {s['preview']}")
            continue
        if user_input == "/stt":
            console.print("[cyan] 正在录音（停顿 2 秒自动结束）...[/cyan]")
            console.print("[dim]按 Ctrl+C 取消[/dim]")
            try:
                import speech_recognition as sr
                r = sr.Recognizer()
                r.energy_threshold = 300
                r.dynamic_energy_threshold = True
                r.pause_threshold = 2.0
                with sr.Microphone() as source:
                    r.adjust_for_ambient_noise(source, duration=0.5)
                    audio = r.listen(source, timeout=10, phrase_time_limit=60)
                console.print("[cyan] 识别中...[/cyan]")

                # 优先 Google Web Speech，降级 Vosk
                text = r.recognize_google(audio, language="zh-CN")
                if not text:
                    text = _stt_vosk(audio)
                text = text.strip()

                if text:
                    console.print(f"[green] {text}[/green]")
                    user_input = text
                else:
                    console.print("[yellow]未能识别语音内容[/yellow]")
                    continue
            except sr.WaitTimeoutError:
                console.print("[yellow]未检测到语音输入[/yellow]")
                continue
            except ImportError:
                console.print("[yellow]SpeechRecognition 未安装[/yellow]")
                continue
            except Exception as e:
                console.print(f"[yellow]语音识别失败：{e}[/yellow]")
                continue

        # 调用代理
        streamed: list[str] = []

        def on_token(tok):
            streamed.append(tok)
            print(tok, end="", flush=True)

        def on_tool(name, kwargs):
            console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

        try:
            response = agent.chat(user_input, on_token=on_token, on_tool=on_tool)
            if streamed:
                print()  # 获取流式回复后新建一行
            else:
                # 未得到流式回复 (在工具调用后才获取)
                console.print(Markdown(response))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")

# ── Vosk 离线语音识别 ──────────────────────────────────────
def _stt_vosk(audio_data) -> str | None:
    """用 Vosk 离线识别语音（无需联网）。"""
    import os, json
    from pathlib import Path

    model_rel = os.getenv("STT_MODEL_PATH", "")
    if not model_rel:
        return None
    model_path = (Path(__file__).parent / model_rel).resolve()
    if not model_path.is_dir():
        return None

    try:
        from vosk import Model, KaldiRecognizer
        model = Model(str(model_path))
        rec = KaldiRecognizer(model, 16000)
        rec.AcceptWaveform(bytes(audio_data.get_raw_data(convert_rate=16000, convert_width=2)))
        result = json.loads(rec.FinalResult())
        return result.get("text", "").strip() or None
    except Exception:
        return None


# 获取/命令列表
def _show_help():
    console.print(Panel(
        "[bold]Commands:[/bold]\n"
        "  /help          Show this help\n"
        "  /reset         Clear conversation history\n"
        "  /model         Show current model\n"
        "  /model <name>  Switch model mid-conversation\n"
        "  /tokens        Show token usage\n"
        "  /compact       Compress conversation context\n"
        "  /diff          Show files modified this session\n"
        "  /save          Save session to disk\n"
        "  /sessions      List saved sessions\n"
        "  /stt           Voice input (microphone → text)\n"
        "  quit           Exit CoreCoder\n"
        "\n"
        "[bold]Input:[/bold]\n"
        "  Enter          Submit message\n"
        "  Esc+Enter      Insert newline (for pasting code)",
        title="CoreCoder Help",
        border_style="dim",
    ))

# 工具参数格式化
# {"pattern": "*.py", "path": "./src"}  ->  pattern='*.py', path='./src'
def _brief(kwargs: dict, maxlen: int = 300) -> str:
    s = ", ".join(f"{k}={repr(v)[:200]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")
