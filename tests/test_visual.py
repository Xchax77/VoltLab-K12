# -*- coding: utf-8 -*-
"""P4 单元测试：状态判定 calc_visual_state + 8 向导线路径 + 场景冒烟。

运行方式（工作区根目录）::
    python tests/test_visual.py -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.solver import calc_visual_state, solve_circuit  # noqa: E402
from ui.circuit_scene import (  # noqa: E402
    TERMINAL_HALF,
    CircuitScene,
    GRID_SIZE,
    wire_path_points,
    _is_8dir,
)


def _series(closed=True):
    return [
        {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
         "props": {"E": 3.0}},
        {"id": "s1", "type": "switch", "nodes": ["n2", "n3"],
         "props": {"closed": closed}},
        {"id": "lamp1", "type": "bulb", "nodes": ["n3", "n4"],
         "props": {"resistance": 10.0}},
        {"id": "a1", "type": "ammeter", "nodes": ["n4", "n1"],
         "props": {}},
    ]


class TestCalcVisualState(unittest.TestCase):

    def test_bulb_on_when_closed(self):
        res = solve_circuit(_series(closed=True))
        st = calc_visual_state(res)
        self.assertTrue(st["ok"])
        self.assertEqual(st["elements"]["lamp1"]["state"], "on")
        self.assertFalse(st["elements"]["lamp1"]["floating"])
        self.assertIn("I", st["elements"]["a1"]["readings"])

    def test_bulb_off_when_open(self):
        res = solve_circuit(_series(closed=False))
        st = calc_visual_state(res)
        self.assertTrue(st["ok"])
        self.assertEqual(st["elements"]["lamp1"]["state"], "off")

    def test_short_error_passes_through(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "w1", "type": "wire", "nodes": ["n1", "n2"], "props": {}},
        ]
        st = calc_visual_state(solve_circuit(els))
        self.assertFalse(st["ok"])
        self.assertEqual(st["error"], "short")  # UI 红字提示依据

    def test_floating_marked(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0}},
            {"id": "r2", "type": "resistor", "nodes": ["n8", "n9"],
             "props": {"resistance": 5.0}},
        ]
        st = calc_visual_state(solve_circuit(els))
        self.assertTrue(st["ok"])
        self.assertFalse(st["elements"]["r1"]["floating"])
        self.assertTrue(st["elements"]["r2"]["floating"])  # UI 标灰


class TestWirePath(unittest.TestCase):
    """8 向导线路径：主轴/对角单段；其余自动 L 拆段且每段 8 向。"""

    def test_straight_single(self):
        pts, single = wire_path_points((0, 0), (5, 0))
        self.assertTrue(single)
        self.assertEqual(pts, [(0, 0), (5, 0)])

    def test_diagonal_single(self):
        pts, single = wire_path_points((0, 0), (3, 3))
        self.assertTrue(single)
        self.assertEqual(pts, [(0, 0), (3, 3)])

    def test_l_split_all_segments_8dir(self):
        pts, single = wire_path_points((0, 0), (3, 5))
        self.assertFalse(single)
        self.assertEqual(len(pts), 3)
        for a, b in zip(pts, pts[1:]):
            self.assertTrue(_is_8dir(a, b), (a, b))

    def test_same_point(self):
        pts, single = wire_path_points((2, 2), (2, 2))
        self.assertFalse(single)
        self.assertEqual(pts, [(2, 2)])


class TestSceneSmoke(unittest.TestCase):
    """场景冒烟：放置/连线/切换/删除/导出数据结构（无头环境）。"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_place_wire_toggle_remove(self):
        scene = CircuitScene()
        # 放置电池与灯泡（2×2 占位：引脚在中心 ±1 格）
        bid = scene.place_element("battery", 5, 5)   # 引脚 (4,5)(6,5)
        lid = scene.place_element("bulb", 10, 5)     # 引脚 (9,5)(11,5)
        els = scene.get_elements()
        self.assertEqual(len(els), 2)
        # 电池右引脚 (6,5) 与灯泡左引脚 (9,5)：距离 3 格水平 → 单段
        ids = scene.add_wire((6, 5), (9, 5))
        self.assertEqual(len(ids), 1)
        # 非 8 向：垂直差 5、水平差 4 → 拆 2 段
        ids2 = scene.add_wire((12, 7), (16, 12))
        self.assertEqual(len(ids2), 2)
        els = scene.get_elements()
        self.assertEqual(len(els), 5)  # 电池+灯泡+3 段导线
        types = {e["type"] for e in els}
        self.assertIn("wire", types)
        # 所有元件 2 节点、无重节点元件
        for e in els:
            self.assertEqual(len(e["nodes"]), 2)
            self.assertNotEqual(e["nodes"][0], e["nodes"][1])
        # 开关切换
        sid = scene.place_element("switch", 5, 10)
        el_switch = [e for e in scene.get_elements() if e["id"] == sid][0]
        self.assertFalse(el_switch["props"]["closed"])
        scene.toggle_switch(sid)
        el_switch = [e for e in scene.get_elements() if e["id"] == sid][0]
        self.assertTrue(el_switch["props"]["closed"])
        # 删除电池后节点引用回收（其孤立引脚节点应被移除）
        n_before = len(scene._node_at)
        scene.remove_element(bid)
        self.assertLess(len(scene._node_at), n_before)
        # 清空
        scene.clear_all()
        self.assertEqual(len(scene.get_elements()), 0)
        self.assertEqual(len(scene._node_at), 0)

    def test_grid_snap_constants(self):
        self.assertEqual(GRID_SIZE, 30)  # 网格常量
        self.assertEqual(TERMINAL_HALF, GRID_SIZE)  # 2×2 占位（用户裁定）

    def test_relocate_follows_wires(self):
        """拖动元件：导线端点跟随、节点注册迁移、占位表迁移（2×2 占位）。"""
        scene = CircuitScene()
        bid = scene.place_element("battery", 5, 5)   # 引脚 (4,5)(6,5)
        lid = scene.place_element("bulb", 10, 5)     # 引脚 (9,5)(11,5)
        scene.add_wire((6, 5), (9, 5))               # 电池右 → 灯泡左
        # 拖动电池到 (5, 10)
        scene.relocate_element(bid, (5, 5), (5, 10))
        el_b = [e for e in scene.get_elements() if e["id"] == bid][0]
        # 电池右引脚换了新节点（旧节点被导线端点共享，留在原地）
        self.assertNotEqual(el_b["nodes"][1], "n2")
        # 导线端点跟随：起点到 (6,10)，且与电池右引脚仍是同一节点（保持导通）
        w = [e for e in scene.get_elements() if e["type"] == "wire"][0]
        w_item = scene._items[w["id"]]
        self.assertEqual(w_item.waypoints[0], (6, 10))
        self.assertEqual(w_item.waypoints[1], (9, 5))
        self.assertEqual(w["nodes"][0], el_b["nodes"][1])
        # 占位表已迁移：旧中心空出，新中心被占
        self.assertNotIn((5, 5), scene._cells)
        self.assertIn((bid, "c"), scene._cells.get((5, 10), []))

    def test_collision_rejected(self):
        scene = CircuitScene()
        scene.place_element("battery", 5, 5)  # 占 (5,5) 中心 + (4,5)(6,5)
        got = []
        scene.notice_signal.connect(got.append)
        rid = scene.place_element("resistor", 5, 5)  # 中心重叠 → 拒
        self.assertEqual(rid, "")
        self.assertEqual(len(got), 1)
        self.assertEqual(len(scene.get_elements()), 1)

    def test_adjacent_placement_allowed(self):
        """引脚-引脚相接（间隔 2 格放置）= 串联，允许。"""
        scene = CircuitScene()
        r1 = scene.place_element("battery", 5, 5)    # 右引脚 (6,5)
        r2 = scene.place_element("resistor", 7, 5)   # 左引脚 (6,5) 相接
        self.assertTrue(r1)
        self.assertTrue(r2)
        el_r = [e for e in scene.elements if e["id"] == r2][0]
        el_b = [e for e in scene.elements if e["id"] == r1][0]
        self.assertEqual(el_b["nodes"][1], el_r["nodes"][0])  # 同节点导通

    def test_snap_to_terminal(self):
        scene = CircuitScene()
        scene.place_element("battery", 5, 5)  # 引脚 (4,5)(6,5)
        # 点击点 (4,6) 距电池左引脚 (4,5) 切比雪夫距离 1 → 吸附
        self.assertEqual(scene._snap_to_terminal((4, 6)), (4, 5))
        # 远处不吸附
        self.assertEqual(scene._snap_to_terminal((20, 20)), (20, 20))


if __name__ == "__main__":
    unittest.main(verbosity=2)
