# -*- coding: utf-8 -*-
"""P4：主窗口（最小可交付壳）。

- 左侧元件栏（学生友好大按钮）、画布居中、底部状态栏；
- 求解走 run_with_watchdog（QThread + QTimer 800ms 看门狗，需求书第五章）；
- 电路变更 150ms 防抖后自动重算；
- 短接/超时 → 状态栏红字（学生口吻提示）；
- 初中/高中模式切换（高中暴露内阻编辑，双击电池）。

P5 将补全：Help > About 开源声明、置灰的"导入习题"菜单、菜单栏整体。
"""

from __future__ import annotations

import logging

from PyQt5.QtCore import QRectF, QSize, Qt, QTimer
from PyQt5.QtGui import (QBrush, QColor, QIcon, QImage, QPainter, QPalette,
                         QPen)
from PyQt5.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.logging_setup import setup_logging
from core.solver import calc_visual_state
from core.watchdog import run_with_watchdog
from ui.circuit_scene import (  # noqa: F401
    COLOR_BG,
    CircuitScene,
    ELEMENT_TYPES,
    GridGraphicsView,
    TYPE_NAME_CN,
    type_icon_pixmap,
)
from ui.graph_dialog import GraphDialog

log = logging.getLogger("voltlab.ui")

ERR_TEXT = {
    "short": "电源被短路了！请检查导线有没有直接连住电池两端。",
    "timeout": "求解超时，请检查环路。",
}

ABOUT_TEXT = """VoltLab v0.1

数值求解算法参考自：
- CircuitJS1 (Falstad, GPLv2)
- PhET Circuit Construction Kit (AGPL)

本软件参考了上述项目的公开算法思路，求解内核在本项目内独立编写。
图标来源：自绘（Qt QPainter 矢量符号）。

适用对象：初中 / 高中物理电路教学。"""


