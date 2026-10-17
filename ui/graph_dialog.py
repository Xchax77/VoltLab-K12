# -*- coding: utf-8 -*-
"""图像分析窗口：滑动变阻器滑片位置扫描曲线（V1.8 映射表方案）。

- 打开窗口（或点「重新扫描」）时**自动建立映射表**：
  电路当前状态 → 滑片位置 0→100% **1% 步进（101 点）**逐点求解，
  记录每个元件的 U / I / P —— 求解在副本上进行，不影响画布；
- 绘图直接查映射表，勾选纵轴量即时出图（不再逐点重解）；
- 横轴 = 滑片位置（%）——这是扫描自变量；纵轴可多选任意元件的 U/I/P；
- 绘制：QPainter 自绘坐标轴 + 多曲线（每条独立归一化，图例标注
  该曲线在整个扫描中的最小~最大值），无第三方绘图库依赖。

缺陷修复记录：旧实现把 get_elements() 的深拷贝当画布元件改 position，
改在副本上 → 41 个点全部用同一位置求解 → 曲线恒为平线（"没有图像"）。
"""

from __future__ import annotations

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.solver import solve_circuit
from ui.circuit_scene import TYPE_NAME_CN

CURVE_COLORS = ["#1e88e5", "#e53935", "#43a047", "#fb8c00", "#8e24aa",
                "#00897b", "#d81b60", "#5e35b1"]


class PlotWidget(QWidget):
    """自绘曲线区。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(560, 420)
        self.series = []   # [(label, [values...], color)]
        self.x_label = ""

    def set_data(self, series, x_label):
        self.series = series
        self.x_label = x_label
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#F1F8F2"))
        m = 60
        w, h = self.width() - 2 * m, self.height() - 2 * m
        if w <= 0 or h <= 0:
            return
        # 坐标轴
        p.setPen(QPen(QColor("#2d2d2d"), 1.5))
        p.drawLine(m, m, m, m + h)
        p.drawLine(m, m + h, m + w, m + h)
        if not self.series:
            p.setPen(QPen(QColor("#777777")))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "勾选右侧纵轴量后自动绘制")
            return
        n = max(len(v) for _, v, _ in self.series)
        # 网格（4 分线）
        p.setPen(QPen(QColor("#C5E3CB"), 1))
        for k in range(1, 4):
            y = m + h * k / 4
            p.drawLine(m, int(y), m + w, int(y))
        # 曲线（每条独立归一化）
        for idx, (label, vals, color) in enumerate(self.series):
            if not vals:
                continue
            lo, hi = min(vals), max(vals)
            rng = (hi - lo) or 1.0
            p.setPen(QPen(QColor(color), 2))
            prev = None
            for i, v in enumerate(vals):
                x = m + w * (i / max(1, n - 1))
                y = m + h - h * ((v - lo) / rng)
                if prev is not None:
                    p.drawLine(int(prev[0]), int(prev[1]),
                               int(x), int(y))
                prev = (x, y)
            p.setPen(QPen(QColor(color)))
            p.drawText(m + w + 6, m + 18 + idx * 18,
                       "%s [%.3g ~ %.3g]" % (label, lo, hi))
        # 轴标签
        p.setPen(QPen(QColor("#2d2d2d")))
        p.drawText(self.rect().adjusted(0, self.height() - 24, 0, 0),
                   Qt.AlignHCenter, self.x_label)
        p.save()
        p.translate(14, self.height() / 2)
        p.rotate(-90)
        p.drawText(QRect(0, 0, 160, 20), Qt.AlignCenter, "各纵轴量（独立归一化）")
        p.restore()


class GraphDialog(QDialog):
    """滑动变阻器扫描图像窗口（映射表方案）。"""

    STEP_PCT = 1   # 用户裁定：1% 步进

    def __init__(self, parent, scene, mode):
        super().__init__(parent)
        self.setWindowTitle("图像分析 — 滑动变阻器扫描")
        self.resize(900, 520)
        self.scene = scene
        self.mode = mode

        elements = scene.get_elements()
        rheo = [e for e in elements if e["type"] == "rheostat"]
        if not rheo:
            self._no_rheostat = True
            QVBoxLayout(self).addWidget(
                QLabel("画布中没有滑动变阻器。请先放置一个变阻器再打开本窗口。"))
            return
        self._no_rheostat = False
        self.rheo_id = rheo[0]["id"]

        # -- 映射表：{eid: {"I": [101 值], "U": [...], "P": [...]}} --
        self._table = {}

        root = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("横轴：滑片位置 0→100%（1% 步进）　"
                             "纵轴（可多选，查表即绘）："))
        row.addStretch(1)
        btn = QPushButton("重新扫描")
        btn.clicked.connect(self.rescan)
        row.addWidget(btn)
        root.addLayout(row)

        body = QHBoxLayout()
        self.plot = PlotWidget()
        body.addWidget(self.plot, 1)

        panel = QWidget()
        pv = QVBoxLayout(panel)
        self._checks = []
        for el in elements:
            if el["type"] == "wire":
                continue
            nm = "%s(%s)" % (el["id"], TYPE_NAME_CN[el["type"]])
            for key, unit in (("I", "电流I"), ("U", "电压U"), ("P", "功率P")):
                cb = QCheckBox("%s %s" % (nm, unit))
                cb.setProperty("eid", el["id"])
                cb.setProperty("key", key)
                cb.setProperty("unit", unit)
                cb.stateChanged.connect(self.redraw)
                pv.addWidget(cb)
                self._checks.append(cb)
        pv.addStretch(1)
        body.addWidget(panel)
        root.addLayout(body)

        # 默认勾选第一个灯泡/马达/电铃的电流
        for cb in self._checks:
            if cb.property("key") == "I" and \
                    cb.property("eid").startswith(("lamp", "bell", "m")):
                cb.setChecked(True)
                break
        self.rescan()

    # -- 映射表 --
    def rescan(self):
        """电路当前状态 → 1% 步进扫描 101 点，建立 U/I/P 映射表。"""
        if self._no_rheostat:
            return
        n = 100 // self.STEP_PCT + 1
        table = {}
        for i in range(n):
            pos = min(1.0, i * self.STEP_PCT / 100.0)
            elements = self.scene.get_elements()   # 副本上扫描，不动画布
            for el in elements:
                if el["id"] == self.rheo_id:
                    el["props"]["position"] = pos
            res = solve_circuit(elements, mode=self.mode)
            if not res.get("ok"):
                continue
            for eid, er in res["element_results"].items():
                col = table.setdefault(eid, {"I": [], "U": [], "P": []})
                col["I"].append(abs(er.get("current") or 0.0))
                col["U"].append(abs(er.get("voltage") or 0.0))
                col["P"].append(abs(er.get("power") or 0.0))
        self._table = table
        self.redraw()

    def redraw(self):
        """按映射表查表绘图（勾选变化时即时刷新，不重解）。"""
        if self._no_rheostat:
            return
        if not self._table:
            self.plot.set_data([], "滑动变阻器滑片位置 (%)")
            return
        series = []
        idx = 0
        for cb in self._checks:
            if not cb.isChecked():
                continue
            eid, key = cb.property("eid"), cb.property("key")
            vals = self._table.get(eid, {}).get(key)
            if not vals:
                continue
            label = "%s %s(%s)" % (eid, key, cb.property("unit"))
            series.append((label, vals,
                           CURVE_COLORS[idx % len(CURVE_COLORS)]))
            idx += 1
        self.plot.set_data(series, "滑动变阻器滑片位置 (%)")
