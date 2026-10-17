# -*- coding: utf-8 -*-
"""软件日志：文件 + 可选控制台。

- 日志目录默认 `<项目根>/logs`，按天滚动文件 `voltlab_YYYYMMDD.log`；
- 记录内容：每次求解（模式/元件数/耗时/结果）、短路、看门狗 200ms 预警
  与 800ms 强杀、路端电压违规等；
- 遵守用户规则：日志文件不写 C 盘（项目在 F 盘）。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = _ROOT / "logs"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_configured = False


def setup_logging(log_dir: str | None = None, console: bool = False,
                  level: int = logging.INFO) -> Path:
    """初始化 voltlab 日志。重复调用安全（仅配置一次，除非目录变化）。

    返回日志目录路径。
    """
    global _configured
    d = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    os.makedirs(d, exist_ok=True)
    logger = logging.getLogger("voltlab")
    if _configured and logger.handlers:
        return d
    logger.setLevel(level)
    fh = logging.FileHandler(
        d / ("voltlab_%s.log" % datetime.now().strftime("%Y%m%d")),
        encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(fh)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(sh)
    _configured = True
    logging.getLogger("voltlab").info("logging initialized, dir=%s", d)
    return d


def log_file_path(log_dir: str | None = None) -> Path:
    d = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    return d / ("voltlab_%s.log" % datetime.now().strftime("%Y%m%d"))


def reset_logging():
    """关闭并移除全部 voltlab handler（测试隔离用），允许重新 setup。"""
    global _configured
    logger = logging.getLogger("voltlab")
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)
    _configured = False
