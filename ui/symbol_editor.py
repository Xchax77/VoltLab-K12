# -*- coding: utf-8 -*-
"""元件图像编辑器 —— 类 Word 几何画布。

- 工具：选择 / 线条 / 圆 / 方（按下拖动松开创建，拖动中实时预览）；
- 画布：原点为元件中心，浅色网格；蓝圈=实际引脚位置（±1 格）；
  选中图形可拖动移动（虚线框高亮）；
- 属性面板：选中图形的坐标/长短（线 X1Y1X2Y2、圆 CXCY半径、方 XYWH）
  与线条粗细（1-8px）精确调整；
- 保存：写入项目根 symbols.json（按元件类型存储，画布坐标=符号局部
  坐标，原点=元件中心），运行时自定义符号优先于内置符号渲染
  （引脚线/名称/读数/发光效果不受影响）。
"""

from __future__ import annotations

import json

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ui.circuit_scene import (
    ELEMENT_TYPES,
    GRID_SIZE,
    SYMBOLS_PATH,
    TYPE_NAME_CN,
    builtin_symbol_pixmap,
    load_custom_symbols,
)

CANVAS_W, CANVAS_H = 640, 480
PEN_COLORS = ["#2d2d2d", "#c0392b", "#1e88e5", "#2e7d32", "#b8860b"]
PIN_OFFSET = GRID_SIZE  # 引脚参考点偏移（±1 格，与元件端子一致）
PEN_WIDTH_MAX = 16      # 线条粗细上限（V1.10 由 8 放宽）


def _to_local(widget_pt):
    """画布像素坐标 → 符号局部坐标（原点=元件中心）。"""
    return (round(widget_pt.x() - CANVAS_W / 2, 1),
            round(widget_pt.y() - CANVAS_H / 2, 1))


def _shape_center(s):
    if s["kind"] == "line":
        return (s["x1"] + s["x2"]) / 2, (s["y1"] + s["y2"]) / 2
    if s["kind"] == "circle":
        return s["x"], s["y"]
    return s["x"] + s["w"] / 2, s["y"] + s["h"] / 2


def _shape_bounds(s):
    """选中虚线框范围。"""
    if s["kind"] == "line":
        return (min(s["x1"], s["x2"]) - 5, min(s["y1"], s["y2"]) - 5,
                abs(s["x2"] - s["x1"]) + 10, abs(s["y2"] - s["y1"]) + 10)
    if s["kind"] == "circle":
        return (s["x"] - s["r"] - 4, s["y"] - s["r"] - 4,
                2 * s["r"] + 8, 2 * s["r"] + 8)
    return (s["x"] - 4, s["y"] - 4, s["w"] + 8, s["h"] + 8)


def _translate(s, dx, dy):
    if s["kind"] == "line":
        for k in ("x1", "y1", "x2", "y2"):
            s[k] = round(s[k] + dx, 1)
    elif s["kind"] == "circle":
        s["x"] = round(s["x"] + dx, 1)
        s["y"] = round(s["y"] + dy, 1)
    else:
        s["x"] = round(s["x"] + dx, 1)
        s["y"] = round(s["y"] + dy, 1)


def _hit_test(shapes, x, y):
    for i in range(len(shapes) - 1, -1, -1):
        s = shapes[i]
        if s["kind"] == "line":
            x1, y1, x2, y2 = s["x1"], s["y1"], s["x2"], s["y2"]
            dx, dy = x2 - x1, y2 - y1
            den = (dx * dx + dy * dy) or 1
            t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / den))
            px, py = x1 + t * dx, y1 + t * dy
            if (x - px) ** 2 + (y - py) ** 2 <= 36:
                return i
        elif s["kind"] == "circle":
            d = ((x - s["x"]) ** 2 + (y - s["y"]) ** 2) ** 0.5
            if abs(d - s["r"]) <= 6 or d <= s["r"]:
                return i
        elif s["kind"] == "rect":
            if s["x"] - 4 <= x <= s["x"] + s["w"] + 4 and \
                    s["y"] - 4 <= y <= s["y"] + s["h"] + 4:
                return i
    return -1


