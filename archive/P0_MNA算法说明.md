# P0 — MNA 矩阵填充算法说明（参考 CircuitJS1 / PhET 公开思路实现）

> 本文档为 P0 阶段交付物：**仅算法说明与伪代码，不迁移任何 JS 代码**。
> 求解内核在本项目内独立编写，CircuitJS1 / PhET 仅作思路参考（见需求书第六章）。
> 本阶段**跳过**灯泡非线性 R(T)（P3/P4 之后再做牛顿迭代）。

---

## 1. MNA（改进节点法）基本思想

未知量分两类：

| 类别 | 数量 | 含义 |
|---|---|---|
| 节点电压 `V[i]` | `N-1`（去掉接地节点） | 每个非地节点的电位 |
| 电压源支路电流 `I[k]` | `M`（理想电压源个数） | 流过每个理想电压源的电流 |

矩阵方程规模：`A · x = z`，其中 `A` 是 `(N-1+M) × (N-1+M)` 方阵，`x = [V ; I]`，`z = [电流注入 ; 电压源约束]`。

这就是"矩阵维度正确"的含义：**每接入一个理想电压源（电池），矩阵整体扩 1 阶**。

## 2. CircuitJS1 三要素的思路总结（伪代码重述）

### 2.1 stampResistor — 电阻/导线/开关/电表统一入口

电路中凡是"二端、电流与两端电压差成正比"的元件，都归结为在两点之间接一个电导 `g = 1/R`：

```
function stampResistor(A, nodeA, nodeB, R):
    if R == 0:  R = R_WIRE_MIN      # 数值下限，防止除零（见 §4）
    if R >= R_OPEN: return           # 断开开关/理想电压表：不stamp，等于开路
    g = 1 / R
    if nodeA != GND: A[nodeA][nodeA] += g
    if nodeB != GND: A[nodeB][nodeB] += g
    if nodeA != GND and nodeB != GND:
        A[nodeA][nodeB] -= g
        A[nodeB][nodeA] -= g
```

要点：接地节点（编号 0）对应的行列**不进入矩阵**（其电位恒为 0）。

### 2.2 stampVoltageSource — 理想电压源（电池）

理想电压源不能当电阻 stamp（其电流不由压差决定），需要**附加一列 + 一行**：

```
function stampVoltageSource(A, z, nodePos, nodeNeg, E, colIdx):
    # colIdx = N-1 + k，为本电源分配的电流未知量下标
    # 行约束：V(pos) - V(neg) = E
    if nodePos != GND: A[row_k][nodePos] += 1
    if nodeNeg != GND: A[row_k][nodeNeg] -= 1
    z[row_k] = E
    # 列贡献：电流 Ik 从正极流出、负极流入（KCL）
    if nodePos != GND: A[nodePos][colIdx] += 1
    if nodeNeg != GND: A[nodeNeg][colIdx] += -1
```

### 2.3 doStep — 每次仿真的主循环

```
function doStep(elements):
    (N, M) = countNodesAndSources(elements)      # 拓扑阶段(P1)给出
    A = zeros(N-1+M, N-1+M); z = zeros(N-1+M)
    k = 0
    for el in elements:
        switch el.type:
            battery:  # 有内阻 r > 0 → 先拆成"理想源 + 串联电阻"（见 §3.2）
                      stampVoltageSource(A, z, el.pos, el.neg, el.E, N-1+k); k += 1
                      if el.r_internal > 0: stampResistor(A, el.internalNode, el.neg, el.r_internal)
            resistor: stampResistor(A, el.a, el.b, el.R)
            bulb(junior): stampResistor(A, el.a, el.b, el.R_rated)   # 初中=定值电阻
            switch:   stampResistor(A, el.a, el.b, el.closed ? R_CLOSED : R_OPEN)
            ammeter:  stampResistor(A, el.a, el.b, r_ammeter)        # 初中理想=0→R_CLOSED
            voltmeter: stampResistor(A, el.a, el.b, r_voltmeter)     # 初中理想=∞→不stamp
    x = solve(A, z)                 # numpy.linalg.solve
    提取 V = x[0:N-1]，I_source = x[N-1:]
    逐元件计算支路电流（见 §3.3），写入求解结果
```

