# -*- coding: utf-8 -*-
"""P2：电路求解器 —— NumPy 求解 Ax=b，输出各支路电流。

- 线性电路：一次 assemble + numpy.linalg.solve；
- 含非线性灯泡（model='nonlinear'）：牛顿迭代（数值雅可比），见 P0 §1；
- 短路：check_short 预检 + LinAlgError 兜底 → {'ok': False, 'error': 'short'}，
  不崩溃（硬 Case #3 前置）；
- 浮空节点：stamp 图中不与地连通的节点集合（供 P4 标灰）。

返回结构（dict）::

    成功: {'ok': True, 'mode', 'ground', 'node_voltages': {正则节点: V},
           'element_results': {元件id: {'type','voltage','current','power',
                                        'resistance','floating'}},
           'floating_nodes': [...], 'iterations': int}
    失败: {'ok': False, 'error': 'short'|'no_converge', 'message': str}

电流符号约定：
- 电阻类：I = (V[a] - V[b]) / R，正方向 a → b；
- 电池：正 = 放电（电流从正极流出经外电路回到负极）。

导线（wire）电流不单独计算：导线在拓扑层已合并节点，其电流融入
节点 KCL，无法在不引入内部节点的情况下拆分（V1 约定，接 P4 展示层）。
"""

from __future__ import annotations

import logging
import time

import numpy as np

from core.mna import (
    DEFAULT_THERMAL_K,
    ShortCircuitError,
    assemble_matrix,
    bulb_resistance,
    check_short,
    floating_node_set,
    is_nonlinear_bulb,
)
from core.topology import (
    BATTERY,
    BELL,
    BULB,
    LED,
    MOTOR,
    R_OPEN,
    VOLTMETER,
    WIRE,
    build_graph,
    diode_r_on,
    diode_vf,
    is_diode,
)

# 状态判定阈值（需求书 4.4，可在测试中调整）
I_ON_MIN = 1e-6    # |I| > 1e-6 A
I_DARK_MAX = 1e-6    # 暗电流阈值（A）：|I|<此值直接归零（用户裁定）
P_ON_MIN = 0.01    # P > 0.01 W

log = logging.getLogger("voltlab.solver")

NEWTON_MAX_ITER = 30
NEWTON_TOL_K = 1e-6       # 温度收敛容差（K）
NEWTON_STEP_CLAMP = 50.0  # 单步最大温度修正（K），防发散


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def solve_circuit(elements: list, mode: str = "junior") -> dict:
    """求解电路，返回节点电压与各支路电流（结构见模块 docstring）。"""
    t0 = time.perf_counter()
    log.info("solve start: mode=%s elements=%d", mode, len(elements))
    topo = build_graph(elements, mode=mode)

    if check_short(topo, elements, mode):
        log.warning("solve aborted: short circuit detected")
        return {"ok": False, "error": "short",
                "message": "电源被纯导线（低阻支路）短接"}

    nonlinear = [e for e in elements if is_nonlinear_bulb(e)]
    has_diode = any(is_diode(e["type"]) for e in elements)
    try:
        if has_diode:
            result = _solve_diodes(topo, elements, mode, nonlinear)
        elif nonlinear:
            result = _solve_newton(topo, elements, mode, nonlinear)
        else:
            result = _solve_linear(topo, elements, mode)
    except ShortCircuitError as exc:
        log.warning("solve aborted: %s", exc)
        return {"ok": False, "error": "short", "message": str(exc)}
    except np.linalg.LinAlgError:
        log.warning("solve aborted: singular matrix")
        return {"ok": False, "error": "short",
                "message": "矩阵奇异（疑似短路或病态连接）"}
    dt_ms = (time.perf_counter() - t0) * 1000.0
    log.info("solve done in %.2fms ok=%s iterations=%s",
             dt_ms, result.get("ok"), result.get("iterations"))
    return result


# ---------------------------------------------------------------------------
# 线性求解
# ---------------------------------------------------------------------------

def _solve_linear(topo, elements, mode, r_overrides=None, extra_iters=1,
                  diode_states=None):
    """一次线性求解 + 结果提取。非线性迭代器复用本函数。"""
    a_mat, z_vec, meta = assemble_matrix(topo, elements, mode, r_overrides,
                                         diode_states)
    try:
        if a_mat.shape[0] == 0:  # 空电路：无数值解需求
            x = np.zeros(0)
        else:
            x = np.linalg.solve(a_mat, z_vec)
    except np.linalg.LinAlgError:
        raise
    result = _extract(topo, elements, mode, x, meta, r_overrides,
                      diode_states)
    result["iterations"] = extra_iters
    return result


