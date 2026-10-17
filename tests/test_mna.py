# -*- coding: utf-8 -*-
"""P2 单元测试：MNA 组装 + 求解器 + 验收硬 Case（读取 test_cases.json）。

运行方式（工作区根目录）::
    python tests/test_mna.py -v
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from core import mna, solver  # noqa: E402
from core.solver import bulb_is_on, solve_circuit  # noqa: E402
from core.topology import build_graph  # noqa: E402

CASES = json.loads(
    (Path(__file__).parent / "test_cases.json").read_text(encoding="utf-8"))
CASE_BY_ID = {c["id"]: c for c in CASES["cases"]}


def _run_case(case_id):
    case = CASE_BY_ID[case_id]
    return solve_circuit(case["elements"], mode=case["mode"])


class TestHardCasesFromJson(unittest.TestCase):
    """验收硬 Case（数据驱动，test_cases.json）。"""

    def test_hc1_closed_bulb_on(self):
        res = _run_case("HC1_closed")
        self.assertTrue(res["ok"], res)
        exp = CASE_BY_ID["HC1_closed"]["expect"]
        i_bulb = res["element_results"]["lamp1"]["current"]
        # 电流符号取决于元件端点顺序（正方向 a→b），验收取幅值
        self.assertGreater(abs(i_bulb), exp["bulb_current_min"])
        self.assertLess(abs(i_bulb), exp["bulb_current_max"])
        self.assertEqual(bulb_is_on(res["element_results"]["lamp1"]),
                         exp["bulb_on"])
        # 电流表读数 = 回路电流
        self.assertAlmostEqual(res["element_results"]["a1"]["current"],
                               i_bulb, places=9)
        # 电压表读数（初中理想：读电压、电流为 0）
        v_read = abs(res["element_results"]["v1"]["voltage"])
        self.assertGreater(v_read, exp["voltmeter_min"])
        self.assertLess(v_read, exp["voltmeter_max"])
        self.assertEqual(res["element_results"]["v1"]["current"], 0.0)

    def test_hc1_open_all_zero(self):
        """硬 Case #1：串联开关断开 → 全灭、读数归零。"""
        res = _run_case("HC1_open")
        self.assertTrue(res["ok"], res)
        exp = CASE_BY_ID["HC1_open"]["expect"]
        for eid, er in res["element_results"].items():
            if er["current"] is not None:
                self.assertLess(abs(er["current"]),
                                exp["max_abs_current_any"],
                                "element %s current %g" % (eid, er["current"]))
        self.assertEqual(bulb_is_on(res["element_results"]["lamp1"]),
                         exp["bulb_on"])
        # 读数归零口径：需求书阈值 |I|<1e-6，电压残余 <1e-6V（R_OPEN 微电流）
        self.assertLess(abs(res["element_results"]["v1"]["voltage"]), 1e-6)

    def test_hc3_short_wire(self):
        res = _run_case("HC3_short_wire")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"],
                         CASE_BY_ID["HC3_short_wire"]["expect"]["error"])

    def test_hc3_short_switch(self):
        res = _run_case("HC3_short_switch")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "short")


class TestLinearSolve(unittest.TestCase):
    """线性求解正确性（解析解对照）。"""

    def test_series_loop_analytic(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n2", "n3"],
             "props": {"resistance": 10.0}},
            {"id": "r2", "type": "resistor", "nodes": ["n3", "n1"],
             "props": {"resistance": 20.0}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"], res)
        # I = 3/30 = 0.1A；电池正=放电
        self.assertAlmostEqual(res["element_results"]["b1"]["current"],
                               0.1, places=9)
        self.assertAlmostEqual(abs(res["element_results"]["r1"]["current"]),
                               0.1, places=9)
        self.assertAlmostEqual(abs(res["element_results"]["r2"]["current"]),
                               0.1, places=9)
        # 节点电压（地=b1 负极 n2）
        self.assertAlmostEqual(res["node_voltages"]["n1"], 3.0, places=9)
        self.assertAlmostEqual(res["node_voltages"]["n3"], 1.0, places=9)
        self.assertAlmostEqual(res["node_voltages"]["n2"], 0.0, places=9)

    def test_parallel_analytic(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0}},
            {"id": "r2", "type": "resistor", "nodes": ["n1", "n2"],
             "props": {"resistance": 20.0}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"], res)
        self.assertAlmostEqual(res["element_results"]["r1"]["current"],
                               0.3, places=9)
        self.assertAlmostEqual(res["element_results"]["r2"]["current"],
                               0.15, places=9)
        self.assertAlmostEqual(res["element_results"]["b1"]["current"],
                               0.45, places=9)

    def test_wire_merge_loop(self):
        """导线合并节点后求解不变。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "w1", "type": "wire", "nodes": ["n2", "n3"], "props": {}},
            {"id": "r1", "type": "resistor", "nodes": ["n3", "n4"],
             "props": {"resistance": 10.0}},
            {"id": "w2", "type": "wire", "nodes": ["n4", "n1"], "props": {}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"], res)
        self.assertAlmostEqual(abs(res["element_results"]["r1"]["current"]),
                               0.3, places=9)
        self.assertIsNone(res["element_results"]["w1"]["current"])

    def test_matrix_dimension_contract(self):
        """P0 §5：电池+两电阻单回路 → N=2（非地）、M=1 → 3x3。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n2", "n3"],
             "props": {"resistance": 10.0}},
            {"id": "r2", "type": "resistor", "nodes": ["n3", "n1"],
             "props": {"resistance": 20.0}},
        ]
        from core.topology import build_graph
        topo = build_graph(els)
        a_mat, z_vec, meta = mna.assemble_matrix(topo, els)
        self.assertEqual(a_mat.shape, (3, 3))
        self.assertEqual(z_vec.shape, (3,))
        self.assertEqual(meta["N"], 2)
        self.assertEqual(meta["M"], 1)


