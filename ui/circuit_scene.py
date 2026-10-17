# -*- coding: utf-8 -*-
"""P4：电路画布 —— QGraphicsScene 渲染与交互。

需求落地（含用户裁定补充）：
- **元件网格化**：所有落点吸附 GRID_SIZE=20px 网格，引脚必在格点；
- **导线 8 向**：只接受 4 主轴 + 4 对角直线段；非 8 向输入自动拆为
  L 形两段（水平/垂直），每段一个 wire 元件，拐点自动生成可续接节点；
- **护眼绿**：画布底色 #C7EDCC，网格点 #B0D5B8（需求补充_UI风格.md）；
- **学生友好**：元件符号清晰（深灰描边 ≥2px）、开关单击切换、
  悬空元件标灰、短接红字提示由主窗口状态栏承接；
- 电学连通只用 node id（拓扑层），坐标仅存在于本画布层（红线分工）。

坐标 ↔ 节点：格点坐标 (gx, gy) 唯一对应一个 node id，放置元件或画
导线时注册；删除元件时按引用计数回收。
"""

from __future__ import annotations

import copy
import json
import logging
import math
from pathlib import Path

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath, QPen,
                         QPixmap, QTransform)
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

GRID_SIZE = 30
TERMINAL_HALF = GRID_SIZE          # 元件引脚距中心 30px（2×2 格占位）

log = logging.getLogger("voltlab.scene")   # 崩溃前操作面包屑（配合 crash.log）

# 护眼绿配色（需求补充_UI风格.md）
COLOR_BG = QColor("#C7EDCC")
COLOR_GRID = QColor("#B0D5B8")
COLOR_BODY = QColor("#2d2d2d")
COLOR_WIRE = QColor("#37474f")
COLOR_ON = QColor("#ffd54f")       # 灯亮暖黄
COLOR_ON_EDGE = QColor("#f9a825")
COLOR_FLOAT = QColor("#9e9e9e")    # 悬空标灰
COLOR_SELECT = QColor("#1e88e5")

ELEMENT_TYPES = ("battery", "resistor", "bulb", "switch",
                 "voltmeter", "ammeter", "bell", "motor",
                 "diode", "led", "rheostat", "spdt", "sp3t")
TYPE_LETTER = {"battery": "b", "resistor": "r", "bulb": "lamp",
               "switch": "s", "voltmeter": "v", "ammeter": "a",
               "wire": "w", "bell": "bell", "motor": "m",
               "diode": "d", "led": "led", "rheostat": "rh",
               "spdt": "sw2", "sp3t": "sw3"}
TYPE_NAME_CN = {"battery": "电池", "resistor": "电阻", "bulb": "灯泡",
                "switch": "开关", "voltmeter": "电压表", "ammeter": "电流表",
                "wire": "导线", "bell": "电铃", "motor": "马达",
                "diode": "二极管", "led": "LED", "rheostat": "变阻器",
                "spdt": "双掷开关", "sp3t": "三掷开关"}

DEFAULT_PROPS = {
    "battery": {"E": 3.0, "r_internal": 0.0},
    "resistor": {"resistance": 10.0},
    "bulb": {"resistance": 10.0, "rated_voltage": 2.5, "rated_current": 0.3},
    "switch": {"closed": False},
    "voltmeter": {},
    "ammeter": {},
    "bell": {"resistance": 10.0},
    "motor": {"resistance": 10.0},
    "diode": {"vf": 0.7, "r_on": 1.0},
    "led": {"vf": 2.0, "r_on": 1.0, "rated_current": 0.02},
    "rheostat": {"r_max": 10.0, "position": 0.5},
    "spdt": {"position": 0},
    "sp3t": {"position": 0},
}


def snap(v: float) -> int:
    """像素 → 网格吸附（格点整数坐标）。"""
    return int(round(v / GRID_SIZE))


def wire_path_points(p0: tuple, p1: tuple):
    """两格点间的 8 向路径。

    返回 (waypoints, is_single)：waypoints 为格点列表（含拐点），
    相邻两点必构成 8 向线段；is_single 表示未拆弯。
    """
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    if p0 == p1:
        return [p0], False
    if dx == 0 or dy == 0 or abs(dx) == abs(dy):
        return [p0, p1], True
    # 非 8 向：L 形拆段（水平到目标列，再垂直/对角）——拐点为新节点
    corner = (p1[0], p0[1])
    return [p0, corner, p1], False


def _is_8dir(a: tuple, b: tuple) -> bool:
    dx, dy = b[0] - a[0], b[1] - a[1]
    return dx == 0 or dy == 0 or abs(dx) == abs(dy)


# ---------------------------------------------------------------------------
# 元件图元
# ---------------------------------------------------------------------------

