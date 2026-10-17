# -*- coding: utf-8 -*-
"""V1.5 新功能测试：电铃/马达、二极管/LED、滑动变阻器、单刀双掷、
插入替换导线、撤销、图像窗口。

运行方式（工作区根目录）::
    python tests/test_features.py -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.solver import calc_visual_state, solve_circuit  # noqa: E402
from ui.circuit_scene import CircuitScene  # noqa: E402


class TestBellMotor(unittest.TestCase):

    def test_bell_resistive(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "bell1", "type": "bell", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"], res)
        self.assertAlmostEqual(res["element_results"]["bell1"]["current"],
                               0.3, places=9)
        st = calc_visual_state(res)
        self.assertEqual(st["elements"]["bell1"]["state"], "on")

    def test_motor_off_without_current(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "m1", "type": "motor", "nodes": ["n2", "n3"],
             "props": {"resistance": 10.0}},
        ]
        res = solve_circuit(els)
        st = calc_visual_state(res)
        self.assertEqual(st["elements"]["m1"]["state"], "off")  # 断路


class TestDiodes(unittest.TestCase):

    def _circuit(self, reverse=False):
        return [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "d1", "type": "diode",
             "nodes": ["n2", "n1"] if reverse else ["n1", "n2"],
             "props": {"vf": 0.7, "r_on": 1.0}},
        ]

    def test_diode_forward_conducts(self):
        res = solve_circuit(self._circuit())
        self.assertTrue(res["ok"], res)
        er = res["element_results"]["d1"]
        self.assertTrue(er["diode_on"])
        self.assertAlmostEqual(er["current"], (3.0 - 0.7) / 1.0, places=6)

    def test_diode_reverse_blocks(self):
        res = solve_circuit(self._circuit(reverse=True))
        self.assertTrue(res["ok"], res)
        er = res["element_results"]["d1"]
        self.assertFalse(er["diode_on"])
        self.assertAlmostEqual(er["current"], 0.0, places=9)

    def test_led_brightness_by_current(self):
        """电流越小越暗（亮度 = |I|/额定电流，封顶 1）。"""
        def brightness(e_v, r_series):
            els = [
                {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
                 "props": {"E": e_v}},
                {"id": "r1", "type": "resistor", "nodes": ["n1", "n3"],
                 "props": {"resistance": r_series}},
                {"id": "led1", "type": "led", "nodes": ["n3", "n2"],
                 "props": {"vf": 2.0, "r_on": 1.0, "rated_current": 0.02}},
            ]
            res = solve_circuit(els)
            self.assertTrue(res["ok"], res)
            return res["element_results"]["led1"]["brightness"]

        bright_low = brightness(2.05, 1000.0)   # I≈0.05mA → 很暗
        bright_high = brightness(3.0, 30.0)     # I≈32mA → 满亮（封顶）
        self.assertLess(bright_low, 0.5)
        self.assertAlmostEqual(bright_high, 1.0, places=6)


class TestRheostat(unittest.TestCase):

    def _circuit(self, pos):
        return [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "rh1", "type": "rheostat", "nodes": ["n1", "n2", "nw"],
             "props": {"r_max": 10.0, "position": pos}},
        ]

    def test_total_resistance_independent_of_slider(self):
        """滑片悬空时 A-B 总阻值恒为 r_max。"""
        for pos in (0.0, 0.3, 0.8):
            res = solve_circuit(self._circuit(pos))
            self.assertTrue(res["ok"], res)
            self.assertAlmostEqual(
                abs(res["element_results"]["rh1"]["current"]), 0.3, places=6)

    def test_slider_as_potential_divider(self):
        """W 接入电路（接负载）：位置改变输出。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "rh1", "type": "rheostat", "nodes": ["n1", "n2", "nw"],
             "props": {"r_max": 10.0, "position": 0.5}},
            {"id": "lamp1", "type": "bulb", "nodes": ["nw", "n2"],
             "props": {"resistance": 10.0}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"], res)
        v_pos50 = res["element_results"]["lamp1"]["voltage"]
        # pos=0.5：上半段 5Ω 与灯 10Ω 分压 → V < 3
        self.assertLess(v_pos50, 3.0)
        self.assertGreater(v_pos50, 1.0)


class TestSpdt(unittest.TestCase):

    def _circuit(self, pos):
        return [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "lamp1", "type": "bulb", "nodes": ["n1", "n3"],
             "props": {"resistance": 10.0}},
            {"id": "lamp2", "type": "bulb", "nodes": ["n1", "n4"],
             "props": {"resistance": 10.0}},
            {"id": "sw2", "type": "spdt", "nodes": ["n2", "n3", "n4"],
             "props": {"position": pos}},
        ]

    def test_throw_selects_branch(self):
        res = solve_circuit(self._circuit(0))
        st = calc_visual_state(res)
        self.assertEqual(st["elements"]["lamp1"]["state"], "on")
        self.assertEqual(st["elements"]["lamp2"]["state"], "off")
        res = solve_circuit(self._circuit(1))
        st = calc_visual_state(res)
        self.assertEqual(st["elements"]["lamp1"]["state"], "off")
        self.assertEqual(st["elements"]["lamp2"]["state"], "on")


