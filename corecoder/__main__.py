import sys
from pathlib import Path

# 把项目根目录加入路径，支持在 corecoder/ 内直接运行 python __main__.py
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from corecoder.cli import main

main()
