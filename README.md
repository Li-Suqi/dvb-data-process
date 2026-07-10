# DVB ATP Pipeline / DVB 到站时间预测流水线

## English

Arrival Time Prediction (ATP) preprocessing and analysis pipeline for the Dresden DVB public transport network.

The repository converts raw ITCS/AVL vehicle telemetry and timetable data into a stop-event table for delay analysis and next-stop travel-time modelling. It also includes data-quality notebooks, bus/tram-specific diagnostics, stop geometry, traffic-signal experiments, and a dwell-time ablation study.

Detailed documentation:

| Language | File |
|---|---|
| English | `DVB_ATP_Pipeline_EN.md` |
| Chinese | `DVB_ATP_Pipeline_CN.md` |

Main components:

| Path | Purpose |
|---|---|
| `pipeline/` | Reusable Polars-based modules for stop detection, timetable matching, feature building, and external joins |
| `notebooks/` | End-to-end pipeline notebooks and extension notebooks |
| `quality-analysis/` | Full-network quality diagnostics and delay analysis |
| `bus-quality-analysis/` | Bus-only quality diagnostics |
| `tram-quality-analysis/` | Tram-only quality diagnostics |
| `ablation/experiment_a/` | LightGBM computed-vs-fixed dwell-time ablation |
| `data/processed/` | Generated core and extension parquet outputs |

Current local core output:

| Metric | Value |
|---|---:|
| File | `data/processed/core_stop_events.parquet` |
| Rows | 976,393 |
| Columns | 18 |
| Vehicles | 360 |
| Trips | 40,146 |
| Unique stops | 1,724 |
| Mean calculated delay | 157.24 s |

Quick start:

```bash
pip install polars numpy pandas matplotlib scikit-learn lightgbm jupyter
```

Then run:

1. `notebooks/01_pipeline.ipynb` to build the core stop-event table.
2. `notebooks/02_extensions.ipynb` to join weather and vehicle information.
3. `notebooks/04_pipeline_with_ort_signal.ipynb` for the signal-aware variant.

Note: the currently committed/generated processed files are not fully synchronized. `core_stop_events.parquet` has 976,393 rows, while the weather and vehicle extension files have 994,117 rows. Re-run the extension notebook after regenerating the core table when consistent row counts are required.

---

## 中文

本项目是面向德累斯顿 DVB 公共交通网络的到站时间预测（Arrival Time Prediction, ATP）预处理与分析流水线。

仓库将原始 ITCS/AVL 车辆遥测数据和时刻表数据转换为站级停站事件表，用于延误分析和下一站行驶时间建模。同时包含数据质量分析、公交/有轨电车分组诊断、站点地理信息、交通信号实验，以及 dwell time（驻留时间）消融实验。

详细文档：

| 语言 | 文件 |
|---|---|
| 英文 | `DVB_ATP_Pipeline_EN.md` |
| 中文 | `DVB_ATP_Pipeline_CN.md` |

主要目录：

| 路径 | 作用 |
|---|---|
| `pipeline/` | 基于 Polars 的可复用模块：停站检测、时刻表匹配、特征构建、外部数据关联 |
| `notebooks/` | 端到端流水线和扩展构建 notebooks |
| `quality-analysis/` | 全网络质量诊断与延误分析 |
| `bus-quality-analysis/` | 公交子集质量诊断 |
| `tram-quality-analysis/` | 有轨电车子集质量诊断 |
| `ablation/experiment_a/` | LightGBM computed-vs-fixed dwell time 消融实验 |
| `data/processed/` | 已生成的核心表和扩展 parquet 产物 |

当前本地核心产物：

| 指标 | 数值 |
|---|---:|
| 文件 | `data/processed/core_stop_events.parquet` |
| 行数 | 976,393 |
| 列数 | 18 |
| 车辆数 | 360 |
| 行程数 | 40,146 |
| 唯一站点数 | 1,724 |
| 平均计算延误 | 157.24 秒 |

快速开始：

```bash
pip install polars numpy pandas matplotlib scikit-learn lightgbm jupyter
```

然后运行：

1. `notebooks/01_pipeline.ipynb` 生成核心停站事件表。
2. `notebooks/02_extensions.ipynb` 关联天气和车辆信息。
3. `notebooks/04_pipeline_with_ort_signal.ipynb` 生成带交通信号信息的变体。

注意：当前已生成的 processed 文件并非完全同步。`core_stop_events.parquet` 为 976,393 行，而 weather 和 vehicle 扩展文件为 994,117 行。如果需要行数一致的扩展产物，请在重新生成核心表后重新运行扩展 notebook。
