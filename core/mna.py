# -*- coding: utf-8 -*-
"""P2：MNA 矩阵组装（初中理想化线性直流）。

对应 archive/P0_MNA算法说明.md 的伪代码落地：
- stamp_resistor：电导四点填充，接地节点不入矩阵；
- stamp_battery：理想电压源附加一行一列；
- 初中模式：电源内阻视为 0（隐藏内阻）、电压表 ∞Ω 不 stamp、电流表 0Ω；
- 开关为二值电阻（R_CLOSED / R_OPEN），断开开关仍以 R_OPEN stamp，
  避免浮空子电路导致矩阵奇异（配合 gmin 泄漏电导）；
- 短路预检：电池两端仅经"导线类"低阻支路连通 → ShortCircuitError；
- 灯泡：默认线性（定值 R0）；props.model == 'nonlinear' 时按 R(T) 模型，
  由 solver 牛顿迭代提供温度，本模块只负责按给定电阻 stamp。

红线：本模块只用 node id（拓扑层输出），禁止碰像素坐标。
"""

from __future__ import annotations

import numpy as np

from core.topology import (
    BATTERY,
    BULB,
    DIODE,
    LED,
    R_CLOSED,
    R_OPEN,
    RHEOSTAT,
    SP3T,
    SPDT,
    VOLTMETER,
    WIRE,
    Topology,
    diode_r_on,
    diode_vf,
    effective_resistance,
    is_diode,
    rheostat_resistances,
    spdt_active_index,
    spdt_active_nodes,
)

# ---------------------------------------------------------------------------
# 数值常量（与 P0 §3.4 对齐，测试可调）
# ---------------------------------------------------------------------------
GMIN = 1e-9          # 节点对地泄漏电导（gmin）：保证浮空孤岛可解且 V≈0
SHORT_R_MAX = 1e-2   # 支路电阻低于该值视为"导线类"（参与短路判定）

# 非线性灯泡 R(T) 模型参数（用户裁定：初中默认线性，可同时支持非线性）
T0_KELVIN = 293.15           # 参考温度（室温 20°C）
DEFAULT_ALPHA = 0.0045       # 电阻温度系数 1/K（钨丝近似）
DEFAULT_THERMAL_K = 40.0     # 热平衡系数 K/W：T = T0 + k * P


class ShortCircuitError(Exception):
    """纯导线（或低阻支路）短接电源。由 solver 捕获转为 {'error':'short'}。"""


def bulb_resistance(el: dict, temperature_k: float = T0_KELVIN) -> float:
    """灯泡电阻。

    - 线性（默认，初中）：恒为额定电阻 props['resistance']；
    - 非线性（model='nonlinear'）：R(T) = R0 * (1 + alpha * (T - T0))。
    """
    r0 = float(el["props"]["resistance"])
    if el.get("props", {}).get("model") == "nonlinear":
        alpha = float(el["props"].get("alpha", DEFAULT_ALPHA))
        return r0 * (1.0 + alpha * (temperature_k - T0_KELVIN))
    return r0


def is_nonlinear_bulb(el: dict) -> bool:
    return el["type"] == BULB and \
        el.get("props", {}).get("model") == "nonlinear"


def _conduction_pairs(el: dict, nds: list, mode: str = "junior"):
    """元件的导通支路列表 [(正则节点a, 正则节点b, 电阻)]。

    - 二端电阻类：一条支路；
    - 滑动变阻器：A-W、W-B 两条；
    - 掷开关：公共-选通掷一条（R_CLOSED）；
    - 二极管：导通与否未知（拓扑层近似），按开路处理（不参与短路/浮空）。
    """
    t = el["type"]
    if t == RHEOSTAT:
        r1, r2 = rheostat_resistances(el)
        return [(nds[0], nds[2], r1), (nds[2], nds[1], r2)]
    if t in (SPDT, SP3T):
        i = spdt_active_index(el)
        return [(nds[0], nds[i + 1], R_CLOSED)]
    r = effective_resistance(el, mode)
    if r is None:
        return []
    return [(nds[0], nds[1], r)]


# ---------------------------------------------------------------------------
# 短路预检
# ---------------------------------------------------------------------------