def _solve_diodes(topo, elements, mode, nonlinear):
    """二极管/LED 状态迭代：导通=Vf 源+R_on，截止=开路，直到状态自洽。"""
    states = {e["id"]: False for e in elements if is_diode(e["type"])}
    for _ in range(20):
        try:
            if nonlinear:
                result = _solve_newton(topo, elements, mode, nonlinear,
                                       diode_states=states)
            else:
                result = _solve_linear(topo, elements, mode,
                                       diode_states=states)
        except ShortCircuitError as exc:
            return {"ok": False, "error": "short", "message": str(exc)}
        except np.linalg.LinAlgError:
            return {"ok": False, "error": "short",
                    "message": "矩阵奇异（疑似短路或病态连接）"}
        changed = False
        for el in elements:
            if not is_diode(el["type"]):
                continue
            er = result["element_results"][el["id"]]
            if er["diode_on"] and er["current"] < -1e-9:
                states[el["id"]] = False   # 反向电流 → 截止
                changed = True
            elif not er["diode_on"] and er["voltage"] > diode_vf(el):
                states[el["id"]] = True    # 正向电压超过 Vf → 导通
                changed = True
        if not changed:
            result["diode_states"] = dict(states)
            return result
    return {"ok": False, "error": "no_converge",
            "message": "二极管状态迭代未收敛"}


def _extract(topo, elements, mode, x, meta, r_overrides=None,
             diode_states=None):
    """从解向量提取节点电压 / 支路电流 / 功率。"""
    idx = meta["node_index"]
    battery_row = meta["battery_row"]

    node_voltages = {n: 0.0 for n in topo.nodes}
    for n, i in idx.items():
        if not n.startswith("__int__"):  # 内部节点不对外暴露
            node_voltages[n] = float(x[i])

    # 浮空检测：stamp 图（含 R_OPEN 支路与电池）中不连通到地的节点
    floating_nodes = _floating_nodes(topo, elements, mode)

    element_results = {}
    for el in elements:
        t = el["type"]
        if t == WIRE:
            element_results[el["id"]] = {
                "type": t, "voltage": 0.0, "current": None,
                "power": 0.0, "resistance": None, "floating": False,
            }
            continue
        a = topo.node_of[el["nodes"][0]]
        b = topo.node_of[el["nodes"][1]]
        v_ab = node_voltages[a] - node_voltages[b]
        is_float = a in floating_nodes and b in floating_nodes

        if t == BATTERY:
            i_br = -float(x[battery_row[el["id"]]])  # 正=放电
            r_int = 0.0 if mode == "junior" else \
                float(el["props"].get("r_internal", 0.0))
            element_results[el["id"]] = {
                "type": t, "voltage": v_ab, "current": i_br,
                "power": abs(v_ab * i_br), "resistance": None,
                "r_internal": r_int, "E": float(el["props"].get("E", 0.0)),
                "floating": is_float,
            }
            continue

        if is_diode(t):
            on = bool((diode_states or {}).get(el["id"]))
            if on:
                r_on = diode_r_on(el)
                i_br = (v_ab - diode_vf(el)) / r_on
                r_eff = r_on
            else:
                i_br, r_eff = 0.0, None
            element_results[el["id"]] = {
                "type": t, "voltage": v_ab, "current": i_br,
                "power": abs(v_ab * i_br), "resistance": r_eff,
                "diode_on": on, "floating": is_float,
                "brightness": min(1.0, abs(i_br) /
                                  float(el["props"].get("rated_current",
                                                        0.02)))
                if on else 0.0,
            }
            continue

        r_eff = None
        if t == VOLTMETER and mode == "junior":
            i_br = 0.0  # 理想电压表：读数取电压，电流为 0
        elif t == "rheostat":
            # nodes = [A, B, W]；两段电流按端子电压分别计算（段阻可为 0）
            from core.topology import rheostat_resistances
            na, nb, nw = (topo.node_of[n] for n in el["nodes"])
            r1, r2 = rheostat_resistances(el)
            i_aw = (node_voltages[na] - node_voltages[nw]) / r1 \
                if r1 > 1e-9 else None
            i_wb = (node_voltages[nw] - node_voltages[nb]) / r2 \
                if r2 > 1e-9 else None
            i_main = i_aw if i_aw is not None else \
                (i_wb if i_wb is not None else 0.0)
            p_el = 0.0
            if i_aw is not None:
                p_el += abs((node_voltages[na] - node_voltages[nw]) * i_aw)
            if i_wb is not None:
                p_el += abs((node_voltages[nw] - node_voltages[nb]) * i_wb)
            element_results[el["id"]] = {
                "type": t, "voltage": v_ab, "current": i_main,
                "power": p_el, "resistance": r1 + r2,
                "current_aw": i_aw, "current_wb": i_wb, "floating": is_float,
            }
            continue
        elif t in ("spdt", "sp3t"):
            from core.topology import spdt_active_nodes
            a, b = spdt_active_nodes(el)
            ra, rb = topo.node_of[a], topo.node_of[b]
            i_br = (node_voltages[ra] - node_voltages[rb]) / 1e-3  # R_CLOSED
            p_el = abs((node_voltages[ra] - node_voltages[rb]) * i_br)
            element_results[el["id"]] = {
                "type": t, "voltage": node_voltages[ra] - node_voltages[rb],
                "current": i_br, "power": p_el, "resistance": 1e-3,
                "floating": is_float,
            }
            continue
        elif t == BULB:
            # 非线性迭代中优先用当前迭代电阻
            r_eff = (r_overrides or {}).get(el["id"]) or bulb_resistance(el)
            i_br = v_ab / r_eff if r_eff else 0.0
        else:
            r_eff = _res_of(el, mode)
            i_br = v_ab / r_eff if r_eff else 0.0
        p_el = abs(v_ab * i_br)
        element_results[el["id"]] = {
            "type": t, "voltage": v_ab, "current": i_br,
            "power": p_el, "resistance": r_eff, "floating": is_float,
        }

    # 暗电流归零（用户裁定）：断开开关/电压表等经 R_OPEN/gmin 的残余
    # 微电流（如 3V/1e9Ω≈3e-9A）不向学生展示——|I| < 1e-6A 直接按 0
    # （与需求书"读数归零口径"一致），功率随之归零。
    for er in element_results.values():
        i = er.get("current")
        if isinstance(i, float) and 0.0 < abs(i) < I_DARK_MAX:
            er["current"] = 0.0
            er["power"] = 0.0
        for k in ("current_aw", "current_wb"):
            v = er.get(k)
            if isinstance(v, float) and 0.0 < abs(v) < I_DARK_MAX:
                er[k] = 0.0

    return {
        "ok": True,
        "mode": mode,
        "ground": topo.ground,
        "node_voltages": node_voltages,
        "element_results": element_results,
        "floating_nodes": sorted(floating_nodes),
    }