class TestSceneFeatures(unittest.TestCase):

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def _loop_scene(self):
        scene = CircuitScene()
        scene.place_element("battery", 5, 5)    # 引脚 (4,5)(6,5)
        scene.place_element("bulb", 13, 5)      # 引脚 (12,5)(14,5)
        scene.add_wire((6, 5), (12, 5))
        scene.add_wire((14, 5), (20, 5))
        scene.add_wire((20, 5), (20, 15))
        scene.add_wire((20, 15), (4, 15))
        scene.add_wire((4, 15), (4, 5))
        return scene

    def test_insert_replaces_wire(self):
        """在导线上放置电阻 → 自动切断导线串入，电路仍导通。"""
        scene = self._loop_scene()
        n_before = len(scene.elements)
        wires_before = sum(1 for e in scene.elements if e["type"] == "wire")
        rid = scene.place_element("resistor", 9, 5)  # 落在导线 (6,5)-(12,5) 上
        self.assertTrue(rid)
        wires_after = sum(1 for e in scene.elements if e["type"] == "wire")
        # 拆 1 段、生成 2 段短导线（引脚 ±1 格，不在原导线端点）
        self.assertEqual(wires_after, wires_before + 1)
        # 元件 +1（电阻）、导线净 +1（拆 1 生 2）→ 总数 n_before + 2
        self.assertEqual(len(scene.elements), n_before + 2)
        # 电路仍导通：灯泡有电流
        res = solve_circuit(scene.get_elements())
        self.assertTrue(res["ok"], res)
        er = [v for k, v in res["element_results"].items()
              if k.startswith("lamp")][0]
        self.assertGreater(abs(er["current"]), 0.01)

    def test_undo_place_and_wire(self):
        scene = self._loop_scene()
        n1 = len(scene.elements)
        scene.place_element("resistor", 9, 8)
        self.assertEqual(len(scene.elements), n1 + 1)
        scene.undo()
        self.assertEqual(len(scene.elements), n1)
        scene.undo()
        self.assertEqual(len(scene.elements), n1 - 1)  # 撤销最后一段导线

    def test_undo_relocate_restores(self):
        scene = CircuitScene()
        bid = scene.place_element("battery", 5, 5)
        scene.relocate_element(bid, (5, 5), (5, 10))
        el = [e for e in scene.elements if e["id"] == bid][0]
        self.assertEqual(scene._geometry[bid]["center"], (5, 10))
        scene.undo()
        self.assertEqual(scene._geometry[bid]["center"], (5, 5))

    def test_delete_wire(self):
        scene = self._loop_scene()
        wires = [e["id"] for e in scene.elements if e["type"] == "wire"]
        n = len(scene.elements)
        scene.remove_element(wires[0])
        self.assertEqual(len(scene.elements), n - 1)
        scene.undo()
        self.assertEqual(len(scene.elements), n)

    def test_graph_dialog_series(self):
        """变阻器串入回路的 1% 步进映射表扫描。

        回归（V1.8）：旧实现改了 get_elements() 深拷贝的 position，
        各点全用同一位置求解 → 曲线恒为平线（用户反馈"没有正常图像"）；
        且 0 阻值段 stamp g=1e12 曾致端点位置矩阵奇异（误报 short）。
        """
        scene = CircuitScene()
        scene.place_element("battery", 5, 5)
        scene.place_element("rheostat", 9, 5)
        scene.place_element("bulb", 13, 5)
        scene.add_wire((6, 5), (8, 5))
        scene.add_wire((10, 5), (12, 5))
        scene.add_wire((14, 5), (20, 5))
        scene.add_wire((20, 5), (20, 15))
        scene.add_wire((20, 15), (4, 15))
        scene.add_wire((4, 15), (4, 5))
        from ui.graph_dialog import GraphDialog
        dlg = GraphDialog(None, scene, "junior")
        self.assertFalse(getattr(dlg, "_no_rheostat", True))
        self.assertTrue(len(dlg.plot.series) >= 1)
        for _label, vals, _c in dlg.plot.series:
            self.assertEqual(len(vals), 101)   # 1% 步进映射表，端点不缺失
        # 曲线必须随滑片位置变化（非平线）
        main_vals = dlg.plot.series[0][1]
        self.assertGreater(max(main_vals) - min(main_vals), 1e-6)
        # 扫描不得修改画布上变阻器的实际位置
        rheo = [e for e in scene.elements if e["type"] == "rheostat"][0]
        self.assertAlmostEqual(rheo["props"].get("position", 0.5), 0.5)


