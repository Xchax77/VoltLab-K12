# -*- coding: utf-8 -*-
"""P1 拓扑分析：导线段合并节点 + 邻接表构建。

红线约束（需求书 10.3）：
- 电学连通只用图论 node id 计算，禁止碰像素坐标；
- 开关不是独立物理对象，是二值电阻（闭合 R_CLOSED / 断开 R_OPEN）；
- 本模块纯 Python，不接入 PyQt5。

输入元件结构（需求书 第十一章）::

    {"id": "b1", "type": "battery", "nodes": ["n1", "n2"],
     "props": {"E": 3.0, "r_internal": 0.0}}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# 数值哨兵值（与 archive/P0_MNA算法说明.md §3.4 一致）
# ---------------------------------------------------------------------------
R_CLOSED = 1e-3      # 闭合开关 / 理想电流表 / 导线残差（教学语义 0Ω）
R_OPEN = 1e9         # 断开开关（教学语义 ∞Ω，等效开路、不 stamp）
R_WIRE_MIN = 1e-9    # 防止 1/R 除零的硬下限

# 高中模式电表默认内阻（需求书 第二章）
R_VOLTMETER_SENIOR = 10_000.0
R_AMMETER_SENIOR = 0.1

WIRE = "wire"
BATTERY = "battery"
RESISTOR = "resistor"
BULB = "bulb"
SWITCH = "switch"
AMMETER = "ammeter"
VOLTMETER = "voltmeter"
BELL = "bell"           # 电铃（线性电阻类用电器）
MOTOR = "motor"         # 马达（线性电阻类用电器）
DIODE = "diode"         # 二极管（状态迭代：导通=Vf 源+R_on，截止=开路）
LED = "led"             # 发光二极管（同二极管，Vf≈2V，导通发光）
RHEOSTAT = "rheostat"   # 滑动变阻器（3 端子：A、B、滑片 W）
SPDT = "spdt"           # 单刀双掷开关（3 端子：公共 + 两掷）
SP3T = "sp3t"           # 单刀三掷开关（4 端子：公共 + 三掷）

KNOWN_TYPES = {WIRE, BATTERY, RESISTOR, BULB, SWITCH, AMMETER, VOLTMETER,
               BELL, MOTOR, DIODE, LED, RHEOSTAT, SPDT, SP3T}

# 各类型端子数（默认 2）
NODE_COUNTS = {RHEOSTAT: 3, SPDT: 3, SP3T: 4}

# 二极管默认参数
DIODE_VF_DEFAULT = 0.7
LED_VF_DEFAULT = 2.0
DIODE_R_ON_DEFAULT = 1.0


def element_nodes_count(t: str) -> int:
    return NODE_COUNTS.get(t, 2)


def is_diode(t: str) -> bool:
    return t in (DIODE, LED)


def diode_vf(el: dict) -> float:
    return float(el.get("props", {}).get(
        "vf", LED_VF_DEFAULT if el["type"] == LED else DIODE_VF_DEFAULT))


def diode_r_on(el: dict) -> float:
    return float(el.get("props", {}).get("r_on", DIODE_R_ON_DEFAULT))


def rheostat_resistances(el: dict):
    """滑动变阻器 → (R_AW, R_WB)：滑片分成的两段电阻。"""
    r_max = float(el.get("props", {}).get("r_max", 10.0))
    pos = min(1.0, max(0.0, float(el.get("props", {}).get("position", 0.5))))
    return r_max * pos, r_max * (1.0 - pos)


def spdt_active_index(el: dict) -> int:
    """掷开关选通掷的下标（el["nodes"] 内，公共端=0）。

    供已有正则节点列表的调用方直接取 nds[0]/nds[1+idx]，
    避免混用原始节点名与正则节点名。
    """
    pos = int(el.get("props", {}).get("position", 0))
    return max(0, min(pos, len(el["nodes"]) - 2))


def spdt_active_nodes(el: dict):
    """掷开关 → (公共节点, 选通节点)。nodes = [公共, 掷1, 掷2(, 掷3)]。

    注意：返回**原始**节点名。需要正则节点时用 topo.node_of 转换，
    或改用 spdt_active_index + 已正则化的节点列表。
    """
    nds = el["nodes"]
    return nds[0], nds[1 + spdt_active_index(el)]


# ---------------------------------------------------------------------------
# 元件电阻语义
# ---------------------------------------------------------------------------

def effective_resistance(el: dict, mode: str = "junior") -> Optional[float]:
    """返回元件的等值电阻（Ω）。

    返回值语义：
    - float        → 按该电阻参与求解 / 连通判定；
    - None（导体） → 电压源/多端子/状态元件类：由 mna/solver 特殊处理
      （battery、rheostat、spdt、sp3t、diode、led）；
    - None（开路） → 初中理想电压表：∞Ω，不导通。
    导线（wire）不在此处理，由节点合并吸收。
    """
    t = el["type"]
    props = el.get("props", {})
    if t == SWITCH:
        # 开关 = 二值电阻，这是唯一实现方式（红线）
        return R_CLOSED if props.get("closed", False) else R_OPEN
    if t in (RESISTOR, BELL, MOTOR):
        return float(props["resistance"])
    if t == BULB:
        # 初中：定值电阻（额定电阻）。高中 R(T) 非线性属 P3 之后，本阶段不实现。
        return float(props["resistance"])
    if t == AMMETER:
        if mode == "senior":
            return float(props.get("r_internal", R_AMMETER_SENIOR))
        return R_CLOSED  # 理想电流表 0Ω
    if t == VOLTMETER:
        if mode == "senior":
            return float(props.get("r_internal", R_VOLTMETER_SENIOR))
        return None  # 理想电压表 ∞Ω → 开路
    if t in (BATTERY, RHEOSTAT, SPDT, SP3T, DIODE, LED):
        return None  # 特殊元件：mna/solver 按类型专门 stamp
    raise ValueError("unknown element type: %r" % (t,))


# ---------------------------------------------------------------------------
# 并查集：导线段合并节点
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        p = self.parent.setdefault(x, x)
        while p != x:
            x, p = p, self.parent[p]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # 正则代表取字典序较小者，保证结果确定性
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


# ---------------------------------------------------------------------------
# 拓扑结果
# ---------------------------------------------------------------------------

@dataclass
class Topology:
    node_of: dict          # 原始节点 id -> 正则节点 id（导线合并后）
    nodes: list            # 正则节点列表（排序）
    ground: str            # 参考节点（正则 id）
    matrix_nodes: list     # nodes 去掉 ground —— MNA 电压未知量集合
    adj: dict              # {正则节点: {邻居正则节点: [元件id]}}，仅导通边
    edges: list            # [(a, b, 元件id, 电阻|None)]；None=电压源类或开路表
    elements_by_node: dict # 正则节点 -> [元件id]（含悬空节点判定）
    components: list       # 导通图连通分量（正则节点集合列表）

    def matrix_dimension(self, num_voltage_sources: int) -> int:
        """MNA 矩阵阶数 = (节点数-1) + 理想电压源数。"""
        return len(self.matrix_nodes) + num_voltage_sources


# ---------------------------------------------------------------------------
# buildGraph 主入口
# ---------------------------------------------------------------------------

def build_graph(elements: list, mode: str = "junior",
                ground_hint: Optional[str] = None) -> Topology:
    """构建电路拓扑。

    步骤：
    1. 校验元件结构；
    2. 导线段并查集合并节点（导线不产生边，只焊接两端）；
    3. 非导线元件生成边：开关按二值电阻处理（断开=不导通）；
    4. 选参考节点（地）：优先电池负极，其次 ground_hint，最后最小节点；
    5. 计算导通图连通分量。
    """
    uf = _UnionFind()

    # --- 1. 校验 + 收集所有节点 ---
    all_nodes = set()
    for el in elements:
        if el.get("type") not in KNOWN_TYPES:
            raise ValueError("unknown element type: %r" % (el.get("type"),))
        nds = el.get("nodes")
        need = element_nodes_count(el["type"])
        if not nds or len(nds) != need:
            raise ValueError("element %r (%s) must have exactly %d nodes"
                             % (el.get("id"), el["type"], need))
        if len(set(nds)) != len(nds):
            raise ValueError("element %r has duplicated nodes"
                             % (el.get("id"),))
        for n in nds:
            uf.find(n)
            all_nodes.add(n)

    # --- 2. 导线合并节点（含变阻器 0 阻值段：等效导线） ---
    # 变阻器某段 r=0（滑片到端点）时按导线焊接节点：避免 mna 用 g=1e12
    # stamp 0Ω 段，与 gmin=1e-9 组合造成浮空岛矩阵奇异（误报 short）。
    for el in elements:
        if el["type"] == WIRE:
            uf.union(el["nodes"][0], el["nodes"][1])
        elif el["type"] == RHEOSTAT:
            r1, r2 = rheostat_resistances(el)
            if r1 <= 1e-12:
                uf.union(el["nodes"][0], el["nodes"][2])   # A-W
            if r2 <= 1e-12:
                uf.union(el["nodes"][2], el["nodes"][1])   # W-B

    node_of = {n: uf.find(n) for n in all_nodes}
    nodes = sorted(set(node_of.values()))

    # --- 3. 建边（非导线元件） ---
    adj: dict = {n: {} for n in nodes}
    edges = []
    elements_by_node: dict = {n: [] for n in nodes}
    for el in elements:
        if el["type"] == WIRE:
            continue
        a, b = node_of[el["nodes"][0]], node_of[el["nodes"][1]]
        r = effective_resistance(el, mode)
        conducting = (r is None and el["type"] == BATTERY) or \
                     (r is not None and r < R_OPEN)
        edges.append((a, b, el["id"], r))
        elements_by_node[a].append(el["id"])
        elements_by_node[b].append(el["id"])
        if conducting and a != b:
            adj[a].setdefault(b, []).append(el["id"])
            adj[b].setdefault(a, []).append(el["id"])

    # --- 4. 选参考节点（地） ---
    ground = _pick_ground(elements, node_of, nodes, ground_hint)

    # --- 5. 连通分量（导通图） ---
    components = _connected_components(nodes, adj)

    return Topology(
        node_of=node_of,
        nodes=nodes,
        ground=ground,
        matrix_nodes=[n for n in nodes if n != ground],
        adj=adj,
        edges=edges,
        elements_by_node=elements_by_node,
        components=components,
    )


def _pick_ground(elements, node_of, nodes, hint):
    """参考节点：优先第一个电池（按 id 排序）的负极，其次 hint，最后最小节点。"""
    if not nodes:
        return ""  # 空电路（空画布求解不崩溃）
    batteries = sorted((el for el in elements if el["type"] == BATTERY),
                       key=lambda e: e["id"])
    if batteries:
        return node_of[batteries[0]["nodes"][1]]  # 约定 nodes[1] 为负极
    if hint is not None and hint in node_of:
        return node_of[hint]
    return nodes[0]


def _connected_components(nodes, adj):
    seen = set()
    comps = []
    for n in nodes:
        if n in seen:
            continue
        comp = {n}
        queue = [n]
        seen.add(n)
        while queue:
            cur = queue.pop()
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    comp.add(nb)
                    queue.append(nb)
        comps.append(comp)
    return comps
