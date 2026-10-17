# -*- coding: utf-8 -*-
"""VoltLab 启动入口：python main.py

崩溃捕获（V1.7）：faulthandler 把致命错误（访问违例/qFatal abort 等）
发生时的 Python 调用栈写入 logs/crash.log —— 排障时先看该文件。
"""

import faulthandler
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_LOGS = _ROOT / "logs"
_LOGS.mkdir(exist_ok=True)
_crash_file = open(_LOGS / "crash.log", "a", encoding="utf-8")
_crash_file.write("\n==== session start ====\n")
_crash_file.flush()
faulthandler.enable(_crash_file)

from ui.main_window import main

if __name__ == "__main__":
    main()
