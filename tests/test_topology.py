# -*- coding: utf-8 -*-
"""P1 拓扑分析单元测试。

运行方式（工作区根目录）::
    python tests/test_topology.py -v
（直接 python 运行，不依赖 pytest；print 输出仅 ASCII）
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.topology import (  # noqa: E402
    R_CLOSED,
    R_OPEN,
    build_graph,
    effective_resistance,
)


def _series_three_switches(all_closed: bool):
    """硬 Case 电路：电池 - s1 - s2 - s3 - 电阻 构成单回路。"""
    return [
        {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
         "props": {"E": 3.0, "r_internal": 0.0}},
        {"id": "s1", "type": "switch", "nodes": ["n2", "n3"],
         "props": {"closed": all_closed}},
        {"id": "s2", "type": "switch", "nodes": ["n3", "n4"],
         "props": {"closed": all_closed}},
        {"id": "s3", "type": "switch", "nodes": ["n4", "n5"],
         "props": {"closed": all_closed}},
        {"id": "r1", "type": "resistor", "nodes": ["n5", "n1"],
         "props": {"resistance": 10.0}},
    ]


class TestHardCaseThreeSwitches(unittest.TestCase):
    """验收硬 Case（P1）：三个开关串联断开时，节点数正确。"""

    def test_all_open_node_count_and_connectivity(self):
        topo = build_graph(_series_three_switches(all_closed=False))
        # 5 个原始节点，无导线 → 5 个正则节点
        self.assertEqual(len(topo.nodes), 5)
        self.assertEqual(len(topo.matrix_nodes), 4)  # 去掉地
        # 三个断开开关均不导通：导通边只有电池(n1-n2)和电阻(n5-n1)
        edges = set()
        for a, nbrs in topo.adj.items():
            for b in nbrs:
                edges.add((a, b))
        self.assertEqual(edges, {("n1", "n2"), ("n2", "n1"),
                                 ("n1", "n5"), ("n5", "n1")})
        # 断开开关不在任何导通邻接表中
        for s in ("s1", "s2", "s3"):
            for nbrs in topo.adj.values():
                self.assertNotIn(s, [x for v in nbrs.values() for x in v])
        # 导通图分为 3 个分量：{n1,n2,n5}、{n3}、{n4}
        self.assertEqual(len(topo.components), 3)
        sizes = sorted(len(c) for c in topo.components)
        self.assertEqual(sizes, [1, 1, 3])

    def test_all_closed_single_loop(self):
        topo = build_graph(_series_three_switches(all_closed=True))
        self.assertEqual(len(topo.nodes), 5)
        # 闭合后整个回路连通
        self.assertEqual(len(topo.components), 1)
        self.assertEqual(len(topo.components[0]), 5)

    def test_ground_is_battery_negative(self):
        topo = build_graph(_series_three_switches(all_closed=False))
        self.assertEqual(topo.ground, "n2")  # b1 负极，且未被导线合并


class TestSwitchBinaryResistor(unittest.TestCase):
    """红线：开关不是独立元件，是二值电阻。"""

    def test_switch_resistance_values(self):
        closed = {"id": "s", "type": "switch", "nodes": ["a", "b"],
                  "props": {"closed": True}}
        opened = {"id": "s", "type": "switch", "nodes": ["a", "b"],
                  "props": {"closed": False}}
        self.assertEqual(effective_resistance(closed), R_CLOSED)
        self.assertEqual(effective_resistance(opened), R_OPEN)

    def test_open_switch_is_not_conducting_edge(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 1.5}},
            {"id": "s1", "type": "switch", "nodes": ["n2", "n3"],
             "props": {"closed": False}},
        ]
        topo = build_graph(els)
        # 电池边 n1-n2 仍在；断开开关不产生 n2-n3 导通边
        self.assertEqual(topo.adj["n2"], {"n1": ["b1"]})
        self.assertEqual(topo.adj["n3"], {})
        self.assertEqual(len(topo.components), 2)


class TestWireMerging(unittest.TestCase):
    """导线段合并节点：导线不产生边，只焊接两端。"""

    def test_wires_merge_nodes(self):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "w1", "type": "wire", "nodes": ["n2", "n3"], "props": {}},
            {"id": "r1", "type": "resistor", "nodes": ["n3", "n4"],
             "props": {"resistance": 5.0}},
            {"id": "w2", "type": "wire", "nodes": ["n4", "n1"], "props": {}},
        ]
        topo = build_graph(els)
        # {n1,n4} 合并、{n2,n3} 合并 → 2 个正则节点
        self.assertEqual(len(topo.nodes), 2)
        self.assertEqual(topo.node_of["n1"], topo.node_of["n4"])
        self.assertEqual(topo.node_of["n2"], topo.node_of["n3"])
        self.assertEqual(len(topo.matrix_nodes), 1)  # MNA 电压未知量 1 个
        # 电池与电阻都连在合并后的两节点之间 → 单分量连通
        self.assertEqual(len(topo.components), 1)

    def test_wire_self_loop_rejected(self):
        els = [{"id": "w1", "type": "wire", "nodes": ["n1", "n1"], "props": {}}]
        with self.assertRaises(ValueError):
            build_graph(els)


class TestMeters(unittest.TestCase):
    """电表理想化（初中）与实际内阻（高中）。"""

    def _base_circuit(self, mode):
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n1", "n2"],
             "props": {"resistance": 10.0}},
            {"id": "v1", "type": "voltmeter", "nodes": ["n1", "n2"], "props": {}},
            {"id": "a1", "type": "ammeter", "nodes": ["n2", "n3"], "props": {}},
        ]
        return els, mode

    def test_junior_ideal_meters(self):
        els, mode = self._base_circuit("junior")
        topo = build_graph(els, mode=mode)
        # 理想电压表 ∞Ω：不产生导通边；理想电流表 0Ω：导通边
        self.assertEqual(effective_resistance(els[2], mode), None)
        self.assertEqual(effective_resistance(els[3], mode), R_CLOSED)
        # 导通边：b1(n1-n2), r1(n1-n2), a1(n2-n3) → 全连通
        self.assertEqual(len(topo.components), 1)

    def test_senior_meter_internal_resistance(self):
        els, mode = self._base_circuit("senior")
        topo = build_graph(els, mode=mode)
        self.assertEqual(effective_resistance(els[2], mode), 10_000.0)
        self.assertEqual(effective_resistance(els[3], mode), 0.1)
        # 高中电压表也导通（10kΩ），仍是全连通
        self.assertEqual(len(topo.components), 1)


class TestGroundAndMisc(unittest.TestCase):

    def test_ground_fallback_without_battery(self):
        els = [{"id": "r1", "type": "resistor", "nodes": ["n2", "n1"],
                "props": {"resistance": 1.0}}]
        topo = build_graph(els)
        self.assertEqual(topo.ground, "n1")  # 最小节点兜底
        self.assertEqual(topo.matrix_nodes, ["n2"])

    def test_dangling_node_still_exists(self):
        """悬空节点保留在拓扑中（供 P4 标灰）：断开开关另一侧节点孤立存在。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "s1", "type": "switch", "nodes": ["n2", "n3"],
             "props": {"closed": False}},
        ]
        topo = build_graph(els)
        self.assertIn("n3", topo.nodes)
        self.assertEqual([c for c in topo.components if c == {"n3"}], [{"n3"}])
        self.assertEqual(topo.elements_by_node["n3"], ["s1"])

    def test_bad_element_rejected(self):
        with self.assertRaises(ValueError):
            build_graph([{"id": "x", "type": "capacitor",
                          "nodes": ["a", "b"], "props": {}}])
        with self.assertRaises(ValueError):
            build_graph([{"id": "r1", "type": "resistor",
                          "nodes": ["a"], "props": {"resistance": 1.0}}])

    def test_matrix_dimension_contract(self):
        """P0 §5 维度对照：电池+电阻 单回路 → 3 个节点（含地）→ MNA 2+1=3 阶。"""
        els = [
            {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
             "props": {"E": 3.0}},
            {"id": "r1", "type": "resistor", "nodes": ["n2", "n3"],
             "props": {"resistance": 10.0}},
            {"id": "r2", "type": "resistor", "nodes": ["n3", "n1"],
             "props": {"resistance": 20.0}},
        ]
        topo = build_graph(els)
        self.assertEqual(len(topo.nodes), 3)
        self.assertEqual(topo.matrix_dimension(num_voltage_sources=1), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
