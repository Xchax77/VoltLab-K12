# -*- coding: utf-8 -*-
"""P3：看门狗 —— 求解放入 QThread，QTimer 限时。

需求书 第五章：
- 求解在 QThread 中执行，主界面不冻结；
- QTimer.singleShot：稳态超过 200ms 发出预警信号，超过 800ms 强制终止
  并返回错误，UI 红字提示"求解超时，请检查环路"（P4 接入）。

实现说明：
- `run_with_watchdog(elements, mode, warn_ms, kill_ms)` 是给 UI/测试用的
  同步入口：内部起 QThread + QEventLoop，返回求解结果 dict；
- **计时起点是 worker 真正开始求解**（working 信号），Qt 首次初始化的
  开销不计入看门狗窗口，避免第一次求解被误杀；
- 强制终止语义：numpy 求解本身不可中断，kill 时置 aborted 标志并放弃
  该线程的结果（线程自然结束后不向已超时的会话投递），对外表现为
  `{'ok': False, 'error': 'timeout', 'message': '求解超时，请检查环路'}`；
- 所有事件写 voltlab 日志。
"""

from __future__ import annotations

import atexit
import logging
import time

from PyQt5.QtCore import (  # noqa: E402
    QEventLoop,
    QObject,
    QThread,
    QTimer,
    pyqtSignal,
)

from core.solver import solve_circuit  # noqa: E402

log = logging.getLogger("voltlab.watchdog")

WARN_MS_DEFAULT = 200    # 需求书：稳态超过 200ms 提示
KILL_MS_DEFAULT = 800    # 需求书：超过 800ms 强制终止

# 超时后放弃的 worker 仍需自然跑完：持引用防止 GC 崩溃，退出时统一回收
_active_workers: list = []


def _cleanup_workers():
    for w in list(_active_workers):
        w.wait(3000)
    _active_workers.clear()


atexit.register(_cleanup_workers)


class SolveWorker(QThread):
    """在独立线程中执行 solve_circuit。"""

    done = pyqtSignal(object)
    working = pyqtSignal()  # 线程真正开始求解时发出（看门狗计时起点）

    def __init__(self, elements: list, mode: str = "junior",
                 parent: QObject | None = None):
        super().__init__(parent)
        self._elements = elements
        self._mode = mode
        self.aborted = False

    def run(self):  # pragma: no cover - 线程入口
        self.working.emit()
        t0 = time.perf_counter()
        result = solve_circuit(self._elements, self._mode)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        log.info("worker finished in %.2fms ok=%s", dt_ms, result.get("ok"))
        if not self.aborted:
            self.done.emit(result)
        else:
            log.info("worker result discarded (session timed out)")


def run_with_watchdog(elements: list, mode: str = "junior",
                      warn_ms: int = WARN_MS_DEFAULT,
                      kill_ms: int = KILL_MS_DEFAULT) -> dict:
    """带看门狗的同步求解入口。

    - 正常：返回 solve_circuit 结果，附 'watchdog' 统计
      {'warned': bool, 'killed': False, 'elapsed_ms': float}（求解净耗时）；
    - 超时：{'ok': False, 'error': 'timeout',
             'message': '求解超时，请检查环路', 'watchdog': {...}}。
    """
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    worker = SolveWorker(elements, mode)
    _active_workers.append(worker)
    worker.finished.connect(  # 线程结束后从活跃表移除
        lambda w=worker: _active_workers.remove(w) if w in _active_workers
        else None)

    state = {"result": None, "warned": False, "killed": False,
             "t0": None}
    loop = QEventLoop()
    _timers: list = []

    def _stop_timers():
        """正常结束时停掉看门狗定时器（避免事后误报 warn/killed）。"""
        for t in _timers:
            t.stop()
        _timers.clear()

    def on_done(res):
        _stop_timers()
        state["result"] = res
        loop.quit()

    def on_working():
        # 看门狗窗口从"真正开始求解"起算
        state["t0"] = time.perf_counter()
        for ms, fn in ((warn_ms, on_warn), (kill_ms, on_kill)):
            t = QTimer()
            t.setSingleShot(True)
            t.timeout.connect(fn)
            _timers.append(t)
            t.start(ms)

    def on_warn():
        state["warned"] = True
        log.warning("watchdog: solve exceeded %dms (warn)", warn_ms)

    def on_kill():
        state["killed"] = True
        worker.aborted = True
        log.error("watchdog: solve exceeded %dms, killed", kill_ms)
        loop.quit()

    worker.working.connect(on_working)
    worker.done.connect(on_done)
    worker.start()
    loop.exec()

    if state["t0"] is None:
        elapsed_ms = 0.0  # 线程未及启动即被外部回收（异常防御）
    else:
        elapsed_ms = (time.perf_counter() - state["t0"]) * 1000.0

    if state["result"] is None:
        return {
            "ok": False, "error": "timeout",
            "message": "求解超时，请检查环路",
            "watchdog": {"warned": state["warned"], "killed": True,
                         "elapsed_ms": elapsed_ms},
        }
    result = dict(state["result"])
    result["watchdog"] = {"warned": state["warned"], "killed": False,
                          "elapsed_ms": elapsed_ms}
    worker.wait(2000)  # 正常路径回收线程
    return result
