# -*- coding: utf-8 -*-
"""P3 单元测试：高中扩展（内阻/电表实内阻/路端电压断言）+ 看门狗 + 日志。

运行方式（工作区根目录）::
    python tests/test_solver.py -v
"""

from __future__ import annotations

import json
import logging
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # 看门狗测试无头运行

from core import solver  # noqa: E402
from core.solver import check_terminal_voltage, solve_circuit  # noqa: E402

CASES = json.loads(
    (Path(__file__).parent / "test_cases.json").read_text(encoding="utf-8"))
CASE_BY_ID = {c["id"]: c for c in CASES["cases"]}


def _run_case(case_id):
    case = CASE_BY_ID[case_id]
    return solve_circuit(case["elements"], mode=case["mode"])


class TestHardCase2Senior(unittest.TestCase):
    """硬 Case #2：高中电压表内阻有限 → 路端电压 < E（红线回归）。"""

    def test_senior_terminal_below_E(self):
        res = _run_case("HC2_senior")
        self.assertTrue(res["ok"], res)
        exp = CASE_BY_ID["HC2_senior"]["expect"]
        er = res["element_results"]["b1"]
        # 路端电压 < E（有放电电流时绝不能等于 E）
        self.assertLess(er["voltage"], er["E"])
        self.assertEqual(check_terminal_voltage(res), [])  # 回归断言通过
        # 解析值：I = 3/(0.5 + 10∥10k) ≈ 0.286，路端 ≈ 2.857
        self.assertGreater(er["voltage"], exp["terminal_min"])
        self.assertLess(er["voltage"], exp["terminal_max"])
        # 电压表读数有限且 < E（加载效应）
        v_read = abs(res["element_results"]["v1"]["voltage"])
        self.assertLess(v_read, exp["voltmeter_max"])
        self.assertGreater(v_read, 2.8)
        self.assertEqual(res["element_results"]["v1"]["current"] != 0, True)

    def test_junior_regression_terminal_equals_E(self):
        """不得破坏 P2 理想分支：初中同拓扑路端电压必须等于 E。"""
        res = _run_case("HC2_junior_regression")
        self.assertTrue(res["ok"], res)
        exp = CASE_BY_ID["HC2_junior_regression"]["expect"]
        self.assertAlmostEqual(res["element_results"]["b1"]["voltage"],
                               exp["terminal_exact"], places=9)
        self.assertAlmostEqual(res["element_results"]["b1"]["current"],
                               0.3, places=9)  # 3/10，内阻被隐藏


class TestSeniorConfig(unittest.TestCase):
    """高中电表实内阻可设定（props.r_internal 覆盖默认 10kΩ/0.1Ω）。"""

    def test_voltmeter_internal_configurable(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0, "r_internal": 0.5}},
            {"id": "r1", "type": "resistor", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0}},
            {"id": "v1", "type": "voltmeter", "nodes": ["n1", "n2"],
             "props": {"r_internal": 5000.0}},
        ]
        res = solve_circuit(els, mode="senior")
        self.assertTrue(res["ok"], res)
        v_5k = abs(res["element_results"]["v1"]["voltage"])
        # 默认 10k 对照（HC2 数据）
        res10 = _run_case("HC2_senior")
        v_10k = abs(res10["element_results"]["v1"]["voltage"])
        self.assertLess(v_5k, v_10k)  # 内阻越小加载越重，读数越低

    def test_ammeter_internal_senior(self):
        """高中电流表 0.1Ω 串入回路：I = 3/10.1。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n2", "n3"],
             "props": {"resistance": 10.0}},
            {"id": "a1", "type": "ammeter", "nodes": ["n3", "n1"],
             "props": {}},
        ]
        res = solve_circuit(els, mode="senior")
        self.assertTrue(res["ok"], res)
        self.assertAlmostEqual(abs(res["element_results"]["a1"]["current"]),
                               3.0 / 10.1, places=9)

    def test_internal_node_matrix_dimension(self):
        """内部节点方案 A：高中电池带内阻 → 矩阵多 1 阶（P0 §5）。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0, "r_internal": 0.5}},
            {"id": "r1", "type": "resistor", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0}},
        ]
        from core import mna
        from core.topology import build_graph
        topo = build_graph(els, mode="senior")
        a_mat, _z, meta = mna.assemble_matrix(topo, els, mode="senior")
        # 拓扑节点 2（非地 1）+ 内部节点 1 + 电池 1 = 3 阶
        self.assertEqual(a_mat.shape, (3, 3))
        self.assertEqual(len(meta["internal_nodes"]), 1)
        self.assertIn("__int__b1", meta["node_index"])
        # 解析验证：I = 3/10.5，路端 = 3 - 0.5*I ≈ 2.857
        res = solve_circuit(els, mode="senior")
        self.assertAlmostEqual(res["element_results"]["b1"]["current"],
                               3.0 / 10.5, places=9)
        self.assertAlmostEqual(res["element_results"]["b1"]["voltage"],
                               3.0 - 0.5 * 3.0 / 10.5, places=9)