def _res_of(el: dict, mode: str):
    from core.topology import effective_resistance
    if el["type"] == BULB:
        return bulb_resistance(el)
    return effective_resistance(el, mode)


def _floating_nodes(topo, elements, mode):
    """浮空节点集合（判定逻辑在 mna 层，与 gmin 注入点保持同一实现）。"""
    return floating_node_set(topo, elements, mode)


# ---------------------------------------------------------------------------
# 非线性灯泡：牛顿迭代（数值雅可比）
# ---------------------------------------------------------------------------

def _solve_newton(topo, elements, mode, bulbs, diode_states=None) -> dict:
    """对每个非线性灯泡的温度 T 求 g(T) = (T - T0) - k·P(T) = 0。

    P(T) 通过整电路求解得到（R 随 T 变），g'(T) 用差分近似。
    """
    from core.mna import T0_KELVIN

    temp = {b["id"]: T0_KELVIN for b in bulbs}
    r_overrides = {bid: bulb_resistance(_by_id(elements, bid), temp[bid])
                   for bid in temp}
    iterations = 0
    for it in range(1, NEWTON_MAX_ITER + 1):
        try:
            result = _solve_linear(topo, elements, mode, r_overrides,
                                   extra_iters=it, diode_states=diode_states)
        except ShortCircuitError as exc:
            return {"ok": False, "error": "short", "message": str(exc)}
        except np.linalg.LinAlgError:
            return {"ok": False, "error": "short",
                    "message": "矩阵奇异（疑似短路或病态连接）"}
        iterations = it

        # 收敛判定
        max_g = 0.0
        step = {}
        for bid in temp:
            p = abs(result["element_results"][bid]["power"])
            g_val = (temp[bid] - T0_KELVIN) - DEFAULT_THERMAL_K * p
            max_g = max(max_g, abs(g_val))
        if max_g < NEWTON_TOL_K:
            result["iterations"] = iterations
            return result

        # 牛顿步：数值导数（每灯泡一次额外求解）
        new_temp = dict(temp)
        for bid in temp:
            h = 1.0
            trial = dict(r_overrides)
            trial[bid] = bulb_resistance(_by_id(elements, bid), temp[bid] + h)
            try:
                probe = _solve_linear(topo, elements, mode, trial,
                                      diode_states=diode_states)
            except (np.linalg.LinAlgError, ShortCircuitError):
                return {"ok": False, "error": "no_converge",
                        "message": "牛顿迭代中电路失稳"}
            p0 = abs(result["element_results"][bid]["power"])
            p1 = abs(probe["element_results"][bid]["power"])
            g0 = (temp[bid] - T0_KELVIN) - DEFAULT_THERMAL_K * p0
            g1 = (temp[bid] + h - T0_KELVIN) - DEFAULT_THERMAL_K * p1
            deriv = (g1 - g0) / h
            if abs(deriv) < 1e-12:
                dt = 0.0
            else:
                dt = g0 / deriv
            dt = max(-NEWTON_STEP_CLAMP, min(NEWTON_STEP_CLAMP, dt))
            new_temp[bid] = temp[bid] - dt
        temp = new_temp
        r_overrides = {bid: bulb_resistance(_by_id(elements, bid), temp[bid])
                       for bid in temp}

    return {"ok": False, "error": "no_converge",
            "message": "灯泡热平衡牛顿迭代 %d 次未收敛" % NEWTON_MAX_ITER}