class SymbolCanvas(QWidget):
    """几何画布：创建 / 选择 / 拖动图形。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(CANVAS_W, CANVAS_H)
        self.shapes = []
        self.tool = "select"      # select | line | circle | rect
        self.selected = -1
        self.pen_width = 2
        self.color = PEN_COLORS[0]
        self.on_change = None     # 选中/数据变化回调
        self._drag_idx = -1
        self._drag_off = None
        self._creating = None     # [kind, x0, y0, x1, y1]
        self.zoom = 1.0           # V1.11：画布显示缩放（0.5~4.0）

    def set_zoom(self, percent: int):
        """按百分比设置画布显示缩放（50%~400%），仅影响显示不影响数据。"""
        self.zoom = max(0.5, min(4.0, percent / 100.0))
        self.update()

    def zoom_by(self, factor: float):
        self.set_zoom(int(round(self.zoom * 100 * factor)))

    def _to_local(self, widget_pt):
        """画布像素坐标 → 符号局部坐标（考虑缩放，原点=元件中心）。"""
        s = self.zoom
        return (round((widget_pt.x() - CANVAS_W / 2) / s, 1),
                round((widget_pt.y() - CANVAS_H / 2) / s, 1))
        self._ghost = None        # 内置符号底图（半透明参考）

    def set_type(self, etype: str):
        """切换元件类型 → 更新底图（内置符号 1:1 半透明显示）。"""
        self._ghost = builtin_symbol_pixmap(etype, CANVAS_W, CANVAS_H)
        self.update()

    # -- 数据 --
    def set_shapes(self, shapes):
        self.shapes = shapes
        self.selected = -1
        self.update()
        if self.on_change:
            self.on_change()

    # -- 交互 --
    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x, y = self._to_local(event.pos())
        if self.tool == "select":
            self.selected = _hit_test(self.shapes, x, y)
            self._drag_idx = self.selected
            if self._drag_idx >= 0:
                cx, cy = _shape_center(self.shapes[self._drag_idx])
                self._drag_off = (x - cx, y - cy)
            self.update()
            if self.on_change:
                self.on_change()
            return
        self._creating = [self.tool, x, y, x, y]
        self.update()

    def mouseMoveEvent(self, event):
        x, y = self._to_local(event.pos())
        if self._creating:
            self._creating[3], self._creating[4] = x, y
            self.update()
        elif self._drag_idx >= 0 and self._drag_off:
            s = self.shapes[self._drag_idx]
            cx, cy = _shape_center(s)
            _translate(s, round(x - self._drag_off[0] - cx, 1),
                       round(y - self._drag_off[1] - cy, 1))
            self.update()
            if self.on_change:
                self.on_change()

    def mouseReleaseEvent(self, event):
        if self._creating:
            kind, x0, y0, x1, y1 = self._creating
            self._creating = None
            if max(abs(x1 - x0), abs(y1 - y0)) >= 3:
                if kind == "line":
                    self.shapes.append({"kind": "line", "x1": x0, "y1": y0,
                                        "x2": x1, "y2": y1,
                                        "pen_width": self.pen_width,
                                        "color": self.color})
                elif kind == "circle":
                    r = max(3.0, ((x1 - x0) ** 2 +
                                  (y1 - y0) ** 2) ** 0.5 / 2)
                    self.shapes.append({"kind": "circle",
                                        "x": round((x0 + x1) / 2, 1),
                                        "y": round((y0 + y1) / 2, 1),
                                        "r": round(r, 1),
                                        "pen_width": self.pen_width,
                                        "color": self.color})
                elif kind == "rect":
                    self.shapes.append({"kind": "rect",
                                        "x": round(min(x0, x1), 1),
                                        "y": round(min(y0, y1), 1),
                                        "w": round(abs(x1 - x0), 1),
                                        "h": round(abs(y1 - y0), 1),
                                        "pen_width": self.pen_width,
                                        "color": self.color})
                self.selected = len(self.shapes) - 1
                if self.on_change:
                    self.on_change()
        self._drag_idx = -1
        self._drag_off = None
        self.update()

    # -- 绘制 --
    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#FFFFFF"))
        p.setPen(QPen(QColor("#E3F0E6"), 1))
        for gx in range(0, CANVAS_W, 15):
            p.drawLine(gx, 0, gx, CANVAS_H)
        for gy in range(0, CANVAS_H, 15):
            p.drawLine(0, gy, CANVAS_W, gy)
        p.setPen(QPen(QColor("#BFDCC7"), 1))
        p.drawLine(CANVAS_W // 2, 0, CANVAS_W // 2, CANVAS_H)
        p.drawLine(0, CANVAS_H // 2, CANVAS_W, CANVAS_H)
        # ---- 内容按缩放绘制（局部坐标，原点=元件中心） ----
        p.save()
        p.translate(CANVAS_W / 2, CANVAS_H / 2)
        p.scale(self.zoom, self.zoom)
        # 内置符号底图（半透明）——当前符号长相参考
        if self._ghost is not None:
            p.save()
            p.setOpacity(0.35)
            p.drawPixmap(-CANVAS_W // 2, -CANVAS_H // 2, self._ghost)
            p.restore()
        # 引脚参考点（±1 格，蓝圈）
        p.setPen(QPen(QColor("#1e88e5"), 2))
        p.setBrush(Qt.NoBrush)
        for px, py in ((-PIN_OFFSET, 0), (PIN_OFFSET, 0)):
            p.drawEllipse(px - 4, py - 4, 8, 8)
        # 已有图形
        for i, s in enumerate(self.shapes):
            p.setPen(QPen(QColor(s.get("color", "#2d2d2d")),
                          float(s.get("pen_width", 2))))
            k = s["kind"]
            if k == "line":
                p.drawLine(int(s["x1"]), int(s["y1"]),
                           int(s["x2"]), int(s["y2"]))
            elif k == "circle":
                p.drawEllipse(int(s["x"] - s["r"]), int(s["y"] - s["r"]),
                              int(2 * s["r"]), int(2 * s["r"]))
            elif k == "rect":
                p.drawRect(int(s["x"]), int(s["y"]),
                           int(s["w"]), int(s["h"]))
        # 创建预览（虚线）
        if self._creating:
            kind, x0, y0, x1, y1 = self._creating
            p.setPen(QPen(QColor("#1e88e5"), self.pen_width, Qt.DashLine))
            if kind == "line":
                p.drawLine(int(x0), int(y0), int(x1), int(y1))
            elif kind == "circle":
                r = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / 2
                p.drawEllipse(int((x0 + x1) / 2 - r),
                              int((y0 + y1) / 2 - r),
                              int(2 * r), int(2 * r))
            elif kind == "rect":
                p.drawRect(int(min(x0, x1)), int(min(y0, y1)),
                           int(abs(x1 - x0)), int(abs(y1 - y0)))
        # 选中框（虚线）
        if 0 <= self.selected < len(self.shapes):
            bx, by, bw, bh = _shape_bounds(self.shapes[self.selected])
            p.setPen(QPen(QColor("#1e88e5"), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(int(bx), int(by), int(bw), int(bh))
        p.restore()


class SymbolEditorDialog(QDialog):
    """元件图像编辑器对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("元件图像编辑器")
        self.resize(1200, 660)
        self._prop_spinners = {}

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("元件类型："))
        self.type_box = QComboBox()
        for t in ELEMENT_TYPES:
            self.type_box.addItem(TYPE_NAME_CN[t], t)
        self.type_box.currentIndexChanged.connect(self._load_type)
        top.addWidget(self.type_box)
        top.addWidget(QLabel("　工具："))
        self._tool_btns = []
        for name, tool in (("选择", "select"), ("线条", "line"),
                           ("圆", "circle"), ("方", "rect")):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.clicked.connect(
                lambda _=False, tl=tool, b=btn: self._set_tool(tl, b))
            top.addWidget(btn)
            self._tool_btns.append(btn)
        top.addWidget(QLabel("线宽："))
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(1, PEN_WIDTH_MAX)
        self.width_spin.setDecimals(0)
        self.width_spin.setValue(2)
        self.width_spin.valueChanged.connect(self._pen_changed)
        top.addWidget(self.width_spin)
        btn_del = QPushButton("删除选中")
        btn_del.clicked.connect(self._delete_selected)
        top.addWidget(btn_del)
        btn_clear = QPushButton("恢复内置符号")
        btn_clear.clicked.connect(self._clear)
        top.addWidget(btn_clear)
        top.addStretch(1)
        root.addLayout(top)

        # 画布缩放条（V1.11）
        zrow = QHBoxLayout()
        zrow.addWidget(QLabel("画布缩放"))
        z_out = QPushButton("－")
        z_out.setFixedWidth(26)
        z_out.clicked.connect(lambda: self.canvas.zoom_by(1 / 1.25))
        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setRange(50, 400)
        self._zoom_slider.setValue(100)
        self._zoom_slider.setFixedWidth(140)
        self._zoom_slider.valueChanged.connect(
            lambda v: self.canvas.set_zoom(v))
        z_in = QPushButton("＋")
        z_in.setFixedWidth(26)
        z_in.clicked.connect(lambda: self.canvas.zoom_by(1.25))
        z_fit = QPushButton("100%")
        z_fit.setFixedWidth(44)
        z_fit.clicked.connect(lambda: self._zoom_slider.setValue(100))
        for w in (z_out, self._zoom_slider, z_in, z_fit):
            zrow.addWidget(w)
        zrow.addStretch(1)
        root.insertLayout(1, zrow)

        body = QHBoxLayout()
        self.canvas = SymbolCanvas()
        self.canvas.on_change = self._sync_props
        body.addWidget(self.canvas, 0, Qt.AlignTop)

        host = QWidget()
        host.setFixedWidth(320)
        self.props = QFormLayout(host)
        self.props.addRow(QLabel("未选中图形"))
        body.addWidget(host, 0, Qt.AlignTop)
        body.addStretch(1)
        root.addLayout(body)
        self._prop_host = host

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel(
            "蓝圈=实际引脚位置（±1 格）。选「线条/圆/方」后在画布拖动创建；"
            "「选择」工具可拖动移动；右侧精确调参。保存后立即生效。"))
        bottom.addStretch(1)
        btn_save = QPushButton("保存")
        btn_save.clicked.connect(self._save)
        bottom.addWidget(btn_save)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        self._set_tool("select", self._tool_btns[0])
        self._load_type()

    # -- 工具 --
    def _set_tool(self, tool, btn):
        self.canvas.tool = tool
        for b in self._tool_btns:
            b.setChecked(b is btn)

    def _pen_changed(self, v):
        self.canvas.pen_width = int(v)
        idx = self.canvas.selected
        if 0 <= idx < len(self.canvas.shapes):
            self.canvas.shapes[idx]["pen_width"] = int(v)
            self.canvas.update()

    # -- 属性面板 --
    def _sync_props(self):
        while self.props.count():
            item = self.props.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._prop_spinners = {}
        idx = self.canvas.selected
        if not (0 <= idx < len(self.canvas.shapes)):
            self.props.addRow(QLabel("未选中图形"))
            return
        s = self.canvas.shapes[idx]
        # 工具栏线宽跟随选中图形（V1.10）
        self.width_spin.blockSignals(True)
        self.width_spin.setValue(float(s.get("pen_width", 2)))
        self.width_spin.blockSignals(False)

        def add_spin(label, key, lo=-320, hi=320):
            sb = QDoubleSpinBox()
            sb.setRange(lo, hi)
            sb.setDecimals(1)
            sb.setValue(float(s[key]))
            sb.valueChanged.connect(
                lambda v, k=key, i=idx: self._set_field(i, k, v))
            self.props.addRow(label, sb)
            self._prop_spinners[key] = sb

        if s["kind"] == "line":
            add_spin("起点 X", "x1")
            add_spin("起点 Y", "y1")
            add_spin("终点 X（长短）", "x2")
            add_spin("终点 Y（长短）", "y2")
        elif s["kind"] == "circle":
            add_spin("圆心 X", "x")
            add_spin("圆心 Y", "y")
            add_spin("半径", "r", 2, 320)
        else:
            add_spin("X", "x")
            add_spin("Y", "y")
            add_spin("宽", "w", 1, 640)
            add_spin("高", "h", 1, 480)
        ws = QDoubleSpinBox()
        ws.setRange(1, PEN_WIDTH_MAX)
        ws.setDecimals(0)
        ws.setValue(float(s.get("pen_width", 2)))
        ws.valueChanged.connect(
            lambda v, i=idx: self._set_field(i, "pen_width", int(v)))
        self.props.addRow("线条粗细", ws)

    def _set_field(self, idx, key, value):
        if not (0 <= idx < len(self.canvas.shapes)):
            return
        self.canvas.shapes[idx][key] = value
        self.canvas.update()

    # -- 数据 --
    def _load_type(self):
        etype = self.type_box.currentData()
        self.canvas.set_type(etype)
        self.canvas.set_shapes(list(load_custom_symbols().get(etype, [])))

    def _delete_selected(self):
        idx = self.canvas.selected
        if 0 <= idx < len(self.canvas.shapes):
            self.canvas.shapes.pop(idx)
            self.canvas.selected = -1
            self.canvas.update()
            self._sync_props()

    def _clear(self):
        """恢复内置符号：清空该类型的自定义图形。"""
        self.canvas.set_shapes([])

    def _save(self):
        etype = self.type_box.currentData()
        data = {}
        if SYMBOLS_PATH.exists():
            try:
                data = json.loads(SYMBOLS_PATH.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        if self.canvas.shapes:
            data[etype] = self.canvas.shapes
        else:
            data.pop(etype, None)  # 空图形 → 恢复内置符号
        SYMBOLS_PATH.write_text(json.dumps(data, ensure_ascii=False,
                                           indent=2), encoding="utf-8")
        load_custom_symbols(force=True)