class TestWatchdog(unittest.TestCase):
    """QThread + QTimer 看门狗：200ms 预警 / 800ms 强杀，不崩溃。"""

    def test_fast_solve_returns_result(self):
        from core.watchdog import run_with_watchdog
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0}},
        ]
        res = run_with_watchdog(els, warn_ms=200, kill_ms=800)
        self.assertTrue(res["ok"], res)
        self.assertFalse(res["watchdog"]["killed"])
        self.assertFalse(res["watchdog"]["warned"])
        self.assertAlmostEqual(abs(res["element_results"]["r1"]["current"]),
                               0.3, places=6)

    def test_slow_solve_killed_at_limit(self):
        """超过 kill 限时 → 返回 error=timeout，程序不崩溃。"""
        import time as _time
        from unittest.mock import patch

        import core.watchdog as wd

        def slow_solve(elements, mode="junior"):
            _time.sleep(0.6)
            return {"ok": True, "element_results": {}}

        with patch.object(wd, "solve_circuit", slow_solve):
            res = wd.run_with_watchdog([], warn_ms=50, kill_ms=150)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "timeout")
        self.assertEqual(res["message"], "求解超时，请检查环路")
        self.assertTrue(res["watchdog"]["killed"])
        self.assertTrue(res["watchdog"]["warned"])


class TestLogging(unittest.TestCase):
    """软件日志：求解事件落盘，不写 C 盘（项目 logs 目录）。"""

    def test_solve_logged_to_file(self):
        import shutil
        from core import logging_setup

        base = Path(__file__).resolve().parents[1] / "logs"
        base.mkdir(exist_ok=True)
        td = base / "_test_tmp"
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        self.addCleanup(logging_setup.reset_logging)
        td.mkdir(exist_ok=True)

        d = logging_setup.setup_logging(log_dir=str(td))
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"])
        logging.shutdown()
        from core.logging_setup import log_file_path
        content = log_file_path(str(d)).read_text(encoding="utf-8")
        self.assertIn("solve start", content)
        self.assertIn("solve done", content)


class TestDarkCurrentClamp(unittest.TestCase):
    """V1.12：暗电流归零（用户裁定）——|I|<1e-6A 直接按 0 展示。"""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_open_switch_dark_current_zero(self):
        """断开开关回路：开关/电池/电流表的暗电流全部显示为 0。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "s1", "type": "switch", "nodes": ["n2", "n3"],
             "props": {"closed": False}},
            {"id": "r1", "type": "resistor", "nodes": ["n3", "n1"],
             "props": {"resistance": 10.0}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"], res)
        for eid in ("s1", "b1", "r1"):
            self.assertEqual(res["element_results"][eid]["current"], 0.0,
                             eid)
            self.assertEqual(res["element_results"][eid]["power"], 0.0,
                             eid)
        # 视觉层读数同样为 0（展示口径）
        from core.solver import calc_visual_state
        st = calc_visual_state(res)
        self.assertEqual(st["elements"]["s1"]["readings"]["I"], 0.0)
        self.assertEqual(st["elements"]["b1"]["readings"]["I"], 0.0)

    def test_real_current_not_clamped(self):
        """正常回路 0.1A 不受影响。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n1", "n2"],
             "props": {"resistance": 30.0}},
        ]
        res = solve_circuit(els)
        self.assertAlmostEqual(
            abs(res["element_results"]["r1"]["current"]), 0.1, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