def _by_id(elements, el_id):
    for el in elements:
        if el["id"] == el_id:
            return el
    raise KeyError(el_id)


# ---------------------------------------------------------------------------
# 状态判定（P4 calcVisualState 的内核侧预置）
# ---------------------------------------------------------------------------

def bulb_is_on(el_result: dict) -> bool:
    """需求书 4.4：|I| > 1e-6 且 P > 0.01W → 亮。"""
    if not el_result or el_result.get("current") is None:
        return False
    return abs(el_result["current"]) > I_ON_MIN and \
        el_result["power"] > P_ON_MIN


# ---------------------------------------------------------------------------
# P4：状态判定（需求书 4.4 —— 求解后计算，供渲染层使用）
# ---------------------------------------------------------------------------

def calc_visual_state(solve_result: dict) -> dict:
    """把求解结果翻译成渲染层可用的视觉状态。

    返回::

        {'ok': True, 'error': None,
         'elements': {元件id: {'state': 'on'|'off'|'normal',
                               'floating': bool,
                               'readings': {'U': V, 'I': A}}}}
        短路/超时: {'ok': False, 'error': 'short'|'timeout', ...}

    规则（需求书 4.4）：
    - |I| > 1e-6 且 P > 0.01W → 灯亮；I == 0 → 灭；
    - 悬空元件 → floating（UI 标灰）；
    - 纯导线短接电源 → ok=False, error='short'（UI 红字），不崩溃。
    """
    out = {
        "ok": bool(solve_result.get("ok")),
        "error": solve_result.get("error"),
        "message": solve_result.get("message"),
        "elements": {},
    }
    if not out["ok"]:
        log.warning("visual state: circuit invalid (%s)", out["error"])
        return out
    for eid, er in solve_result["element_results"].items():
        t = er["type"]
        state = "normal"
        if t == BULB:
            state = "on" if bulb_is_on(er) else "off"
        elif t in (BELL, MOTOR):
            state = "on" if abs(er["current"]) > I_ON_MIN else "off"
        elif is_diode(t):
            state = "on" if er.get("diode_on") else "off"
        entry = {
            "state": state,
            "floating": bool(er.get("floating")),
            "brightness": float(er.get("brightness", 0.0)),
            "readings": {"U": er["voltage"], "I": er["current"]},
        }
        out["elements"][eid] = entry
    return out


def terminal_check_result(result: dict) -> list:
    """别名：路端电压回归断言（语义同 check_terminal_voltage）。"""
    return check_terminal_voltage(result)


# ---------------------------------------------------------------------------
# P3：高中路端电压回归断言（红线：电压表读数绝不能等于 E）
# ---------------------------------------------------------------------------

def check_terminal_voltage(result: dict, eps: float = 1e-6) -> list:
    """校验"放电时路端电压 < E"。

    仅对 mode=senior 且 r_internal > 0 的电池生效；
    放电电流 |I| > I_ON_MIN 而路端电压 >= E - eps 视为违规。

    返回违规电池 id 列表（空列表 = 通过）。同时写 warning 日志。
    """
    if not result.get("ok"):
        return []
    bad = []
    for eid, er in result["element_results"].items():
        if er.get("type") != BATTERY or er.get("r_internal", 0.0) <= 0:
            continue
        if abs(er["current"]) > I_ON_MIN and \
                er["voltage"] >= er["E"] - eps:
            bad.append(eid)
            log.warning("terminal voltage violation: %s V_term=%.6f >= E=%.6f",
                        eid, er["voltage"], er["E"])
    return bad
