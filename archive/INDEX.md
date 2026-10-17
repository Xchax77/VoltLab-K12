# VoltLab 文件索引

> 每次变更必须同步更新本索引与 CHANGELOG.md（需求书 10.3 红线）。

## 版本：v0.1.13（2026-10-17）— V1.13 导线 T 接/选中删除

| 文件 | 说明 | 状态 | 变更 Phase |
|---|---|---|---|
| `README.md` | 项目说明与阶段进度 | 新增 | P0 |
| `archive/P0_MNA算法说明.md` | MNA 矩阵填充算法说明（P0 交付物） | 新增 | P0 |
| `archive/需求补充_网格化.md` | 元件网格化 + 导线 8 向化需求补充（用户裁定） | ✅ 新增 | P1.1 |
| `archive/需求补充_UI风格.md` | 护眼绿底色 + 学生友好 UI 需求补充（用户裁定） | ✅ 新增 | P2.1 |
| `archive/CHANGELOG.md` | 变更记录 | 持续维护 | P0–P2 |
| `archive/INDEX.md` | 本文件 | 持续维护 | P0–P2 |
| `core/__init__.py` | 内核包 | 新增 | P0 |
| `core/topology.py` | 拓扑分析：`build_graph`（导线合并+变阻器0Ω段并节点+邻接表+二值开关）；`spdt_active_index` | ✅ 13 测试通过 | P1/V1.8 |
| `core/mna.py` | MNA 组装 + 短路预检 + 灯泡模型 + 浮空判定 + 内阻内部节点方案（v0.1.6a 修 SPDT 正则节点） | ✅ 持续维护 | P2/P3 |
| `core/solver.py` | `solve_circuit`（NumPy 求解 + 牛顿迭代 + 支路电流 + 路端电压断言） | ✅ 持续维护 | P2/P3 |
| `core/watchdog.py` | 看门狗：QThread 求解线程 + QTimer 200ms 预警/800ms 强杀 | ✅ 新增 | P3 |
| `core/logging_setup.py` | 软件日志（logs/ 按天文件，UTF-8，不写 C 盘） | ✅ 新增 | P3 |
| `ui/__init__.py` | UI 包 | 新增 | P0 |
| `ui/main_window.py` | 主窗口（菜单栏/元件箱+符号小图标/悬浮缩放滑轨/参数调整栏/导出白底PNG/防抖求解） | ✅ 完成 | P4/P5/V1.9 |
| `archive/界面预览_正常回路.png` | 界面预览留档（硬 Case #7） | ✅ 新增 | P5 |
| `archive/界面预览_短接警告.png` | 短接红字提示留档（硬 Case #8） | ✅ 新增 | P5 |
| `ui/circuit_scene.py` | 画布场景（网格吸附 + 8 向导线 + 护眼绿 + 学生友好交互） | ✅ 新增 | P4 |
| `ui/graph_dialog.py` | 图像分析窗口（变阻器扫描曲线，横轴/多纵轴可选，自绘坐标轴） | ✅ 新增 | V1.5 |
| `ui/symbol_editor.py` | 元件图像编辑器（线条/圆/方，粗细长短可调，存 symbols.json） | ✅ 新增 | V1.6 |
| `symbols.json` | 用户自定义元件符号（编辑器保存产物，可删除恢复内置） | 按需生成 | V1.6 |
| `main.py` | 启动入口 `python main.py` | ✅ 新增 | P4 |
| `tests/__init__.py` | 测试包 | 新增 | P0 |
| `tests/test_topology.py` | 拓扑测试（硬 Case：三开关串联断开） | ✅ 13 项通过 | P1 |
| `tests/test_mna.py` | MNA/求解测试（含硬 Case #1、#3 数据驱动 + 掷开关回归） | ✅ 16 项通过 | P2/V0.1.6a |
| `tests/test_solver.py` | 高中扩展 + 看门狗 + 日志测试（HC2 数据驱动） | ✅ 8 项通过 | P3 |
| `tests/test_visual.py` | 状态判定 + 8 向路径 + 场景冒烟 | ✅ 14 项通过 | P4/V1.6 |
| `tests/test_features.py` | V1.5/V1.7 新功能（新元件/撤销/插入/图像/缩放/符号图标/全类型渲染回归） | ✅ 20 项通过 | V1.5/V1.7 |
| `tests/test_cases.json` | 验收硬 Case 数据文件 | ✅ 6 case | P2/P3 |

## Phase 进度

- [x] P0 算法说明
- [x] P1 拓扑 `buildGraph(elements)`（纯 Python，不接 PyQt5；硬 Case 通过）
- [x] P2 初中线性求解（硬 Case #1/#3 通过；非线性灯泡牛顿迭代同步交付）
- [x] P3 高中内阻扩展 + 看门狗（硬 Case #2 通过；软件日志上线）
- [x] P4 状态判定 + 场景渲染（网格化 + 8 向导线 + 护眼绿 + 学生友好落地）
- [x] P5 应用外壳（菜单栏 / About 开源声明 / 置灰导入习题 / 界面预览留档）—— **V1 完成**