初中模式的"理想电表"是同一 stamp 入口的两个极端参数，不需要单独代码路径。

## 3. 元件 → 矩阵的映射（V1 内核）

### 3.1 元件归类

| 元件 | 初中模式 | 高中模式 |
|---|---|---|
| 电池 | 理想源 E（r=0 不 stamp 电阻） | E + 串联 r_internal |
| 定值电阻 | stampResistor | 同左 |
| 小灯泡 | 定值电阻 R_rated | R(T) 非线性，牛顿迭代（本阶段跳过） |
| 开关 | 二值电阻：闭合 R_CLOSED / 断开 R_OPEN | 同左 |
| 电压表 | ∞ → 不 stamp（开路） | 10kΩ 电阻 |
| 电流表 | 0 → R_CLOSED | 0.1Ω 电阻 |
| 导线 | 合并节点（P1 拓扑阶段处理）后不再 stamp | 同左 |

### 3.2 电源内阻（高中）的 stamp 方案

带内阻电源 = 理想电压源 E 与电阻 r 串联。两种实现：

- 方案 A（推荐）：引入**内部节点** `internal`，理想源 stamp 在 `pos–internal` 之间，电阻 stamp 在 `internal–neg` 之间。矩阵多 1 阶，逻辑最清晰。
- 方案 B：无内部节点的合并 stamp（源列中混入电导项）。省 1 阶但公式易错。

**P3 采用方案 A**，本阶段仅在伪代码中预留。

### 3.3 求解后支路电流反推

- 电阻类：`I = (V[a] - V[b]) / R`（开路元件 I=0）
- 电池：`I = x[N-1+k]`（自己那一行的附加未知量），正方向：内部从负极流向正极
- 电流表：按其等值电阻同电阻类公式
- 电压表（非理想）：按其 10kΩ 计算微弱电流

### 3.4 数值哨兵值（P1/P2 落地用）

| 哨兵 | 取值 | 用途 |
|---|---|---|
| `R_CLOSED` | `1e-3 Ω`（可调） | 闭合开关 / 理想电流表 / 导线残差 |
| `R_OPEN` | `1e9 Ω` | 断开开关（等效不 stamp，仅保留语义） |
| `R_WIRE_MIN` | `1e-9 Ω` | 防止 `1/R` 除零的硬下限 |

> 需求书说开关闭合"0Ω"、断开"∞Ω"，这是**教学语义**；实现层必须用有限哨兵值，否则矩阵奇异/溢出。

## 4. 奇异矩阵与短路判定（为硬 Case #3 铺垫）

- 纯导线直接短接理想电源两端 → 方程 `V+ - V- = E` 与 `V+ = V-`（0Ω 支路把两点焊死）矛盾，`numpy.linalg.solve` 抛出 `LinAlgError`（矩阵奇异）。
- 处理约定：捕获后**不崩溃**，返回 `{error: 'short'}`，由 UI 层红字提示（P4 接入）。
- 兜底：solve 前可检查 `cond(A)` 或求解后校验残差 `‖Ax-z‖`，超阈值同样判为无有效解。

## 5. P0 验收对照（矩阵维度）

| 电路 | 节点数 N（含地） | 电压源数 M | 矩阵维度 |
|---|---|---|---|
| 电池+电阻 单回路 | 3 | 1 | 3×3 |
| 电池+两电阻 串联 | 4 | 1 | 4×4 |
| 高中：电池带内阻（方案 A） | N+1 | 1 | (N+1-1+1) 阶 |

P1 实现拓扑后，将用单元测试断言上述维度与节点合并结果。