class TestV17UiFeatures(unittest.TestCase):
    """V1.7：Delete 删除选中、缩放 API、元件箱符号图标。"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        # 必须持有引用：临时 QApplication 被 GC 销毁会导致后续
        # QGraphicsScene 初始化访问违例（0xC0000005）
        self.app = QApplication.instance() or QApplication([])

    def _scene(self):
        return CircuitScene()

    def test_delete_selected(self):
        s = self._scene()
        s.place_element("battery", 5, 5)
        s.place_element("motor", 9, 5)
        s.add_wire((6, 5), (8, 5))
        n_before = len(s.elements)
        s._items["r1"].setSelected(False) if "r1" in s._items else None
        bat_id = [e["id"] for e in s.elements
                  if e["type"] == "battery"][0]
        motor_id = [e["id"] for e in s.elements
                    if e["type"] == "motor"][0]
        s._items[bat_id].setSelected(True)
        s._items[motor_id].setSelected(True)
        n = s.delete_selected()
        self.assertEqual(n, 2)
        self.assertEqual(len(s.elements), n_before - 2)
        # 撤销恢复
        s.undo()
        self.assertEqual(len(s.elements), n_before)

    def test_delete_selected_empty(self):
        s = self._scene()
        self.assertEqual(s.delete_selected(), 0)

    def test_type_icon_pixmap(self):
        from ui.circuit_scene import type_icon_pixmap
        from ui.circuit_scene import ELEMENT_TYPES
        for t in ELEMENT_TYPES:
            pm = type_icon_pixmap(t)
            self.assertFalse(pm.isNull(), t)
            self.assertEqual(pm.width(), pm.height(), t)
        # 缓存命中：两次同一对象
        self.assertIs(type_icon_pixmap("battery"),
                      type_icon_pixmap("battery"))

    def test_render_all_element_types(self):
        """回归：所有元件类型真实渲染不崩溃（V1.7 修复 spdt paint 崩溃）。"""
        from ui.circuit_scene import ELEMENT_TYPES, GridGraphicsView
        s = self._scene()
        x = 3
        for t in ELEMENT_TYPES:
            s.place_element(t, x, 5)
            x += 3
        s.place_element("sp3t", x, 5)
        v = GridGraphicsView(s)
        v.resize(800, 200)
        pm = v.grab()
        self.assertFalse(pm.isNull())

    def test_view_zoom_api(self):
        from ui.circuit_scene import GridGraphicsView
        from ui.circuit_scene import CircuitScene as CS
        s = self._scene()
        v = GridGraphicsView(CS())
        self.assertEqual(v.current_zoom(), 100)
        v.zoom_to(200)
        self.assertEqual(v.current_zoom(), 200)
        v.zoom_by(1.25)
        self.assertEqual(v.current_zoom(), 250)
        v.zoom_to(99999)   # 夹紧上限
        self.assertEqual(v.current_zoom(), GridGraphicsView.ZOOM_MAX)
        v.zoom_to(1)       # 夹紧下限
        self.assertEqual(v.current_zoom(), GridGraphicsView.ZOOM_MIN)
        got = []
        v.zoom_changed.connect(got.append)
        v.zoom_to(150)
        self.assertEqual(got[-1], 150)

    def test_symbol_editor_ghost(self):
        """编辑器画布必须显示内置符号底图（用户反馈"看不到图像"）。"""
        from ui.symbol_editor import SymbolEditorDialog
        dlg = SymbolEditorDialog(None)
        self.assertFalse(dlg.canvas._ghost.isNull())
        # 切换类型底图跟随更新
        dlg.type_box.setCurrentIndex(2)
        self.assertFalse(dlg.canvas._ghost.isNull())


class TestV19Features(unittest.TestCase):
    """V1.9：导出白底 PNG、右侧参数调整栏、缩放滑轨悬浮、符号小图标。"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def _win(self):
        import ui.main_window as mw
        return mw.MainWindow()

    def test_export_png_white_bg(self):
        import os
        import tempfile
        win = self._win()
        s = win.scene
        s.place_element("battery", 5, 5)
        s.place_element("bulb", 9, 5)
        s.add_wire((6, 5), (8, 5))
        s.add_wire((10, 5), (12, 5))
        path = os.path.join(tempfile.gettempdir(),
                            "_voltlab_export_test.png")
        try:
            self.assertTrue(win._render_scene_png(path))
            from PyQt5.QtGui import QImage
            img = QImage(path)
            self.assertFalse(img.isNull())
            c = img.pixelColor(2, 2)
            self.assertEqual((c.red(), c.green(), c.blue()), (255, 255, 255))
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_param_panel_slider_updates_scene(self):
        win = self._win()
        s = win.scene
        s.place_element("battery", 5, 5)
        s.place_element("rheostat", 9, 5)
        s.place_element("bulb", 13, 5)
        win._refresh_param_panel()
        rid = [e["id"] for e in s.elements if e["type"] == "rheostat"][0]
        key = (rid, "position")
        self.assertIn(key, win._param_rows)
        # 拖动滑条 → 场景元件属性实时更新
        win._param_rows[key]["slider"].setValue(300)   # 30%
        rheo = [e for e in s.elements if e["type"] == "rheostat"][0]
        self.assertAlmostEqual(rheo["props"]["position"], 0.3)
        # 拖动前压了撤销点 → undo 恢复
        s.undo()
        rheo = [e for e in s.elements if e["type"] == "rheostat"][0]
        self.assertAlmostEqual(rheo["props"]["position"], 0.5)
        # 勾选固定 → 滑条行显示
        win._param_rows[key]["cb"].setChecked(True)
        self.assertTrue(win._param_rows[key]["slider_row"]
                        .isVisibleTo(win._param_panel))
        # 电路变化（删元件）→ 参数栏行跟随移除
        s.remove_element(rid)
        self.assertNotIn(key, win._param_rows)

    def test_zoom_overlay(self):
        win = self._win()
        self.assertIsNotNone(win.view._overlay)
        self.assertIs(win.view._overlay.parent(), win.view.viewport())

    def test_palette_icon_mode(self):
        win = self._win()
        win._sym_mode.setChecked(True)
        btn = win._type_buttons["battery"]
        self.assertEqual(btn.text(), "")          # 图标模式无中文注释
        self.assertFalse(btn.icon().isNull())
        win._sym_mode.setChecked(False)
        self.assertEqual(btn.text(), "电池")
        self.assertTrue(btn.icon().isNull())