def floating_node_set(topo: Topology, elements: list, mode: str = "junior") -> set:
    """stamp 语义下（含 R_OPEN 支路、电池；∞Ω 电压表断开）的连通分量中，
    不含参考节点的节点集合 —— 浮空孤岛（供 P4 标灰、gmin 定点注入）。"""
    adj: dict = {n: set() for n in topo.nodes}
    for el in elements:
        t = el["type"]
        if t == WIRE:
            continue
        if t in (BATTERY, RHEOSTAT, SPDT, SP3T, DIODE, LED):
            conducting = True  # 多端子/状态元件按"可能导通"处理，避免误标灰
        else:
            r = effective_resistance(el, mode)
            conducting = r is not None and r < R_OPEN
        if not conducting:
            continue
        nds = [topo.node_of[n] for n in el["nodes"]]
        for a, b, _r in _conduction_pairs(el, nds, mode):
            if a != b:
                adj[a].add(b)
                adj[b].add(a)
    seen: set = set()
    floating: set = set()
    for n in topo.nodes:
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
        if topo.ground not in comp:
            floating |= comp
    return floating


def check_short(topo: Topology, elements: list, mode: str = "junior") -> bool:
    """判定电池两端是否被纯低阻（导线类）路径直接短接。

    判据：在"仅含 R <= SHORT_R_MAX 支路"的导通图中，
    任一电池的正则正极与正则负极连通 → 短路。
    （电阻、断开开关、∞Ω 电压表都会阻断该图，因此不误判。）
    """
    batteries = [e for e in elements if e["type"] == BATTERY]
    if not batteries:
        return False

    # 导线类邻接表（排除电池/二极管自身支路）
    tiny: dict = {}
    for el in elements:
        t = el["type"]
        if t in (WIRE, BATTERY, DIODE, LED):
            continue
        nds = [topo.node_of[n] for n in el["nodes"]]
        for a, b, r in _conduction_pairs(el, nds, mode):
            if a != b and r <= SHORT_R_MAX:
                tiny.setdefault(a, set()).add(b)
                tiny.setdefault(b, set()).add(a)

    for bat in batteries:
        pos = topo.node_of[bat["nodes"][0]]
        neg = topo.node_of[bat["nodes"][1]]
        if pos == neg:
            return True  # 正负极被导线合并到同一节点：必为短路
        # BFS
        seen = {pos}
        queue = [pos]
        while queue:
            cur = queue.pop()
            if cur == neg:
                return True
            for nb in tiny.get(cur, ()):
                if nb not in seen:
                    seen.add(nb)
                    queue.append(nb)
    return False


# ---------------------------------------------------------------------------
# 矩阵组装
# ---------------------------------------------------------------------------