def load_exercise(path):
    """二阶段预留（需求书 第八章）：导入老师习题 JSON。

    数据结构已提前固定（后续不改动）::

        {"id": "exercise_001", "grade": "junior",
         "components": [], "expected_readings": {}, "prompt": ""}

    V1 仅预留入口：菜单"文件 > 导入习题..."置灰并悬浮提示"二阶段开放"。
    """
    raise NotImplementedError("二阶段开放")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        setup_logging()
        self.setWindowTitle("VoltLab — 电路实验室")
        self.resize(1080, 700)
        self.mode = "junior"

        self._build_menus()

        self.scene = CircuitScene(self)
        self.scene.notice_signal.connect(self._show_notice)
        self.view = GridGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing, True)
        self.view.setBackgroundBrush(QColor(COLOR_BG))

        # -- 左侧元件栏（V1.7 加宽 + 符号模式切换） --
        palette_panel = QFrame()
        palette_panel.setFixedWidth(190)
        palette_panel.setAutoFillBackground(True)
        pal = palette_panel.palette()
        pal.setColor(QPalette.Window, QColor("#E8F5E9"))
        palette_panel.setPalette(pal)
        pv = QVBoxLayout(palette_panel)
        pv.setSpacing(8)
        title = QLabel("元件箱")
        title.setAlignment(Qt.AlignCenter)
        pv.addWidget(title)
        self._sym_mode = QCheckBox("符号模式（小图标）")
        self._sym_mode.toggled.connect(self._set_symbol_mode)
        pv.addWidget(self._sym_mode)
        self._type_buttons: dict = {}
        for t in ELEMENT_TYPES:
            btn = QPushButton(TYPE_NAME_CN[t])
            btn.setMinimumHeight(36)
            btn.clicked.connect(lambda _=False, ty=t: self._pick(ty))
            self._type_buttons[t] = btn
        self._palette_grid = QGridLayout()
        self._palette_grid.setSpacing(4)
        pv.addLayout(self._palette_grid)
        self._apply_palette_mode(False)   # 默认文字模式（构建网格）
        wire_btn = QPushButton("导线")
        wire_btn.setMinimumHeight(36)
        wire_btn.clicked.connect(lambda: self._pick("wire"))
        pv.addWidget(wire_btn)
        select_btn = QPushButton("选择/移动")
        select_btn.setMinimumHeight(36)
        select_btn.clicked.connect(self._pick_select)
        pv.addWidget(select_btn)
        clear_btn = QPushButton("清空画布")
        clear_btn.setMinimumHeight(32)
        clear_btn.clicked.connect(self._clear)
        pv.addWidget(clear_btn)
        undo_btn = QPushButton("撤销 (Ctrl+Z)")
        undo_btn.setMinimumHeight(32)
        undo_btn.clicked.connect(self._undo)
        pv.addWidget(undo_btn)
        graph_btn = QPushButton("图像分析")
        graph_btn.setMinimumHeight(32)
        graph_btn.clicked.connect(self._show_graph)
        pv.addWidget(graph_btn)
        # 初高中模式已移至菜单「编辑」（用户裁定：设置栏调整）
        pv.addStretch(1)
        self._select_btn = select_btn

        # -- 缩放滑轨（V1.9：画布右下角悬浮覆盖显示） --
        zoom_bar = QWidget()
        zoom_bar.setAttribute(Qt.WA_StyledBackground, True)
        zoom_bar.setStyleSheet(
            "background:rgba(255,255,255,215);"
            "border:1px solid #9CC5A5;border-radius:8px;")
        zl = QHBoxLayout(zoom_bar)
        zl.setContentsMargins(8, 3, 8, 3)
        zl.setSpacing(4)
        btn_out = QPushButton("－")
        btn_out.setFixedSize(26, 24)
        btn_out.clicked.connect(lambda: self.view.zoom_by(1 / 1.25))
        btn_in = QPushButton("＋")
        btn_in.setFixedSize(26, 24)
        btn_in.clicked.connect(lambda: self.view.zoom_by(1.25))
        self._zoom_slider = QSlider(Qt.Horizontal)
        self._zoom_slider.setRange(self.view.ZOOM_MIN, self.view.ZOOM_MAX)
        self._zoom_slider.setValue(100)
        self._zoom_slider.setFixedWidth(130)
        self._zoom_slider.valueChanged.connect(
            lambda v: self.view.zoom_to(v))
        btn_fit = QPushButton("100%")
        btn_fit.setFixedWidth(44)
        btn_fit.setFixedHeight(24)
        btn_fit.clicked.connect(lambda: self.view.zoom_to(100))
        for wdg in (btn_out, self._zoom_slider, btn_in, btn_fit):
            zl.addWidget(wdg)
        self.view.set_overlay(zoom_bar)

        # -- 右侧参数调整栏（V1.9：勾选参数项 → 滑条持续显示，实时联动求解） --
        self._param_panel = QFrame()
        self._param_panel.setFixedWidth(230)
        self._param_panel.setAutoFillBackground(True)
        ppal = self._param_panel.palette()
        ppal.setColor(QPalette.Window, QColor("#E8F5E9"))
        self._param_panel.setPalette(ppal)
        ppv = QVBoxLayout(self._param_panel)
        ppv.setSpacing(6)
        pt = QLabel("参数调整栏")
        pt.setAlignment(Qt.AlignCenter)
        ppv.addWidget(pt)
        ppv.addWidget(QLabel("勾选后滑条固定显示于右侧，\n拖动即改元件并实时重算："))
        pscroll = QScrollArea()
        pscroll.setWidgetResizable(True)
        pscroll.setFrameShape(QFrame.NoFrame)
        phost = QWidget()
        phost.setStyleSheet("background:transparent;")
        self._param_v = QVBoxLayout(phost)
        self._param_v.setSpacing(2)
        self._param_v.addStretch(1)
        pscroll.setWidget(phost)
        ppv.addWidget(pscroll, 1)
        self._param_rows = {}     # (eid, param) -> row 控件字典
        self._param_pinned = set()
        self._param_dragging = None

        central = QWidget()
        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(palette_panel)
        layout.addWidget(self.view, 1)
        layout.addWidget(self._param_panel)
        self.setCentralWidget(body)
        self.view.zoom_changed.connect(self._on_view_zoom)

        # -- Delete 键删除选中（Ctrl+Z 撤销见菜单「编辑」） --
        from PyQt5.QtGui import QKeySequence
        from PyQt5.QtWidgets import QShortcut
        sc_del = QShortcut(QKeySequence(Qt.Key_Delete), self.view)
        sc_del.setContext(Qt.WidgetWithChildrenShortcut)
        sc_del.activated.connect(self._delete_selected)

        # -- 状态栏 --
        self.status = QLabel("从元件箱选择元件，点击画布放置；选「导线」后"
                             "点击两个格点连线；单击开关可闭合/断开。")
        self.status.setStyleSheet("color:#2d2d2d; padding:4px;")
        self.statusBar().addWidget(self.status, 1)

        # -- 防抖求解 --
        self._solve_timer = QTimer(self)
        self._solve_timer.setSingleShot(True)
        self._solve_timer.setInterval(150)
        self._solve_timer.timeout.connect(self._solve)
        self.scene.circuit_changed_signal.connect(self._on_changed)
        self.scene.circuit_changed_signal.connect(self._refresh_param_panel)
        self._refresh_param_panel()
        self._solve()

    # -- 菜单栏（P5 应用外壳 + V1.5 编辑栏） --
    def _build_menus(self):
        from PyQt5.QtGui import QKeySequence
        from PyQt5.QtWidgets import QAction, QShortcut
        mb = self.menuBar()

        m_file = mb.addMenu("文件(&F)")
        act_import = QAction("导入习题(&I)...", self)
        act_import.setEnabled(False)          # 二阶段开放：菜单置灰
        act_import.setToolTip("二阶段开放")
        act_import.setStatusTip("二阶段开放")
        act_import.setWhatsThis("二阶段开放")
        m_file.addAction(act_import)
        act_png = QAction("导出电路图(&X)...", self)
        act_png.triggered.connect(self._export_png)
        m_file.addAction(act_png)
        m_file.addSeparator()
        act_quit = QAction("退出(&Q)", self)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        # 编辑栏：元件图像编辑器 + 初高中模式（用户裁定：模式在设置栏调整）
        m_edit = mb.addMenu("编辑(&E)")
        act_sym = QAction("元件图像编辑器(&S)...", self)
        act_sym.triggered.connect(self._open_symbol_editor)
        m_edit.addAction(act_sym)
        m_edit.addSeparator()
        self.act_junior = QAction("初中模式(&J)", self)
        self.act_junior.setCheckable(True)
        self.act_junior.setChecked(True)
        self.act_junior.triggered.connect(lambda: self._set_mode("junior"))
        self.act_senior = QAction("高中模式(&G)", self)
        self.act_senior.setCheckable(True)
        self.act_senior.triggered.connect(lambda: self._set_mode("senior"))
        m_edit.addAction(self.act_junior)
        m_edit.addAction(self.act_senior)
        m_edit.addSeparator()
        act_undo = QAction("撤销(&U)", self)
        act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        act_undo.triggered.connect(self._undo)
        m_edit.addAction(act_undo)

        m_help = mb.addMenu("帮助(&H)")
        act_about = QAction("关于 VoltLab(&A)", self)
        act_about.triggered.connect(self._show_about)
        m_help.addAction(act_about)
        m_help.addSeparator()
        act_guide = QAction("使用说明(&G)", self)
        act_guide.triggered.connect(self._show_guide)
        m_help.addAction(act_guide)

    def _open_symbol_editor(self):
        from ui.symbol_editor import SymbolEditorDialog
        dlg = SymbolEditorDialog(self)
        dlg.exec_()

    def _set_mode(self, mode):
        if self.mode == mode:
            return
        self.mode = mode
        self.act_junior.setChecked(mode == "junior")
        self.act_senior.setChecked(mode == "senior")
        self.status.setText("已切换到%s（高中模式可双击电池设置内阻）。"
                            % ("初中模式" if mode == "junior"
                               else "高中模式"))
        self._solve()

    def _show_about(self):
        """Help > About：必须包含开源声明（需求书 6.2）。"""
        QMessageBox.about(self, "关于 VoltLab", ABOUT_TEXT)

    def _show_guide(self):
        QMessageBox.information(
            self, "使用说明",
            "1. 从左侧元件箱点选元件，点击画布放置（吸附网格）；放到导线上会"
            "自动串入。\n"
            "2. 选「导线」，点击两个格点连线（8 个方向，靠近引脚自动吸附）。\n"
            "3. 单击开关/双掷/三掷切换；双击电池、电阻、灯泡、变阻器、LED "
            "可设置参数。\n"
            "4. 拖动元件移动（导线自动跟随）；右键元件或导线删除；"
            "点选后按 Delete 也可删除；Ctrl+Z 撤销。\n"
            "5. 灯泡/LED 亮度随电流变化（按额定电流）；悬空元件显示灰色。\n"
            "6. 放置「变阻器」后可用「图像分析」绘制滑片位置扫描曲线。\n"
            "7. 顶部菜单可切换初中/高中模式（高中可设电源内阻）。\n"
            "8. 画布右下角缩放滑轨可放大/缩小（30%~400%），画布内按住 "
            "Ctrl+滚轮缩放；元件箱「符号模式」切换为 Word 式小图标。\n"
            "9. 右侧「参数调整栏」勾选参数项后滑条固定显示，拖动即改元件"
            "并实时重算；「文件 > 导出电路图」可存白底 PNG。")

    def _show_notice(self, text: str):
        self.status.setText(text)
        self.status.setStyleSheet("color:#c0392b; padding:4px;")

    # -- 工具切换 --
    def _set_symbol_mode(self, on: bool):
        """元件箱符号模式（V1.9）：开=Word 式小图标（无文字），关=文字按钮。"""
        self._apply_palette_mode(on)

    def _apply_palette_mode(self, on: bool):
        g = self._palette_grid
        for i in reversed(range(g.count())):
            item = g.takeAt(i)
            w = item.widget()
            if w is not None:
                g.removeWidget(w)
        for i, (t, btn) in enumerate(self._type_buttons.items()):
            if on:
                btn.setIcon(QIcon(type_icon_pixmap(t)))
                btn.setIconSize(QSize(34, 34))
                btn.setText("")
                btn.setFixedSize(48, 48)
                btn.setToolTip("%s（%s）" % (TYPE_NAME_CN[t], t))
                g.addWidget(btn, i // 3, i % 3)
            else:
                btn.setIcon(QIcon())
                btn.setText(TYPE_NAME_CN[t])
                btn.setToolTip("")
                btn.setMinimumSize(0, 36)
                btn.setMaximumSize(16777215, 16777215)
                g.addWidget(btn, i, 0, 1, 3)

    # -- 右侧参数调整栏（V1.9） --
    def _param_specs(self):
        """画布元件 → 可调参数列表 [(key, label, lo, hi, step, fmt)]。"""
        specs = []
        for el in self.scene.elements:
            t, eid = el["type"], el["id"]
            if t == "battery":
                specs.append(((eid, "E"), "%s 电动势 E (V)" % eid,
                              0.1, 24.0, 0.1, lambda v: "%.1f V" % v))
            elif t == "rheostat":
                specs.append(((eid, "position"), "%s 滑片位置 (%s)" % (
                                  eid, TYPE_NAME_CN["rheostat"]),
                              0, 100, 1, lambda v: "%d %%" % v))
                specs.append(((eid, "r_max"), "%s 最大阻值 Rmax (Ω)" % eid,
                              1, 100, 1, lambda v: "%d Ω" % v))
            elif t in ("resistor", "bulb", "bell", "motor"):
                specs.append(((eid, "resistance"),
                              "%s 电阻 R (Ω)·%s" % (eid, TYPE_NAME_CN[t]),
                              0.5, 100.0, 0.5, lambda v: "%.1f Ω" % v))
        return specs

    def _refresh_param_panel(self):
        """电路变化时重建参数栏（键集合不变则只同步数值）。"""
        specs = self._param_specs()
        new_keys = [k for k, *_ in specs]
        if new_keys != list(self._param_rows.keys()):
            self._rebuild_param_rows(specs)
        self._param_sync_values()

    def _rebuild_param_rows(self, specs):
        # 清空旧行
        for row in self._param_rows.values():
            for w in (row["cb"], row["slider_row"]):
                w.setParent(None)
                w.deleteLater()
        self._param_rows = {}
        v = self._param_v
        stretch = v.itemAt(v.count() - 1)
        if stretch is not None:
            v.removeItem(stretch)
        for key, label, lo, hi, step, fmt in specs:
            cb = QCheckBox(label)
            cb.setChecked(key in self._param_pinned)
            slider_row = QWidget()
            sl = QHBoxLayout(slider_row)
            sl.setContentsMargins(18, 0, 0, 0)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(int(lo * 10), int(hi * 10))
            val = QLabel("")
            val.setMinimumWidth(52)
            sl.addWidget(slider, 1)
            sl.addWidget(val)
            cb.toggled.connect(
                lambda on, k=key: self._param_pin_toggled(k, on))
            slider.sliderPressed.connect(
                lambda k=key: self._param_drag_start(k))
            slider.sliderReleased.connect(
                lambda k=key: self._param_drag_end(k))
            slider.valueChanged.connect(
                lambda vv, k=key: self._param_moved(k, vv))
            v.addWidget(cb)
            v.addWidget(slider_row)
            slider_row.setVisible(key in self._param_pinned)
            self._param_rows[key] = {"cb": cb, "slider_row": slider_row,
                                     "slider": slider, "val": val,
                                     "spec": (lo, hi, step, fmt)}
        v.addStretch(1)

    def _param_pin_toggled(self, key, on):
        if on:
            self._param_pinned.add(key)
        else:
            self._param_pinned.discard(key)
        row = self._param_rows.get(key)
        if row:
            row["slider_row"].setVisible(on)

    def _param_drag_start(self, key):
        self._param_dragging = key
        self.scene.push_undo()   # 拖动前留一个撤销点

    def _param_drag_end(self, key):
        self._param_dragging = None

    def _param_moved(self, key, vv):
        row = self._param_rows.get(key)
        if row is None:
            return
        lo, hi, step, fmt = row["spec"]
        eid, param = key
        el = self.scene._find(eid)
        if el is None:
            return
        shown = vv / 10.0
        if param == "position":
            el["props"]["position"] = min(1.0, shown / 100.0)
            row["val"].setText("%d %%" % round(shown))
        elif param == "E":
            el["props"]["E"] = shown
            row["val"].setText("%.1f V" % shown)
        elif param == "r_max":
            el["props"]["r_max"] = shown
            row["val"].setText("%d Ω" % round(shown))
        else:
            el["props"]["resistance"] = shown
            row["val"].setText("%.1f Ω" % shown)
        if self._param_dragging != key:
            return
        self._solve_timer.start()   # 实时联动：拖动中防抖重算

    def _param_sync_values(self):
        """外部修改（双击对话框/撤销）后同步滑条显示。"""
        for key, row in self._param_rows.items():
            eid, param = key
            el = self.scene._find(eid)
            if el is None:
                continue
            if param == "position":
                v = round(float(el["props"].get("position", 0.5)) * 1000)
            elif param == "E":
                v = round(float(el["props"].get("E", 3.0)) * 10)
            elif param == "r_max":
                v = round(float(el["props"].get("r_max", 10.0)) * 10)
            else:
                v = round(float(el["props"].get("resistance", 10.0)) * 10)
            slider = row["slider"]
            if slider.value() != v:
                slider.blockSignals(True)
                slider.setValue(v)
                slider.blockSignals(False)
            row["slider"].valueChanged.emit(slider.value())  # 刷新文本

    # -- 导出电路图（V1.9：白底 PNG） --
    def _render_scene_png(self, path: str) -> bool:
        rect = self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        if rect.isEmpty():
            return False
        scale = 2.0
        img = QImage(int(rect.width() * scale), int(rect.height() * scale),
                     QImage.Format_ARGB32)
        img.fill(Qt.white)
        bg = self.scene.backgroundBrush()
        self.scene.setBackgroundBrush(QBrush(Qt.white))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        self.scene.render(p, QRectF(img.rect()), rect)
        p.end()
        self.scene.setBackgroundBrush(bg)
        return img.save(path)

    def _export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出电路图",
                                              "电路图.png",
                                              "PNG 图片 (*.png)")
        if not path:
            return
        if self._render_scene_png(path):
            self.status.setText("已导出电路图：%s" % path)
            self.status.setStyleSheet("color:#2d2d2d; padding:4px;")
        else:
            self._show_notice("导出失败：画布为空或文件不可写。")

    def _on_view_zoom(self, percent: int):
        if self._zoom_slider.value() != percent:
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setValue(percent)
            self._zoom_slider.blockSignals(False)

    def _delete_selected(self):
        n = self.scene.delete_selected()
        if n:
            self.status.setText("已删除 %d 个选中对象（Ctrl+Z 可撤销）。" % n)
            self.status.setStyleSheet("color:#2d2d2d; padding:4px;")

    def _pick(self, t):
        self.scene.set_tool(t if t == "wire" else t)
        self.status.setText("点击画布放置【%s】（自动吸附网格）。"
                            % TYPE_NAME_CN.get(t, t))
        self.status.setStyleSheet("color:#2d2d2d; padding:4px;")

    def _pick_select(self):
        self.scene.set_tool(None)
        self.status.setText("选择模式：拖动移动、右键删除、单击开关切换、"
                            "双击电池改参数。")
        self.status.setStyleSheet("color:#2d2d2d; padding:4px;")

    def _clear(self):
        self.scene.clear_all()

    def _undo(self):
        self.scene.undo()

    def _show_graph(self):
        dlg = GraphDialog(self, self.scene, self.mode)
        dlg.exec_()

    # -- 求解联动 --
    def _on_changed(self):
        self._solve_timer.start()

    def _solve(self):
        elements = self.scene.get_elements()
        res = run_with_watchdog(elements, mode=self.mode)
        states = calc_visual_state(res)
        if not res.get("ok"):
            self.scene.apply_states({}, res.get("error"))
            msg = ERR_TEXT.get(res.get("error"), "电路无法求解。")
            self.status.setText(msg)
            self.status.setStyleSheet("color:#c0392b; font-weight:bold;"
                                      " padding:4px;")
            log.warning("UI solve failed: %s", res.get("error"))
            return
        self.scene.apply_states(states.get("elements", {}), None)
        n = len(elements)
        self.status.setText("电路正常 ✔（%d 个元件，模式：%s）"
                            % (n, "初中" if self.mode == "junior" else "高中"))
        self.status.setStyleSheet("color:#2d2d2d; padding:4px;")


def main():
    import sys

    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