class TestV110Features(unittest.TestCase):
    """V1.10：编辑器大画布/线宽可调、多参数元件合并单窗口。"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_editor_canvas_enlarged(self):
        import ui.symbol_editor as se
        self.assertEqual((se.CANVAS_W, se.CANVAS_H), (640, 480))
        self.assertEqual(se.PEN_WIDTH_MAX, 16)

    def test_battery_two_params_one_dialog(self):
        """电池 E + 内阻在同一个参数窗口（用户裁定）。"""
        from ui.circuit_scene import CircuitScene
        s = CircuitScene()
        bid = s.place_element("battery", 5, 5)
        el = s._find(bid)
        dlg = s._make_props_dialog(el)
        self.assertIsNotNone(dlg)
        self.assertIn("E", dlg._spins)
        self.assertIn("r_internal", dlg._spins)
        dlg._spins["E"].setValue(6.0)
        dlg._spins["r_internal"].setValue(0.5)
        s._apply_props_dialog(el, dlg)
        self.assertAlmostEqual(el["props"]["E"], 6.0)
        self.assertAlmostEqual(el["props"]["r_internal"], 0.5)
        s.undo()
        el2 = s._find(bid)
        self.assertAlmostEqual(el2["props"]["E"], 3.0)
        self.assertAlmostEqual(el2["props"]["r_internal"], 0.0)

    def test_bulb_three_params_one_dialog(self):
        from ui.circuit_scene import CircuitScene
        s = CircuitScene()
        lid = s.place_element("bulb", 9, 5)
        el = s._find(lid)
        dlg = s._make_props_dialog(el)
        self.assertEqual(len(dlg._spins), 3)
        dlg._spins["resistance"].setValue(20.0)
        dlg._spins["rated_voltage"].setValue(3.8)
        dlg._spins["rated_current"].setValue(0.5)
        s._apply_props_dialog(el, dlg)
        self.assertAlmostEqual(el["props"]["resistance"], 20.0)
        self.assertAlmostEqual(el["props"]["rated_voltage"], 3.8)
        self.assertAlmostEqual(el["props"]["rated_current"], 0.5)

    def test_rheostat_dialog_position_percent(self):
        from ui.circuit_scene import CircuitScene
        s = CircuitScene()
        rid = s.place_element("rheostat", 9, 5)
        el = s._find(rid)
        dlg = s._make_props_dialog(el)
        dlg._spins["r_max"].setValue(50.0)
        dlg._spins["position"].setValue(25.0)
        s._apply_props_dialog(el, dlg)
        self.assertAlmostEqual(el["props"]["r_max"], 50.0)
        self.assertAlmostEqual(el["props"]["position"], 0.25)


class TestV111Features(unittest.TestCase):
    """V1.11：编辑器画布缩放滑条（显示缩放不影响数据坐标）。"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_canvas_zoom_api(self):
        from ui.symbol_editor import SymbolCanvas
        c = SymbolCanvas()
        self.assertEqual(c.zoom, 1.0)
        c.set_zoom(200)
        self.assertEqual(c.zoom, 2.0)
        c.set_zoom(99999)          # 夹紧 400%
        self.assertEqual(c.zoom, 4.0)
        c.set_zoom(1)              # 夹紧 50%
        self.assertEqual(c.zoom, 0.5)
        c.zoom_by(2.0)
        self.assertEqual(c.zoom, 1.0)

    def test_to_local_respects_zoom(self):
        from ui.symbol_editor import SymbolCanvas
        from PyQt5.QtCore import QPoint
        c = SymbolCanvas()
        c.set_zoom(200)
        lx, _ = c._to_local(QPoint(320 + 100, 240))
        self.assertAlmostEqual(lx, 50.0)   # 屏幕右移 100px = 局部 50
        c.set_zoom(100)
        lx, _ = c._to_local(QPoint(320 + 100, 240))
        self.assertAlmostEqual(lx, 100.0)

    def test_dialog_zoom_slider(self):
        from ui.symbol_editor import SymbolEditorDialog
        dlg = SymbolEditorDialog(None)
        self.assertEqual(dlg._zoom_slider.minimum(), 50)
        self.assertEqual(dlg._zoom_slider.maximum(), 400)
        dlg._zoom_slider.setValue(250)
        self.assertAlmostEqual(dlg.canvas.zoom, 2.5)


