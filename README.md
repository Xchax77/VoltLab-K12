# VoltLab — K12 电路教学仿真软件

电路图绘制 + 电路状态仿真，用于初中 / 高中物理课堂演示与课后练习。

- 界面：PyQt5（`QGraphicsView` / `QGraphicsScene`）
- 求解内核：纯 Python + NumPy（改进节点法 MNA），可离线运行
- 详细需求：见《VoltLab_需求书与开发指南》

## 目录结构

```
VoltLab/
├── core/                  # 求解内核
│   ├── topology.py        # 拓扑分析（P1）
│   ├── mna.py             # MNA 矩阵组装（P2）
│   └── solver.py          # 求解与看门狗（P2/P3）
├── ui/                    # PyQt5 界面（P4/P5）
├── tests/                 # 测试文件（必须保留）
├── archive/               # 开发过程存档
│   ├── CHANGELOG.md       # 变更记录
│   ├── INDEX.md           # 版本与文件索引
│   └── P0_MNA算法说明.md  # P0 算法交付物
└── README.md
```

## 运行环境

- Python 3.x + NumPy + PyQt5

## 启动

```
python main.py
```

操作：元件箱点击 → 画布放置（自动吸附网格）；「导线」工具点击两个格点连线（8 向）；单击开关切换通断；双击电池改参数；右键删除元件。

## 开发阶段（SOP：一次只交付一个 Phase）

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | MNA 矩阵填充算法说明 | ✅ 完成 |
| P1 | 拓扑分析 `buildGraph` | ✅ 完成 |
| P2 | 初中线性求解（NumPy，含线性/非线性灯泡） | ✅ 完成 |
| P3 | 高中内阻扩展 + 看门狗 | ✅ 完成 |
| P4 | 状态判定 + QGraphicsScene 渲染 | ✅ 完成 |
| P5 | 应用外壳（主窗口 / About / 菜单） | ✅ 完成（V1 收官） |

## 开源声明

数值求解算法思路参考自 CircuitJS1 (Falstad, GPLv2) 与 PhET Circuit Construction Kit (AGPL)。
本软件参考了上述项目的公开算法思路，求解内核在本项目内独立编写。详见程序内 `Help > About`。