class TestBulbModels(unittest.TestCase):
    """灯泡：初中默认线性；非线性 R(T) 牛顿迭代（用户裁定并存）。"""

    def test_junior_bulb_default_linear(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "lamp1", "type": "bulb", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"], res)
        self.assertAlmostEqual(res["element_results"]["lamp1"]["current"],
                               0.3, places=9)
        self.assertAlmostEqual(res["element_results"]["lamp1"]["resistance"],
                               10.0, places=9)
        self.assertTrue(bulb_is_on(res["element_results"]["lamp1"]))

    def test_nonlinear_bulb_analytic(self):
        """电池直挂非线性灯泡：R 有闭式解 R=(R0+sqrt(R0^2+4*R0*a*k*V^2))/2。"""
        r0, alpha, k, e_v = 10.0, 0.0045, 40.0, 3.0
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": e_v}},
            {"id": "lamp1", "type": "bulb", "nodes": ["n1", "n2"],
             "props": {"resistance": r0, "model": "nonlinear",
                       "alpha": alpha}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"], res)
        r_analytic = (r0 + math.sqrt(r0 * r0 + 4 * r0 * alpha * k * e_v * e_v)) / 2
        er = res["element_results"]["lamp1"]
        self.assertAlmostEqual(er["resistance"], r_analytic, delta=1e-3)
        self.assertAlmostEqual(er["current"], e_v / r_analytic, delta=1e-4)
        self.assertGreater(er["resistance"], r0)  # 发热升温 → 电阻增大
        self.assertGreaterEqual(res["iterations"], 1)

    def test_nonlinear_bulb_short_still_detected(self):
        """非线性灯泡电路中短路仍被捕获。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "lamp1", "type": "bulb", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0, "model": "nonlinear"}},
            {"id": "s1", "type": "switch", "nodes": ["n1", "n2"],
             "props": {"closed": True}},
        ]
        res = solve_circuit(els)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "short")


class TestFloatingAndStability(unittest.TestCase):
    """浮空孤岛：不崩溃、电压约 0、标记 floating（供 P4 标灰）。"""

    def test_isolated_resistor_island(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0}},
            {"id": "r2", "type": "resistor", "nodes": ["n8", "n9"],
             "props": {"resistance": 5.0}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"], res)
        self.assertEqual(set(res["floating_nodes"]), {"n8", "n9"})
        self.assertAlmostEqual(res["element_results"]["r2"]["current"], 0.0,
                               places=9)
        self.assertAlmostEqual(res["node_voltages"]["n8"], 0.0, places=6)
        # 主回路不受影响
        self.assertAlmostEqual(res["element_results"]["r1"]["current"],
                               0.3, places=9)

    def test_open_switch_island(self):
        """P1 硬 Case 延伸：三开关全断开也能稳定求解（R_OPEN stamp + gmin）。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "s1", "type": "switch", "nodes": ["n2", "n3"],
             "props": {"closed": False}},
            {"id": "s2", "type": "switch", "nodes": ["n3", "n4"],
             "props": {"closed": False}},
            {"id": "s3", "type": "switch", "nodes": ["n4", "n5"],
             "props": {"closed": False}},
            {"id": "r1", "type": "resistor", "nodes": ["n5", "n1"],
             "props": {"resistance": 10.0}},
        ]
        res = solve_circuit(els)
        self.assertTrue(res["ok"], res)
        self.assertLess(abs(res["element_results"]["b1"]["current"]), 1e-6)


class TestThrowSwitchPositions(unittest.TestCase):
    """回归：双掷/三掷开关各掷位求解不崩溃（v0.1.6 修复）。

    缺陷：_conduction_pairs 的 SPDT 分支返回原始节点名而非正则节点，
    floating_node_set / check_short 抛 KeyError → 求解线程 abort。
    """

    def _loop(self, throw_type):
        return [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "s1", "type": throw_type,
             "nodes": ["n2", "n3", "n4", "n5"][:3 if throw_type == "spdt" else 4],
             "props": {}},
            {"id": "lamp1", "type": "bulb", "nodes": ["n3", "n1"],
             "props": {"resistance": 10.0}},
            {"id": "lamp2", "type": "bulb", "nodes": ["n4", "n1"],
             "props": {"resistance": 10.0}},
        ] + ([{"id": "lamp3", "type": "bulb", "nodes": ["n5", "n1"],
               "props": {"resistance": 10.0}}] if throw_type == "sp3t" else [])

    def test_spdt_all_positions(self):
        for pos in range(2):
            els = self._loop("spdt")
            els[1]["props"]["position"] = pos
            topo = build_graph(els)
            # floating_node_set / check_short 不再 KeyError
            mna.floating_node_set(topo, els)
            res = solve_circuit(els)
            self.assertTrue(res["ok"], res)
            lit = "lamp1" if pos == 0 else "lamp2"
            self.assertGreater(
                abs(res["element_results"][lit]["current"]), 0.1,
                "pos=%d 时 %s 应点亮" % (pos, lit))

    def test_sp3t_all_positions(self):
        for pos in range(3):
            els = self._loop("sp3t")
            els[1]["props"]["position"] = pos
            res = solve_circuit(els)
            self.assertTrue(res["ok"], res)

    def test_spdt_active_nodes_still_raw(self):
        """spdt_active_nodes 语义保持：返回原始节点名（solver.extract 依赖）。"""
        from core.topology import spdt_active_nodes
        els = self._loop("spdt")
        els[1]["props"]["position"] = 1
        self.assertEqual(spdt_active_nodes(els[1]), ("n2", "n4"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