class TestV113WireFeatures(unittest.TestCase):
    """V1.13：导线连接空白节点（中段拆分 T 接）、导线可选中删除。"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def _scene(self):
        return CircuitScene()

    def test_wire_chain_through_blank_nodes(self):
        """导线端点落在空白格点 → 注册新节点，逐段相连仍然导通。"""
        s = self._scene()
        s.place_element("battery", 5, 5)
        s.place_element("bulb", 11, 5)
        # 电池正极 → 空白点(7,5) → 空白点(8,5) → 灯泡（三段链）
        s.add_wire((6, 5), (7, 5))
        s.add_wire((7, 5), (8, 5))
        s.add_wire((8, 5), (10, 5))
        s.add_wire((12, 5), (20, 5))
        s.add_wire((20, 5), (20, 15))
        s.add_wire((20, 15), (4, 15))
        s.add_wire((4, 15), (4, 5))
        res = solve_circuit(s.get_elements())
        self.assertTrue(res["ok"], res)
        lamp = [e["id"] for e in s.elements if e["type"] == "bulb"][0]
        self.assertGreater(abs(res["element_results"][lamp]["current"]), 0.1)

    def test_wire_mid_split_t_connection(self):
        """点击导线中段 → 拆分出新节点，T 形分支与主干电学连通。"""
        from PyQt5.QtCore import QPoint
        s = self._scene()
        s.place_element("battery", 3, 5)
        s.place_element("bulb", 17, 5)
        s.add_wire((4, 5), (6, 5))
        s.add_wire((6, 5), (12, 5))       # 主干（将被中段拆分）
        s.add_wire((12, 5), (16, 5))
        s.add_wire((18, 5), (20, 5))
        s.add_wire((20, 5), (20, 15))
        s.add_wire((20, 15), (2, 15))
        s.add_wire((2, 15), (2, 5))
        # 模拟导线工具：第一击 (9,5)（主干中段）→ 拆分；第二击 (9,12)
        # （注意 (9,15) 落在回流导线上会被误拆，选空白点 (9,12)）
        s._handle_wire_click((9, 5))
        s._handle_wire_click((9, 12))
        wires = [e for e in s.elements if e["type"] == "wire"]
        self.assertEqual(len(wires), 9)   # 原 7 段 → 拆分 +1、分支 +1
        # (9,5) 成为三线交汇节点
        nid = s._node_at.get((9, 5))
        self.assertIsNotNone(nid)
        self.assertEqual(s._node_refs[nid], 3)
        # 主干仍导通，分支悬空（暗电流已归零不影响）
        res = solve_circuit(s.get_elements())
        self.assertTrue(res["ok"], res)
        lamp = [e["id"] for e in s.elements if e["type"] == "bulb"][0]
        self.assertGreater(abs(res["element_results"][lamp]["current"]), 0.1)

    def test_wire_select_and_delete(self):
        """导线可选中（Delete 删除），撤销可恢复。"""
        from PyQt5.QtWidgets import QGraphicsItem
        s = self._scene()
        s.add_wire((5, 5), (10, 5))
        wid = [e["id"] for e in s.elements if e["type"] == "wire"][0]
        item = s._items[wid]
        self.assertTrue(item.flags() & QGraphicsItem.ItemIsSelectable)
        item.setSelected(True)
        self.assertEqual(s.delete_selected(), 1)
        self.assertEqual(len(s.elements), 0)
        s.undo()
        self.assertEqual(len(s.elements), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