def assemble_matrix(topo: Topology, elements: list, mode: str = "junior",
                    r_overrides: dict | None = None,
                    diode_states: dict | None = None):
    """组装 MNA 矩阵 A·x = z。

    返回 (A, z, meta)：
    - A: (N + M) 方阵；N = 非地拓扑节点数 + 内部节点数，M = 理想源数
      （电池 + 导通二极管）；
    - meta: {'node_index': {节点名: 行号}（含内部节点 "__int__<id>"）,
             'battery_row': {源id: 行号}, 'N': N, 'M': M,
             'internal_nodes': {源id: 内部节点名}}
    - 高中模式：内阻 > 0 的电池按 P0 §3.2 方案 A —— 理想源 stamp 在
      "正极 ↔ 内部节点"，电阻 r_internal stamp 在 "内部节点 ↔ 负极"；
    - 二极管（diode/led）：导通时 = 理想源 Vf（正极→负极压降）+ 串联 R_on
      （同样内部节点方案）；截止时 = R_OPEN 开路。状态由 solver 迭代给出；
    - 滑动变阻器：A-W 与 W-B 两段电阻；
    - 单刀双掷/三掷：公共-选通掷之间 R_CLOSED，其余掷开路。

    异常：ShortCircuitError —— 源两端合并到同一节点（无法建约束）。
    """
    r_overrides = r_overrides or {}
    diode_states = diode_states or {}
    idx = {n: i for i, n in enumerate(topo.matrix_nodes)}
    n_nodes = len(idx)

    # --- 理想源列表：电池 + 导通二极管 ---
    sources = []  # (id, pos, neg, E, r)
    for bat in sorted((e for e in elements if e["type"] == BATTERY),
                      key=lambda e: e["id"]):
        r_int = 0.0 if mode == "junior" else \
            float(bat["props"].get("r_internal", 0.0))
        sources.append((bat["id"],
                        topo.node_of[bat["nodes"][0]],
                        topo.node_of[bat["nodes"][1]],
                        float(bat["props"].get("E", 0.0)), r_int))
    for el in elements:
        if is_diode(el["type"]) and diode_states.get(el["id"]):
            sources.append((el["id"],
                            topo.node_of[el["nodes"][0]],
                            topo.node_of[el["nodes"][1]],
                            diode_vf(el), diode_r_on(el)))
    m_src = len(sources)

    # 内部节点（内阻/导通电阻 > 0 的源 → 方案 A）
    internal_nodes: dict = {}
    for sid, _p, _n, _e, r_ser in sources:
        if r_ser > 0:
            internal_nodes[sid] = "__int__%s" % sid
    idx_full = dict(idx)
    for i, sid in enumerate(sorted(internal_nodes)):
        idx_full[internal_nodes[sid]] = n_nodes + i
    n_total = n_nodes + len(internal_nodes)

    size = n_total + m_src
    a_mat = np.zeros((size, size))
    z_vec = np.zeros(size)

    def stamp_resistor(a: str, b: str, r: float) -> None:
        g = 1.0 / max(r, 1e-12)
        ia, ib = idx_full.get(a), idx_full.get(b)
        if ia is not None:
            a_mat[ia, ia] += g
        if ib is not None:
            a_mat[ib, ib] += g
        if ia is not None and ib is not None:
            a_mat[ia, ib] -= g
            a_mat[ib, ia] -= g

    # --- 电阻类元件（含闭合开关、电流表、灯泡、电铃、马达；断开开关/截止
    #     二极管按 R_OPEN stamp）---
    for el in elements:
        t = el["type"]
        if t in (WIRE, BATTERY) or is_diode(t):
            continue  # 电池/二极管在源 stamp 阶段处理
        if t == VOLTMETER and mode == "junior":
            continue  # 理想电压表 ∞Ω：开路，不 stamp
        if t == RHEOSTAT:
            nds = [topo.node_of[n] for n in el["nodes"]]
            r1, r2 = rheostat_resistances(el)
            stamp_resistor(nds[0], nds[2], r1)
            stamp_resistor(nds[2], nds[1], r2)
            continue
        if t in (SPDT, SP3T):
            a, b = spdt_active_nodes(el)
            stamp_resistor(topo.node_of[a], topo.node_of[b], R_CLOSED)
            continue
        r = r_overrides.get(el["id"])
        if r is None:
            r = effective_resistance(el, mode)
        if r is None:
            continue
        a = topo.node_of[el["nodes"][0]]
        b = topo.node_of[el["nodes"][1]]
        if a == b:
            continue
        stamp_resistor(a, b, min(r, R_OPEN))

    # --- gmin：仅注入浮空孤岛节点，保证其可解且 V≈0（不污染正常电路精度）---
    floating = floating_node_set(topo, elements, mode)
    for n in floating:
        i = idx.get(n)
        if i is not None:
            a_mat[i, i] += GMIN

    # --- 理想电压源 stamp（内阻/导通电阻 > 0 → 内部节点方案 A）---
    battery_row: dict = {}
    for k, (sid, pos, neg, e_v, r_ser) in enumerate(sources):
        if pos == neg:
            raise ShortCircuitError(
                "source %s terminals merged to one node" % sid)
        inode = internal_nodes.get(sid)
        src_a, src_b = (pos, inode) if inode else (pos, neg)
        row = n_total + k
        battery_row[sid] = row
        # 行约束：V(src_a) - V(src_b) = E
        if src_a in idx_full:
            a_mat[row, idx_full[src_a]] += 1.0
        if src_b in idx_full:
            a_mat[row, idx_full[src_b]] -= 1.0
        z_vec[row] = e_v
        # 列贡献：附加电流 I_k（定义：源内部从正极流向负极）
        if src_a in idx_full:
            a_mat[idx_full[src_a], row] += 1.0
        if src_b in idx_full:
            a_mat[idx_full[src_b], row] -= 1.0
        # 串联电阻：内部节点 ↔ 负极
        if inode:
            stamp_resistor(inode, neg, r_ser)

    meta = {
        "node_index": idx_full,
        "battery_row": battery_row,
        "N": n_total,
        "M": m_src,
        "internal_nodes": internal_nodes,
    }
    return a_mat, z_vec, meta