class ElementItem(QGraphicsItem):
    """元件符号基类：水平摆放，引脚在 ±TERMINAL_HALF（2×2 格）；可选中、可拖动。

    多端子元件覆写 TERMINALS（格点偏移列表，顺序与 el["nodes"] 一致）。
    """

    TERMINALS = [(-1, 0), (1, 0)]

    def __init__(self, scene, el: dict, gx: int, gy: int):
        super().__init__()
        self.vscene = scene
        self.el = el
        self.gx, self.gy = gx, gy
        self.state = "normal"
        self.floating = False
        self.reading_text = ""
        self._press_gp = (gx, gy)
        self.setPos(gx * GRID_SIZE, gy * GRID_SIZE)
        self.setFlags(QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)

    def terminal_offsets(self):
        return self.TERMINALS

    def terminal_gps(self):
        return [(self.gx + dx, self.gy + dy) for dx, dy in self.TERMINALS]

    # -- 拖动吸附：拖动过程中实时吸附到格点 --
    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            gx = snap(value.x())
            gy = snap(value.y())
            return QPointF(gx * GRID_SIZE, gy * GRID_SIZE)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        new_gp = (snap(self.pos().x()), snap(self.pos().y()))
        if new_gp != self._press_gp:
            self.vscene.relocate_element(self.el["id"], self._press_gp,
                                         new_gp)
        event.accept()

    # -- 几何 --
    def boundingRect(self):
        return QRectF(-TERMINAL_HALF - 6, -40,
                      2 * TERMINAL_HALF + 12, 84)

    def _lead_pen(self):
        return QPen(self._body_color(), 2)

    def _body_color(self):
        if self.floating:
            return COLOR_FLOAT
        return QColor(COLOR_BODY)

    # -- 交互 --
    def mousePressEvent(self, event):
        self._press_gp = (snap(self.pos().x()), snap(self.pos().y()))
        t = self.el["type"]
        if event.button() == Qt.LeftButton and t in ("switch", "spdt", "sp3t"):
            if t == "switch":
                self.vscene.toggle_switch(self.el["id"])
            else:
                self.vscene.cycle_throw(self.el["id"])
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.el["type"] in ("battery", "resistor", "bulb", "rheostat",
                               "led", "bell", "motor"):
            self.vscene.edit_properties(self.el["id"])
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu()
        act_del = menu.addAction("删除元件")
        chosen = menu.exec_(event.screenPos())
        if chosen is act_del:
            self.vscene.remove_element(self.el["id"])
        event.accept()

    # -- 状态 --
    def apply_state(self, entry: dict):
        self.state = entry.get("state", "normal")
        self.floating = entry.get("floating", False)
        self._bright = float(entry.get("brightness", 0.0))
        u = entry.get("readings", {}).get("U")
        i = entry.get("readings", {}).get("I")
        if self.el["type"] == "voltmeter":
            self.reading_text = "%s" % _fmt_reading(u, "V")
        elif self.el["type"] == "ammeter":
            self.reading_text = "%s" % _fmt_reading(i, "A")
        elif self.el["type"] == "battery":
            self.reading_text = "%.1fV" % self.el["props"].get("E", 0.0)
        elif self.el["type"] == "bulb":
            self.reading_text = "额定 %.1fV" % \
                self.el["props"].get("rated_voltage", 2.5)
        elif self.el["type"] == "rheostat":
            self.reading_text = "%.0fΩ %d%%" % (
                self.el["props"].get("r_max", 10.0),
                round(self.el["props"].get("position", 0.5) * 100))
        elif self.el["type"] in ("spdt", "sp3t"):
            self.reading_text = "掷%d" % \
                (int(self.el["props"].get("position", 0)) + 1)
        else:
            self.reading_text = ""
        self.update()

    # -- 绘制辅助 --
    def _draw_leads(self, painter: QPainter):
        painter.setPen(self._lead_pen())
        for dx, dy in self.terminal_offsets():
            tx, ty = dx * GRID_SIZE, dy * GRID_SIZE
            length = (tx * tx + ty * ty) ** 0.5 or 1
            ex, ey = tx - tx / length * 12, ty - ty / length * 12
            painter.drawLine(tx, ty, ex, ey)

    def _draw_custom(self, painter: QPainter) -> bool:
        """用户自定义符号（元件图像编辑器产物）优先渲染。返回是否命中。"""
        shapes = get_custom_symbol(self.el["type"])
        if not shapes:
            return False
        for s in shapes:
            pen = QPen(QColor(s.get("color", "#2d2d2d")),
                       float(s.get("pen_width", 2)))
            painter.setPen(pen)
            if s.get("filled"):
                painter.setBrush(QBrush(QColor(s.get("brush") or "#ffffff")))
            else:
                painter.setBrush(Qt.NoBrush)
            k = s["kind"]
            if k == "line":
                painter.drawLine(s["x1"], s["y1"], s["x2"], s["y2"])
            elif k == "circle":
                painter.drawEllipse(s["x"] - s["r"], s["y"] - s["r"],
                                    2 * s["r"], 2 * s["r"])
            elif k == "rect":
                painter.drawRect(s["x"], s["y"], s["w"], s["h"])
        return True

    def _draw_label(self, painter: QPainter):
        painter.setPen(QPen(self._body_color(), 1))
        painter.setFont(QFont("", 7))
        # 名称放低、读数抬高，避免贴住符号
        painter.drawText(QRectF(-TERMINAL_HALF, 14, 2 * TERMINAL_HALF, 14),
                         Qt.AlignCenter, TYPE_NAME_CN[self.el["type"]])
        if self.reading_text:
            painter.setFont(QFont("", 8, QFont.Bold))
            painter.drawText(QRectF(-TERMINAL_HALF, -34,
                                    2 * TERMINAL_HALF, 14),
                             Qt.AlignCenter, self.reading_text)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        if self.isSelected():
            painter.setPen(QPen(COLOR_SELECT, 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(-TERMINAL_HALF - 4, -20,
                                    2 * TERMINAL_HALF + 8, 44))
        self._draw_leads(painter)
        if not self._draw_custom(painter):
            self._draw_symbol(painter)
        self._draw_label(painter)


def _fmt_reading(value, unit):
    if value is None:
        return "0 " + unit
    return "%.3g %s" % (abs(value), unit)


# ---------------------------------------------------------------------------
# 用户自定义符号（元件图像编辑器产物，symbols.json）
# ---------------------------------------------------------------------------
SYMBOLS_PATH = Path(__file__).resolve().parents[1] / "symbols.json"
_custom_cache = {"mtime": None, "data": {}}


def load_custom_symbols(force: bool = False) -> dict:
    if not SYMBOLS_PATH.exists():
        _custom_cache["data"] = {}
        _custom_cache["mtime"] = None
        return _custom_cache["data"]
    mtime = SYMBOLS_PATH.stat().st_mtime
    if force or _custom_cache["mtime"] != mtime:
        try:
            _custom_cache["data"] = json.loads(
                SYMBOLS_PATH.read_text(encoding="utf-8"))
        except Exception:
            _custom_cache["data"] = {}
        _custom_cache["mtime"] = mtime
    return _custom_cache["data"]


def get_custom_symbol(etype: str):
    return load_custom_symbols().get(etype) or []


class BatteryItem(ElementItem):
    def _draw_symbol(self, painter):
        """国标电池符号：长板（细）=正极，短粗板=负极。"""
        c = self._body_color()
        painter.setPen(QPen(c, 2))    # 正极：长而细
        painter.drawLine(-3, -12, -3, 12)
        painter.setPen(QPen(c, 4))    # 负极：短而粗
        painter.drawLine(3, -6, 3, 6)
        painter.setFont(QFont("", 8, QFont.Bold))
        painter.setPen(QPen(c, 1))
        painter.drawText(QRectF(-16, -22, 12, 12), Qt.AlignCenter, "+")


class ResistorItem(ElementItem):
    def _draw_symbol(self, painter):
        painter.setPen(QPen(self._body_color(), 2))
        painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
        painter.drawRect(-10, -6, 20, 12)


class BulbItem(ElementItem):
    def _draw_symbol(self, painter):
        on = self.state == "on" and not self.floating
        brightness = 0.0
        if on:
            # 电流越大越亮：亮度由求解层给出（|I|/额定电流）
            brightness = max(0.15, min(1.0, getattr(self, "_bright", 0.0)))
        painter.setPen(QPen(COLOR_ON_EDGE if on else self._body_color(), 2))
        if self.floating:
            painter.setBrush(QBrush(QColor(200, 200, 200, 180)))  # 悬空标灰
        elif on:
            painter.setBrush(QBrush(QColor(255, 213, 79,
                                           int(120 + 135 * brightness))))
        else:
            painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
        painter.drawEllipse(-9, -9, 18, 18)
        painter.drawLine(-6, -6, 6, 6)
        painter.drawLine(-6, 6, 6, -6)
        if on:  # 光晕随亮度增强
            painter.setPen(QPen(QColor(255, 213, 79,
                                       int(40 + 140 * brightness)), 5))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(-14, -14, 28, 28)


class SwitchItem(ElementItem):
    def _draw_symbol(self, painter):
        c = self._body_color()
        painter.setPen(QPen(c, 2))
        painter.setBrush(QBrush(c))
        painter.drawEllipse(-9, -3, 6, 6)
        painter.drawEllipse(3, -3, 6, 6)
        closed = self.el["props"].get("closed", False)
        if closed:
            painter.drawLine(-6, 0, 6, 0)
        else:
            painter.drawLine(-6, 0, 5, -9)


class MeterItem(ElementItem):
    letter = "?"

    def _draw_symbol(self, painter):
        painter.setPen(QPen(self._body_color(), 2))
        painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
        painter.drawEllipse(-10, -10, 20, 20)
        painter.setFont(QFont("", 8, QFont.Bold))
        painter.drawText(QRectF(-10, -10, 20, 20), Qt.AlignCenter,
                         self.letter)


class VoltmeterItem(MeterItem):
    letter = "V"


class AmmeterItem(MeterItem):
    letter = "A"


class BellItem(ElementItem):
    """电铃：拱形铃 + 底座，通电时显示敲击符号。"""

    def _draw_symbol(self, painter):
        on = self.state == "on" and not self.floating
        c = QColor("#b8860b") if on else self._body_color()
        painter.setPen(QPen(c, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(QRectF(-9, -10, 18, 18), 0, 180 * 16)  # 铃拱
        painter.drawLine(-12, 0, 12, 0)                        # 底座
        painter.setBrush(QBrush(c))
        painter.drawEllipse(-2, -13, 4, 4)                     # 撞锤
        if on:
            painter.setPen(QPen(QColor("#e67e22"), 2))
            painter.drawLine(6, -14, 10, -18)


class MotorItem(ElementItem):
    """马达：圆圈 + M，通电时变色。"""

    def _draw_symbol(self, painter):
        on = self.state == "on" and not self.floating
        c = QColor("#2e7d32") if on else self._body_color()
        painter.setPen(QPen(c, 2))
        painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
        painter.drawEllipse(-10, -10, 20, 20)
        painter.setFont(QFont("", 8, QFont.Bold))
        painter.drawText(QRectF(-10, -10, 20, 20), Qt.AlignCenter, "M")


class DiodeItem(ElementItem):
    """二极管：三角+势垒线，方向为 a→b（左→右）。"""

    def _draw_symbol(self, painter):
        on = self.state == "on" and not self.floating
        c = self._body_color()
        painter.setPen(QPen(c, 2))
        painter.setBrush(QBrush(c) if on else QBrush(
            QColor(255, 255, 255, 220)))
        painter.drawPolygon(*[QPointF(-7, -7), QPointF(-7, 7),
                              QPointF(5, 0)])
        painter.drawLine(5, -7, 5, 7)

    def apply_state(self, entry: dict):
        super().apply_state(entry)


class LedItem(DiodeItem):
    """发光二极管：二极管 + 发光箭头，导通时红光。"""

    def _draw_symbol(self, painter):
        super()._draw_symbol(painter)
        on = self.state == "on" and not self.floating
        bright = max(0.15, min(1.0, getattr(self, "_bright", 0.0)))
        if on:
            painter.setPen(QPen(QColor(229, 57, 53,
                                       int(60 + 180 * bright)), 4))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(-13, -13, 26, 26)
        painter.setPen(QPen(self._body_color(), 1.5))
        painter.drawLine(6, -9, 10, -13)
        painter.drawLine(10, -7, 14, -11)


class RheostatItem(ElementItem):
    """滑动变阻器：三端子（A 左、B 右、滑片 W 上），矩形 + 滑片箭头。"""

    TERMINALS = [(-1, 0), (1, 0), (0, -1)]

    def _draw_symbol(self, painter):
        c = self._body_color()
        painter.setPen(QPen(c, 2))
        painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
        painter.drawRect(-10, -5, 20, 10)
        # 滑片箭头（位置随 position 移动）
        pos = min(1.0, max(0.0, float(self.el["props"].get("position", 0.5))))
        ax = -10 + 20 * pos
        painter.drawLine(int(ax), -20, int(ax), -5)
        painter.drawLine(int(ax) - 3, -9, int(ax), -5)
        painter.drawLine(int(ax) + 3, -9, int(ax), -5)
        painter.setFont(QFont("", 6))
        painter.drawText(QRectF(-10, -5, 20, 10), Qt.AlignCenter,
                         "%.0fΩ" % self.el["props"].get("r_max", 10.0))


class SpdtItem(ElementItem):
    """单刀多掷开关基类：公共端在左，掷位在右；点击切换掷位。"""

    TERMINALS = [(-1, 0), (1, -1), (1, 1)]

    def _draw_symbol(self, painter):
        c = self._body_color()
        painter.setPen(QPen(c, 2))
        painter.setBrush(QBrush(c))
        offs = self.terminal_offsets()
        com_px = QPointF(offs[0][0] * GRID_SIZE, offs[0][1] * GRID_SIZE)
        painter.drawEllipse(com_px.x() - 3, com_px.y() - 3, 6, 6)
        throw_px = []
        for dx, dy in offs[1:]:
            px = QPointF(dx * GRID_SIZE, dy * GRID_SIZE)
            throw_px.append(px)
            painter.drawEllipse(px.x() - 3, px.y() - 3, 6, 6)
        pos = int(self.el["props"].get("position", 0))
        pos = max(0, min(pos, len(throw_px) - 1))
        painter.drawLine(com_px.x(), com_px.y(),
                         throw_px[pos].x(), throw_px[pos].y())

    def mousePressEvent(self, event):
        self._press_gp = (snap(self.pos().x()), snap(self.pos().y()))
        if event.button() == Qt.LeftButton:
            self.vscene.cycle_throw(self.el["id"])
            event.accept()
            return
        super().mousePressEvent(event)


class Sp3tItem(SpdtItem):
    TERMINALS = [(-1, 0), (1, -1), (1, 0), (1, 1)]


ITEM_CLASS = {"battery": BatteryItem, "resistor": ResistorItem,
              "bulb": BulbItem, "switch": SwitchItem,
              "voltmeter": VoltmeterItem, "ammeter": AmmeterItem,
              "bell": BellItem, "motor": MotorItem,
              "diode": DiodeItem, "led": LedItem,
              "rheostat": RheostatItem, "spdt": SpdtItem,
              "sp3t": Sp3tItem}


_ICON_CACHE: dict = {}


def type_icon_pixmap(etype: str, size: int = 40) -> QPixmap:
    """元件类型 → 符号图标（元件箱「符号模式」用），带缓存。

    独立渲染：临时构建图元（不入场景），按真实符号绘制
    引脚 + 符号主体（不走 symbols.json 自定义渲染）。
    """
    if etype in _ICON_CACHE:
        return _ICON_CACHE[etype]
    from core.topology import element_nodes_count
    el = {"id": "_icon", "type": etype,
          "nodes": ["n%d" % i for i in range(element_nodes_count(etype))],
          "props": dict(DEFAULT_PROPS.get(etype, {}))}
    item = ITEM_CLASS[etype](None, el, 0, 0)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(size / 2, size / 2)
    p.scale(size / 44.0, size / 44.0)   # 显示中心 ±22px：符号完整、引脚延到边
    item._draw_leads(p)
    item._draw_symbol(p)
    p.end()
    _ICON_CACHE[etype] = pm
    return pm


_BUILTIN_CACHE: dict = {}


def builtin_symbol_pixmap(etype: str, w: int, h: int) -> QPixmap:
    """内置符号 1:1 像素图（原点=画布中心，元件图像编辑器底图用）。"""
    key = (etype, w, h)
    if key in _BUILTIN_CACHE:
        return _BUILTIN_CACHE[key]
    from core.topology import element_nodes_count
    el = {"id": "_ghost", "type": etype,
          "nodes": ["n%d" % i for i in range(element_nodes_count(etype))],
          "props": dict(DEFAULT_PROPS.get(etype, {}))}
    item = ITEM_CLASS[etype](None, el, 0, 0)
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(w / 2, h / 2)
    item._draw_leads(p)
    item._draw_symbol(p)
    p.end()
    _BUILTIN_CACHE[key] = pm
    return pm


class WireItem(QGraphicsItem):
    """导线：waypoints 为格点坐标列表（像素 = 格点 × GRID_SIZE）。"""

    def __init__(self, scene, el: dict, waypoints: list):
        super().__init__()
        self.vscene = scene
        self.el = el
        self.waypoints = waypoints
        self.setZValue(-1)  # 导线在元件下方
        # V1.13：导线可选中（Delete 键 / delete_selected 删除）
        self.setFlags(QGraphicsItem.ItemIsSelectable)

    def boundingRect(self):
        xs = [p[0] * GRID_SIZE for p in self.waypoints]
        ys = [p[1] * GRID_SIZE for p in self.waypoints]
        return QRectF(min(xs) - 4, min(ys) - 4,
                      max(xs) - min(xs) + 8, max(ys) - min(ys) + 8)

    def set_waypoints(self, waypoints: list):
        """端点跟随元件拖动：更新途经点并重绘。"""
        self.prepareGeometryChange()
        self.waypoints = waypoints
        self.update()

    def contextMenuEvent(self, event):
        menu = QMenu()
        act_del = menu.addAction("删除导线")
        chosen = menu.exec_(event.screenPos())
        if chosen is act_del:
            self.vscene.remove_element(self.el["id"])
        event.accept()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        if self.isSelected():   # V1.13：选中高亮（蓝色加粗）
            painter.setPen(QPen(COLOR_SELECT, 3.5,
                                Qt.SolidLine, Qt.RoundCap))
        else:
            painter.setPen(QPen(COLOR_WIRE, 2.5,
                                Qt.SolidLine, Qt.RoundCap))
        x0, y0 = self.waypoints[0]
        path = QPainterPath(QPointF(x0 * GRID_SIZE, y0 * GRID_SIZE))
        for p in self.waypoints[1:]:
            path.lineTo(p[0] * GRID_SIZE, p[1] * GRID_SIZE)
        painter.drawPath(path)


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

class CircuitScene(QGraphicsScene):
    """管理元件/导线图元与 node id 注册，对外输出需求书 §11 数据结构。"""

    circuit_changed_signal = pyqtSignal()
    notice_signal = pyqtSignal(str)   # 画布给状态栏的用户提示

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackgroundBrush(QBrush(COLOR_BG))
        self.elements: list = []          # 需求书 §11 结构
        self._items: dict = {}            # id -> QGraphicsItem
        self._node_at: dict = {}          # (gx,gy) -> node id
        self._node_refs: dict = {}        # node id -> 引用计数
        self._cells: dict = {}            # (gx,gy) -> [(元件id, 'c'|'t')]
        self._geometry: dict = {}         # 元件id -> {"center":..}/{"waypoints":..}
        self._undo_stack: list = []
        self._node_seq = 0
        self._id_seq: dict = {}
        self.tool = None                  # None=选择 | 类型名=放置 | 'wire'
        self._wire_start = None           # (gx,gy)
        self._wire_marker = None

    # -- 背景网格已移至视图层 GridGraphicsView（grab()/视口渲染走视图级 drawBackground）--

    # -- 工具 --
    def set_tool(self, tool):
        self.tool = tool
        self._cancel_wire_start()

    def _cancel_wire_start(self):
        self._wire_start = None
        if self._wire_marker is not None:
            self.removeItem(self._wire_marker)
            self._wire_marker = None

    # -- 节点注册 --
    def _new_node(self) -> str:
        self._node_seq += 1
        return "n%d" % self._node_seq

    def _node_of(self, gp: tuple) -> str:
        nid = self._node_at.get(gp)
        if nid is None:
            nid = self._new_node()
            self._node_at[gp] = nid
            self._node_refs[nid] = 0
        self._node_refs[nid] += 1
        return nid

    def _release_node(self, nid):
        self._node_refs[nid] -= 1
        if self._node_refs[nid] <= 0:
            self._node_refs.pop(nid, None)
            for gp, n in list(self._node_at.items()):
                if n == nid:
                    del self._node_at[gp]

    # -- 鼠标 --
    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        gp = (snap(event.scenePos().x()), snap(event.scenePos().y()))
        if self.tool == "wire":
            self._handle_wire_click(gp)
            event.accept()
            return
        if self.tool in ELEMENT_TYPES:
            self.place_element(self.tool, gp[0], gp[1])
            event.accept()
            return
        super().mousePressEvent(event)

    def _handle_wire_click(self, gp: tuple):
        # V1.13：点击既有导线中段 → 原位拆分出节点（T 形连接空白节点）
        w = self._wire_segment_containing(gp)
        if w is not None:
            self.push_undo()
            self._split_wire_at(w["id"], gp)
        gp = self._snap_to_terminal(gp)   # 引脚就近吸附
        if self._wire_start is None:
            self._wire_start = gp
            self._wire_marker = self.addEllipse(
                gp[0] * GRID_SIZE - 4, gp[1] * GRID_SIZE - 4, 8, 8,
                pen=QPen(COLOR_SELECT, 2), brush=QBrush(COLOR_SELECT))
            return
        start = self._wire_start
        self._cancel_wire_start()
        if start == gp:
            return
        self.add_wire(start, gp)

    def _snap_to_terminal(self, gp: tuple) -> tuple:
        """点击点若紧邻某元件引脚（1 格内），吸附到该引脚格点。"""
        if gp in self._node_at:
            return gp
        for el in self.elements:
            if el["type"] == "wire":
                continue
            item = self._items.get(el["id"])
            if item is None or not hasattr(item, "terminal_gps"):
                continue
            for t in item.terminal_gps():
                if max(abs(t[0] - gp[0]), abs(t[1] - gp[1])) == 1:
                    return t
        return gp

    # -- 放置 / 连线 --
    # -- 占位碰撞（中心格与引脚格区分：引脚相接=串联，允许同格）--
    def _cells_register(self, eid: str, center: tuple, terminals: list):
        self._cells.setdefault(center, []).append((eid, "c"))
        for t in terminals:
            self._cells.setdefault(t, []).append((eid, "t"))

    def _cells_unregister(self, eid: str, center: tuple, terminals: list):
        for gp in (center,) + tuple(terminals):
            lst = [x for x in self._cells.get(gp, []) if x[0] != eid]
            if lst:
                self._cells[gp] = lst
            else:
                self._cells.pop(gp, None)

    def _collide(self, etype: str, gx: int, gy: int) -> bool:
        """中心格被任何占用 → 拒；引脚格压到别人中心 → 拒；引脚-引脚相接 → 允许。"""
        if self._cells.get((gx, gy)):
            return True
        for dx, dy in ITEM_CLASS[etype].TERMINALS:
            for _eid, kind in self._cells.get((gx + dx, gy + dy), []):
                if kind == "c":
                    return True
        return False

    def place_element(self, etype: str, gx: int, gy: int) -> str:
        """在格点 (gx,gy) 放置元件（引脚偏移由符号类定义）。重叠则拒绝。"""
        log.info("place %s @(%d,%d)", etype, gx, gy)
        if self._collide(etype, gx, gy):
            self.notice_signal.emit("放置失败：该位置已有元件，请换个空位。")
            return ""
        self.push_undo()
        terminal_gps = [(gx + dx, gy + dy)
                        for dx, dy in ITEM_CLASS[etype].TERMINALS]
        nodes = [self._node_of(gp) for gp in terminal_gps]
        self._id_seq[etype] = self._id_seq.get(etype, 0) + 1
        el = {"id": "%s%d" % (TYPE_LETTER[etype], self._id_seq[etype]),
              "type": etype, "nodes": nodes,
              "props": copy.deepcopy(DEFAULT_PROPS[etype])}
        item = ITEM_CLASS[etype](self, el, gx, gy)
        self.addItem(item)
        self.elements.append(el)
        self._items[el["id"]] = item
        self._cells_register(el["id"], (gx, gy), terminal_gps)
        self._geometry[el["id"]] = {"center": (gx, gy)}
        self.circuit_changed_emit()
        self._auto_insert_wire(el)
        return el["id"]

    def _auto_insert_wire(self, el: dict):
        """元件落在水平导线上 → 自动切断导线并串入元件（CircuitJS 体验）。"""
        gx, gy = self._geometry.get(el["id"], {}).get("center", (None, None))
        if gx is None:
            return
        offs = ITEM_CLASS[el["type"]].TERMINALS
        t1 = (gx + offs[0][0], gy + offs[0][1])
        t2 = (gx + offs[-1][0], gy + offs[-1][1])
        if offs[0][1] != 0 or offs[-1][1] != 0:
            return  # 仅支持水平端子元件（水平导线）
        for w in list(self.elements):
            if w["type"] != "wire":
                continue
            wi = self._items.get(w["id"])
            if wi is None or len(wi.waypoints) != 2:
                continue
            (ax, ay), (bx, by) = wi.waypoints
            if ay != by or ay != gy:
                continue  # 仅支持水平导线
            if not (min(ax, bx) < gx < max(ax, bx)):
                continue
            if not (min(ax, bx) <= t1[0] and t2[0] <= max(ax, bx)):
                continue
            # 拆除导线，重建两段
            self._items.pop(w["id"])
            self.removeItem(wi)
            self.elements.remove(w)
            for n in w["nodes"]:
                self._release_node(n)
            self._geometry.pop(w["id"], None)
            self.add_wire((ax, ay), t1)
            self.add_wire(t2, (bx, by))
            self.notice_signal.emit("已自动切断导线并串入新元件。")
            return

    def relocate_element(self, eid: str, old_c: tuple, new_c: tuple):
        """元件拖动重定位：占位表/节点注册迁移，导线端点跟随。"""
        el = self._find(eid)
        if el is None:
            return
        item = self._items[eid]
        offs = item.terminal_offsets()
        old_t = [(old_c[0] + dx, old_c[1] + dy) for dx, dy in offs]
        new_t = [(new_c[0] + dx, new_c[1] + dy) for dx, dy in offs]
        self.push_undo()
        for i, (ogp, ngp) in enumerate(zip(old_t, new_t)):
            if ogp == ngp:
                continue
            nid = self._node_at.get(ogp)
            if nid is None or el["nodes"][i] != nid:
                continue
            new_nid = self._node_at.get(ngp)
            if new_nid is None:
                if self._node_refs.get(nid, 0) <= 1:
                    # 节点只被本引脚引用 → 整体搬到新格点
                    del self._node_at[ogp]
                    self._node_at[ngp] = nid
                    new_nid = nid
                else:
                    # 旧格点还有别的连接 → 旧节点留下，引脚改用新节点
                    self._release_node(nid)
                    new_nid = self._node_of(ngp)
            el["nodes"][i] = new_nid
            # 导线端点跟随
            for w in self._items.values():
                if not isinstance(w, WireItem):
                    continue
                wps = list(w.waypoints)
                changed = False
                for j, wp in enumerate(wps):
                    if wp == ogp and j < len(w.el["nodes"]) and \
                            w.el["nodes"][j] == nid:
                        wps[j] = ngp
                        w.el["nodes"][j] = new_nid
                        changed = True
                if changed:
                    w.set_waypoints(wps)
                    self._geometry[w.el["id"]] = {"waypoints": wps}
        item.gx, item.gy = new_c
        self._geometry[eid] = {"center": new_c}
        # 占位表迁移
        self._cells_unregister(eid, old_c, old_t)
        self._cells_register(eid, new_c, new_t)
        self.circuit_changed_emit()

    def add_wire(self, p0: tuple, p1: tuple) -> list:
        """8 向导线（非 8 向自动 L 拆段）。返回 wire 元件 id 列表。

        端点可以是任意空白格点（自动注册新节点），也可以落在既有导线
        中段（该导线被原位拆分出节点，实现 T 形连接）。
        """
        waypoints, _single = wire_path_points(p0, p1)
        if len(waypoints) < 2:
            return []
        self.push_undo()
        ids = self._create_wire_path(waypoints)
        self.circuit_changed_emit()
        return ids

    def _create_wire_path(self, waypoints: list) -> list:
        """按途经点创建导线段（不压撤销、不发信号）。"""
        node_ids = [self._node_of(gp) for gp in waypoints]
        ids = []
        for i in range(len(waypoints) - 1):
            a, b = waypoints[i], waypoints[i + 1]
            if a == b:
                continue
            self._id_seq["wire"] = self._id_seq.get("wire", 0) + 1
            wid = "w%d" % self._id_seq["wire"]
            el = {"id": wid, "type": "wire",
                  "nodes": [node_ids[i], node_ids[i + 1]], "props": {}}
            item = WireItem(self, el, [a, b])
            self.addItem(item)
            self.elements.append(el)
            self._items[wid] = item
            self._geometry[wid] = {"waypoints": [a, b]}
            ids.append(wid)
        return ids

    # -- V1.13：导线中段拆分（T 形连接） --
    @staticmethod
    def _point_on_segment(gp: tuple, a: tuple, b: tuple) -> bool:
        """gp 是否落在 a-b 线段内部（8 向，端点除外）。"""
        ax, ay = a
        bx, by = b
        px, py = gp
        if gp in (a, b):
            return False
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return False
        if dx * (py - ay) != dy * (px - ax):   # 共线判定
            return False
        return (min(ax, bx) <= px <= max(ax, bx)
                and min(ay, by) <= py <= max(ay, by))

    def _wire_segment_containing(self, gp: tuple):
        """返回内部含 gp 的导线元件（端点除外），无则 None。"""
        for el in self.elements:
            if el["type"] != "wire":
                continue
            wi = self._items.get(el["id"])
            if wi is None or len(wi.waypoints) != 2:
                continue
            a, b = wi.waypoints
            if self._point_on_segment(gp, a, b):
                return el
        return None

    def _split_wire_at(self, eid: str, gp: tuple):
        """把导线 eid 在 gp 处拆为两段（gp 成为新节点，T 形连接点）。"""
        el = self._find(eid)
        if not el:
            return
        wi = self._items[eid]
        a, b = wi.waypoints
        self.remove_element(eid, undo=False)   # 端点节点按引用计数处理
        self._create_wire_path([a, gp])
        self._create_wire_path([gp, b])
        self.circuit_changed_emit()

    def toggle_switch(self, eid: str):
        el = self._find(eid)
        if not el:
            return
        self.push_undo()
        el["props"]["closed"] = not el["props"].get("closed", False)
        self.circuit_changed_emit()

    def cycle_throw(self, eid: str):
        """单刀双掷/三掷：切换选通掷位。"""
        el = self._find(eid)
        if not el:
            return
        log.info("cycle_throw %s -> pos %d", eid,
                 int(el["props"].get("position", 0)) + 1)
        self.push_undo()
        n = len(el["nodes"]) - 1
        el["props"]["position"] = \
            (int(el["props"].get("position", 0)) + 1) % n
        self.circuit_changed_emit()

    # -- 属性编辑（V1.10：多参数元件合并到一个窗口，OK 全部生效） --
    _PARAM_DIALOGS = {
        "battery": [("E", "电动势 E（伏特）", 0.1, 24.0, 2),
                    ("r_internal", "内阻 r（欧姆，初中忽略）", 0.0, 10.0, 2)],
        "resistor": [("resistance", "电阻（欧姆）", 0.1, 1e6, 2)],
        "bell": [("resistance", "电阻（欧姆）", 0.1, 1e6, 2)],
        "motor": [("resistance", "电阻（欧姆）", 0.1, 1e6, 2)],
        "bulb": [("resistance", "灯丝电阻（欧姆）", 0.1, 1000.0, 2),
                 ("rated_voltage", "额定电压（伏特）", 0.1, 220.0, 2),
                 ("rated_current", "额定电流（安培，亮度基准）", 0.001, 10.0, 3)],
        "rheostat": [("r_max", "最大阻值（欧姆）", 0.1, 1e5, 2),
                     ("position", "滑片位置（0-100%）", 0.0, 100.0, 1)],
        "led": [("rated_current", "额定电流（安培，亮度基准）", 0.001, 1.0, 3)],
    }

    def _make_props_dialog(self, el: dict):
        """元件 → 单个多参数编辑窗口（dlg._spins 供测试/应用读取）。"""
        t = el["type"]
        fields = self._PARAM_DIALOGS.get(t)
        if not fields:
            return None
        p = el["props"]
        views = self.views()
        dlg = QDialog(views[0] if views else None)
        dlg.setWindowTitle("编辑%s — %s（确定后全部生效）"
                           % (TYPE_NAME_CN[t], el["id"]))
        form = QFormLayout(dlg)
        spins = {}
        for key, label, lo, hi, dec in fields:
            sb = QDoubleSpinBox()
            sb.setRange(lo, hi)
            sb.setDecimals(dec)
            if key == "position":
                sb.setValue(float(p.get("position", 0.5)) * 100)
            else:
                sb.setValue(float(p.get(
                    key, DEFAULT_PROPS.get(t, {}).get(key, 0.0))))
            form.addRow(label, sb)
            spins[key] = sb
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        form.addRow(box)
        dlg._spins = spins
        return dlg

    def _apply_props_dialog(self, el: dict, dlg) -> None:
        self.push_undo()
        for key, sb in dlg._spins.items():
            if key == "position":
                el["props"]["position"] = sb.value() / 100.0
            else:
                el["props"][key] = sb.value()
        self.circuit_changed_emit()

    def edit_properties(self, eid: str):
        el = self._find(eid)
        if not el:
            return
        dlg = self._make_props_dialog(el)
        if dlg is None:
            return
        if dlg.exec_() == QDialog.Accepted:
            self._apply_props_dialog(el, dlg)

    def remove_element(self, eid: str, undo: bool = True):
        el = self._find(eid)
        if not el:
            return
        log.info("remove %s(%s) undo=%s", el["type"], eid, undo)
        if undo:
            self.push_undo()
        item = self._items.pop(eid)
        self.removeItem(item)
        self.elements.remove(el)
        for n in el["nodes"]:
            self._release_node(n)
        if hasattr(item, "gx") and hasattr(item, "terminal_gps"):
            self._cells_unregister(eid, (item.gx, item.gy),
                                   item.terminal_gps())
        self._geometry.pop(eid, None)
        self.circuit_changed_emit()

    def delete_selected(self) -> int:
        """Delete 键：删除所有选中图元（元件/导线）。返回删除数量。

        一次操作只压一条撤销记录。
        """
        ids = [it.el["id"] for it in self.selectedItems()
               if hasattr(it, "el") and it.el["id"] in self._items]
        if not ids:
            return 0
        self.push_undo()
        for eid in ids:
            self.remove_element(eid, undo=False)
        return len(ids)

    # -- 撤销 --
    def push_undo(self):
        self._undo_stack.append({
            "elements": copy.deepcopy(self.elements),
            "geometry": copy.deepcopy(self._geometry),
            "node_seq": self._node_seq,
            "id_seq": dict(self._id_seq),
        })
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)

    def undo(self):
        if not self._undo_stack:
            self.notice_signal.emit("没有可撤销的操作。")
            return
        log.info("undo (stack=%d)", len(self._undo_stack) - 1)
        snap = self._undo_stack.pop()
        for it in list(self._items.values()):
            self.removeItem(it)
        self._items.clear()
        self.elements = copy.deepcopy(snap["elements"])
        self._geometry = copy.deepcopy(snap["geometry"])
        self._node_at.clear()
        self._node_refs.clear()
        self._cells.clear()
        self._node_seq = snap["node_seq"]
        self._id_seq = dict(snap["id_seq"])
        self._cancel_wire_start()
        for el in self.elements:
            t = el["type"]
            geo = self._geometry.get(el["id"], {})
            if t == "wire":
                wps = geo.get("waypoints") or [(0, 0), (2, 0)]
                item = WireItem(self, el, wps)
            else:
                gx, gy = geo.get("center", (0, 0))
                item = ITEM_CLASS[t](self, el, gx, gy)
                self._cells_register(el["id"], (gx, gy), item.terminal_gps())
            self.addItem(item)
            self._items[el["id"]] = item
            gps = item.waypoints if isinstance(item, WireItem) \
                else item.terminal_gps()
            for nid, gp in zip(el["nodes"], gps):
                self._node_at[gp] = nid
                self._node_refs[nid] = self._node_refs.get(nid, 0) + 1
        self.circuit_changed_emit()

    def clear_all(self):
        if self.elements:
            self.push_undo()
        for eid in [e["id"] for e in list(self.elements)]:
            item = self._items.pop(eid, None)
            if item is not None:
                self.removeItem(item)
        self.elements.clear()
        self._items.clear()
        self._node_at.clear()
        self._node_refs.clear()
        self._cells.clear()
        self._geometry.clear()
        self._node_seq = 0
        self._id_seq.clear()
        self._cancel_wire_start()
        self.circuit_changed_emit()

    # -- 状态应用 --
    def apply_states(self, states: dict, error: str | None):
        for eid, item in self._items.items():
            if isinstance(item, WireItem):
                continue
            entry = states.get(eid, {"state": "normal", "floating": False,
                                     "readings": {"U": None, "I": None}})
            item.apply_state(entry)

    # -- 输出 --
    def get_elements(self) -> list:
        return copy.deepcopy(self.elements)

    # -- 内部 --
    def _find(self, eid):
        for el in self.elements:
            if el["id"] == eid:
                return el
        return None

    def circuit_changed_emit(self):
        # 主窗口连接本信号做防抖求解（见 main_window.py）
        self.circuit_changed_signal.emit()


class GridGraphicsView(QGraphicsView):
    """画布视图：视图级 drawBackground 画护眼绿网格（视图与 grab() 都生效）。

    V1.7：Ctrl+滚轮 / zoom API 缩放（30%~400%），zoom_changed 同步缩放条。
    """

    zoom_changed = pyqtSignal(int)
    ZOOM_MIN, ZOOM_MAX = 30, 400

    def __init__(self, parent=None):
        super().__init__(parent)
        self._overlay = None

    def set_overlay(self, widget):
        """在视口右下角悬浮覆盖显示控件（V1.9 缩放滑轨），随视图缩放跟随。"""
        self._overlay = widget
        widget.setParent(self.viewport())
        widget.show()
        self._place_overlay()

    def _place_overlay(self):
        ov = self._overlay
        if ov is None:
            return
        vp = self.viewport()
        ov.move(max(6, vp.width() - ov.width() - 12),
                max(6, vp.height() - ov.height() - 12))
        ov.raise_()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place_overlay()

    def current_zoom(self) -> int:
        return int(round(self.transform().m11() * 100))

    def zoom_to(self, percent: int):
        percent = max(self.ZOOM_MIN, min(self.ZOOM_MAX, int(percent)))
        s = percent / 100.0
        self.setTransform(QTransform().scale(s, s))
        self.zoom_changed.emit(self.current_zoom())

    def zoom_by(self, factor: float):
        self.zoom_to(int(round(self.current_zoom() * factor)))

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
            return
        super().wheelEvent(event)

    def drawBackground(self, painter: QPainter, rect: QRectF):
        super().drawBackground(painter, rect)
        painter.setRenderHint(QPainter.Antialiasing, False)
        gx0 = int(rect.left()) // GRID_SIZE
        gx1 = int(rect.right()) // GRID_SIZE + 1
        gy0 = int(rect.top()) // GRID_SIZE
        gy1 = int(rect.bottom()) // GRID_SIZE + 1
        # 普通网格线（淡绿，清晰可辨）
        painter.setPen(QPen(QColor("#A9D2B4"), 1))
        for gx in range(gx0, gx1 + 1):
            painter.drawLine(gx * GRID_SIZE, int(rect.top()),
                             gx * GRID_SIZE, int(rect.bottom()))
        for gy in range(gy0, gy1 + 1):
            painter.drawLine(int(rect.left()), gy * GRID_SIZE,
                             int(rect.right()), gy * GRID_SIZE)
        # 每 5 格一条更深的线，便于数格
        painter.setPen(QPen(QColor("#82BD96"), 1))
        for gx in range(gx0 - gx0 % 5, gx1 + 1, 5):
            painter.drawLine(gx * GRID_SIZE, int(rect.top()),
                             gx * GRID_SIZE, int(rect.bottom()))
        for gy in range(gy0 - gy0 % 5, gy1 + 1, 5):
            painter.drawLine(int(rect.left()), gy * GRID_SIZE,
                             int(rect.right()), gy * GRID_SIZE)
