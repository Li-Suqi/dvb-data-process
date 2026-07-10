# DVB ATP Pipeline - 中文 README

本项目是面向德累斯顿 DVB 公共交通网络的到站时间预测（Arrival Time Prediction, ATP）数据流水线。

项目将原始 ITCS/AVL 车辆遥测数据和时刻表数据转换为站级停站事件表，用于后续延误分析、行驶时间建模和特征实验。仓库中还包含数据质量分析、公交/有轨电车分组诊断、站点地理信息提取、交通信号实验，以及 dwell time（驻留时间）消融实验。

双语入口见 `README.md`，英文详细版见 `DVB_ATP_Pipeline_EN.md`。

---

## 1. 项目内容

```text
dvb_atp_pipeline/
├── README.md                         # 双语项目入口
├── DVB_ATP_Pipeline_EN.md            # 英文详细文档
├── DVB_ATP_Pipeline_CN.md            # 中文详细文档
├── pipeline/                         # 可复用 Python 流水线模块
│   ├── detector.py                   # 基于 distanz 下降和门状态的停站事件检测
│   ├── timetable.py                  # 时刻表展开、匹配、延误/驻留/行驶时间计算
│   ├── extensions.py                 # 天气、车辆、特殊活动关联
│   ├── feature_builder.py            # 衍生二值特征
│   ├── rescue.py                     # 停站转换恢复/对齐辅助逻辑
│   ├── expand_timetable.py           # 早期独立时刻表展开脚本
│   └── match_arrivals.py             # 早期独立到站匹配脚本
├── notebooks/
│   ├── 01_pipeline.ipynb             # 核心停站事件表构建
│   ├── 02_extensions.ipynb           # 天气、车辆、事件扩展
│   ├── 03_stop_geo.ipynb             # 站点地理信息提取
│   ├── 04_pipeline_with_ort_signal.ipynb
│   └── explore_vehicle_types.ipynb
├── data_preparation/
│   └── 03_regular_lines.ipynb        # regular lines 预处理及公交/有轨电车拆分
├── quality-analysis/                 # 全网络质量分析和延误诊断
├── bus-quality-analysis/             # 公交子集质量分析
├── tram-quality-analysis/            # 有轨电车子集质量分析
├── ablation/
│   ├── computed-vs-fixed-dwell-time.ipynb
│   └── experiment_a/                 # LightGBM dwell-time 消融实验
└── data/
    ├── regular_linie_week.csv
    ├── regular_lines_0728_0803.parquet
    ├── bus_2025-07-28_2025-08-03.parquet
    ├── tram_2025-07-28_2025-08-03.parquet
    ├── timetable_trips_2025_07_22.csv
    ├── vehicle_data_2025_07_22.csv
    ├── stop_geometry.csv / stop_geometry.parquet
    ├── external/
    │   ├── weather/
    │   ├── vehicle/
    │   └── events/
    ├── processed/
    └── processed_with_signal_info/
```

---

## 2. 数据资产

当前本地数据文件包括：

| 文件 | 作用 | 当前本地观测规模 |
|---|---|---|
| `data/regular_linie_week.csv` | 早期 notebooks 使用的一周原始车辆遥测 CSV | 大型 CSV |
| `data/regular_lines_0728_0803.parquet` | regular lines 原始遥测 Parquet | 10,708,589 行，25 列 |
| `data/bus_2025-07-28_2025-08-03.parquet` | 公交子集 | 5,728,505 行，25 列 |
| `data/tram_2025-07-28_2025-08-03.parquet` | 有轨电车子集 | 4,980,084 行，25 列 |
| `data/timetable_trips_2025_07_22.csv` | 原始时刻表，`segmente` 字段包含 JSON 站序列 | CSV |
| `data/vehicle_data_2025_07_22.csv` | 原始车辆元数据 | CSV |
| `data/stop_geometry.parquet` | 站点坐标与名称 | 3,583 行，6 列 |
| `data/external/weather/weather_dresden.parquet` | 清洗后的 DWD 天气观测 | Parquet |
| `data/external/vehicle/vehicle_info.parquet` | 车辆查找表匹配结果 | Parquet |
| `data/external/events/special_events.csv` | 人工整理的特殊活动日期 | CSV |

已生成产物：

| 文件 | 说明 | 当前本地观测规模 |
|---|---|---|
| `data/processed/core_stop_events.parquet` | 主核心停站事件表 | 976,393 行，18 列 |
| `data/processed/core_stop_events_with_weather.parquet` | 关联日级天气后的核心表 | 994,117 行，27 列 |
| `data/processed/core_stop_events_with_vehicle.parquet` | 关联车辆属性后的核心表 | 994,117 行，24 列 |
| `data/processed_with_signal_info/core_stop_events.parquet` | 带交通信号信息的核心表变体 | 950,531 行，18 列 |

注意：当前 processed 文件并非同一次执行生成。核心表为 976,393 行，而 weather/vehicle 扩展表为 994,117 行。如果需要完全同步的扩展产物，请在重新生成核心表后再运行 `notebooks/02_extensions.ipynb`。

---

## 3. 流水线流程

```text
原始车辆遥测                              原始时刻表
regular_linie_week.csv / parquet          timetable_trips_2025_07_22.csv
        │                                           │
        ▼                                           ▼
detect_stop_events()                       expand_timetable()
pipeline/detector.py                       pipeline/timetable.py
        │                                           │
        └────────────────────┬──────────────────────┘
                             ▼
                  match_and_compute_delay()
                  pipeline/timetable.py
                             │
                             ▼
                   add_binary_features()
                  pipeline/feature_builder.py
                             │
                             ▼
              data/processed/core_stop_events.parquet
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   join_weather()     join_vehicle_info()   join_special_events()
   extensions.py      extensions.py         extensions.py
```

主流程：

1. 根据累计距离 `distanz` 的下降和门状态转换检测停站事件。
2. 将时刻表中的 JSON 站序列展开为每趟车每站一行的计划到站表。
3. 按 `fahrt_id`、站点编号和服务日匹配实际停站与计划到站。
4. 计算计算延误、自报延误、驻留时间和站间行驶时间。
5. 添加时间类二值特征。
6. 可选关联天气、车辆、特殊活动、站点地理信息或交通信号属性。

---

## 4. 核心模块

### `pipeline/detector.py`

`detect_stop_events(raw_df)` 从原始车辆遥测中检测停站事件。

必要输入字段：

| 字段 | 含义 |
|---|---|
| `fzg_id` | 车辆 ID |
| `tst_iso` | 时间戳 |
| `distanz` | 线路段累计距离 |
| `tuerkriterium` | 门状态/开门判据 |
| `linie` | 线路号 |

算法概要：

1. 按 `(fzg_id, tst_iso)` 排序。
2. 按车辆计算 `delta = distanz[t] - distanz[t-1]`。
3. 将 `delta < -100 m` 视为站点边界处的距离回落。
4. 在每个回落点附近构建 3 到 10 行的自适应窗口。
5. 在窗口内统计开门和关门状态转换。
6. 将事件分类为：
   - `normal`：正常停站。
   - `no_door`：距离回落附近没有门动作。
   - `multi_door`：同一站点内出现多次有效开门。

输出字段包含 `arrival_time`、`departure_time`、`stop_status`、门动作计数、距离窗口诊断信息和原始 drop 位置。

### `pipeline/timetable.py`

`expand_timetable(raw_tt)` 解析时刻表的 `segmente` JSON 字段，生成每趟车每站一行的计划到站表。

`match_and_compute_delay(stop_events, raw_df, timetable)` 将检测出的实际停站事件与计划到站匹配，并计算建模字段。

关键匹配逻辑：

| 步骤 | 逻辑 |
|---|---|
| 停站元数据 | 从原始 drop 行读取 `fahrt_id`、`ort_nr_start`、`lage`、`besetztgrad` |
| 服务日 | 使用 `(time - 4h).date()`，将午夜后继续运行的班次归入同一运营日 |
| 重复站点匹配 | 保留实际到站与计划到站时间差绝对值最小的候选 |
| 碰撞去重 | 同一车辆/行程/站点/服务日出现多条记录时，优先保留 `normal`，其次 `multi_door`，最后 `no_door` |
| 跨日异常过滤 | 删除计算延误极大且与自报延误严重不一致的疑似跨日错配 |
| 驻留时间 | `departure_time - arrival_time`；`no_door` 行使用 `-1.0` 作为哨兵值 |
| 行驶时间 | 同一车辆当前到站时间减上一站离站时间 |

### `pipeline/feature_builder.py`

`add_binary_features(df)` 添加：

| 特征 | 定义 |
|---|---|
| `is_peak_hour` | 07:00-09:00 或 16:00-19:00 为 1 |
| `is_workday` | 周一至周五为 1；未排除法定节假日 |
| `has_traffic_signal` | 核心流水线中的占位字段；带信号信息的结果位于 `data/processed_with_signal_info/` |

### `pipeline/extensions.py`

| 函数 | 用途 |
|---|---|
| `load_weather(raw_path)` | 加载并清洗 DWD 日级天气数据，将 `-999` 替换为空值 |
| `join_weather(core, weather)` | 按 `arrival_time.date()` 关联天气 |
| `load_vehicle_info(raw_vehicle_path, lookup_path)` | 将原始车辆编号与查找表编号范围匹配，并计算容量 |
| `join_vehicle_info(core, vehicle_info)` | 按 `fzg_id` 关联车辆属性 |
| `load_special_events(events_path)` | 加载特殊活动日期和位置 |
| `join_special_events(core, events)` | 按日期关联活动信息 |

---

## 5. 核心表字段

`data/processed/core_stop_events.parquet` 当前有 976,393 行、18 列。

| 字段 | 含义 |
|---|---|
| `fzg_id` | 车辆 ID |
| `drop_row_idx` | 检测到距离回落的车辆内原始行索引 |
| `arrival_time` | 实际到站时间 |
| `departure_time` | 实际离站时间 |
| `linie` | 线路号 |
| `fahrt_id` | 行程 ID |
| `ort_nr_start` | 站点编号 |
| `stop_index` | 展开时刻表中的站序号 |
| `stop_status` | `normal`、`no_door` 或 `multi_door` |
| `scheduled_arrival_time` | 计划到站时间 |
| `delay_calculated_sec` | 实际到站减计划到站，单位秒 |
| `delay_recorded_sec` | ITCS `lage` 字段自报延误，单位秒 |
| `dwell_time` | 离站减到站，单位秒；`no_door` 为 `-1.0` |
| `travel_time` | 同一车辆当前到站减上一站离站 |
| `besetztgrad` | 载客率等级 |
| `is_peak_hour` | 高峰时段标记 |
| `is_workday` | 工作日标记 |
| `has_traffic_signal` | 核心表中的占位字段 |

当前本地核心表统计：

| 指标 | 数值 |
|---|---:|
| 行数 | 976,393 |
| 车辆数 | 360 |
| 行程数 | 40,146 |
| 唯一站点数 | 1,724 |
| 到站时间范围 | 2025-07-27 22:00:06 UTC 至 2025-08-03 21:59:59 UTC |
| 平均计算延误 | 157.24 秒 |
| 高峰时段事件数 | 254,756 |
| 工作日事件数 | 584,810 |
| `normal` 事件 | 776,517 |
| `no_door` 事件 | 197,504 |
| `multi_door` 事件 | 2,372 |

---

## 6. 运行方式

仓库当前没有提交环境文件。根据代码中的 import 推断，建议使用 Python 3.11+，并至少安装：

```bash
pip install polars numpy pandas matplotlib scikit-learn lightgbm jupyter
```

部分地图或 notebook-only 单元可能还需要额外包，取决于具体执行的分析 notebook。

通过 notebooks 运行核心流程：

1. `notebooks/01_pipeline.ipynb` - 生成 `data/processed/core_stop_events.parquet`。
2. `notebooks/02_extensions.ipynb` - 生成 weather 和 vehicle 扩展产物。
3. `notebooks/03_stop_geo.ipynb` - 生成站点地理信息。
4. `notebooks/04_pipeline_with_ort_signal.ipynb` - 生成带交通信号信息的变体。

在 Python 中直接调用可复用 API：

```python
import polars as pl

from pipeline.detector import detect_stop_events
from pipeline.timetable import expand_timetable, match_and_compute_delay
from pipeline.feature_builder import add_binary_features
from pipeline.extensions import (
    load_weather,
    join_weather,
    load_vehicle_info,
    join_vehicle_info,
)

raw_df = (
    pl.read_parquet("data/regular_lines_0728_0803.parquet")
    .with_columns(
        pl.col("tst_iso").str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z")
    )
    .sort(["fzg_id", "tst_iso"])
    .with_columns(pl.int_range(pl.len()).over("fzg_id").alias("row_idx"))
)

raw_tt = pl.read_csv("data/timetable_trips_2025_07_22.csv", infer_schema_length=10000)

stop_events = detect_stop_events(raw_df)
timetable = expand_timetable(raw_tt)
matched = match_and_compute_delay(stop_events, raw_df, timetable)
core = add_binary_features(matched)

weather = load_weather("data/external/weather/produkt_klima_tag_20241129_20260601_01048.txt")
core_weather = join_weather(core, weather)

vehicle_info = load_vehicle_info(
    "data/vehicle_data_2025_07_22.csv",
    "data/external/vehicle/dvb_fahrzeug_info.csv",
)
core_vehicle = join_vehicle_info(core, vehicle_info)
```

---

## 7. 质量分析

质量分析 notebooks 分为全网络、公交子集和有轨电车子集三个目录。

常见分析：

| Notebook 组 | 用途 |
|---|---|
| `01_redundant.ipynb` | 硬重复和软重复检测 |
| `02_missing.ipynb` | 字段缺失与行程内时间戳缺口 |
| `03_noisy.ipynb` / `03_noisy_eda.ipynb` | GPS 零值、距离尖峰、小幅回落、极端延误、不可靠状态 |
| `04_window_size.ipynb` | 停站检测窗口敏感性 |
| `05_speed_analysis.ipynb` / `06_gps_speed.ipynb` | 基于 `distanz` 和 GPS 的速度合理性检查 |
| `07_drop_timing.ipynb` | 距离回落与门动作之间的时间关系 |
| `08_dwell_time_analysis.ipynb` | 驻留时间诊断 |

全网络目录额外包含：

| Notebook | 用途 |
|---|---|
| `09_ort_nr_transition.ipynb` | 站点编号转换诊断 |
| `10_delay_map.ipynb` | 站点级延误地图 |
| `11_timetable_collision_check.ipynb` | 时刻表碰撞检查 |
| `12_delay_comparison.ipynb` | 带信号信息产物的延误对比 |
| `13_trip_inspection.ipynb` | 结合站点地理信息的行程级检查 |

报告和图表保存在各目录的 `quality_report/` 下。

---

## 8. 消融实验

`ablation/experiment_a/` 用于评估“计算得到的 dwell time”相比“按线路固定 dwell time 基线”是否能提升下一站行驶时间预测。

脚本：

| 脚本 | 作用 |
|---|---|
| `prepare_data.py` | 生成 computed/fixed dwell time 两组训练、验证、测试特征矩阵 |
| `train.py` | 使用 LightGBM 和 early stopping 训练模型 |
| `evaluate.py` | 计算 MAE/RMSE 并保存图表 |

当前 `ablation/experiment_a/results/metrics.json` 中记录的结果：

| 变体 | MAE | RMSE |
|---|---:|---:|
| 计算 dwell time | 23.2744 秒 | 36.9413 秒 |
| 固定 dwell time | 23.4588 秒 | 37.3426 秒 |

相对 RMSE 提升：1.0747%。

更完整的 `metrics_ablation.json` 还包含 `Full`、`No-location` 和 `Ops-only` 配置。其中 `Full` 配置下，计算 dwell time 的 MAE 为 16.7291 秒，RMSE 为 32.3327 秒。

---

## 9. 已知限制

| 范围 | 当前状态 |
|---|---|
| 产物同步 | 核心表与扩展 parquet 当前行数不同；重新生成核心表后应重新运行扩展 notebook |
| 环境可复现性 | 尚未提交 `requirements.txt` 或 `pyproject.toml` |
| 法定节假日 | `is_workday` 仅区分工作日和周末，未排除德国节假日 |
| 交通信号 | 核心表中的 `has_traffic_signal` 仍是占位字段；带信号信息的结果单独存放 |
| 特殊活动 | 活动文件为人工整理，目前覆盖范围较窄 |
| 车辆元数据 | 车辆查找基于编号范围，不能覆盖所有车辆记录 |
| Notebook 路径 | 一些探索性 notebook 仍直接引用旧 CSV 或公交/有轨电车文件 |

---

## 10. 建议后续工作

1. 增加可复现环境文件，优先考虑 `pyproject.toml` 或 `requirements.txt`。
2. 按顺序重新运行 `notebooks/01_pipeline.ipynb` 和 `notebooks/02_extensions.ipynb`，同步核心表和扩展表。
3. 将交通信号特征从 notebook-only 逻辑提升为可复用模块。
4. 为 `detect_stop_events`、`expand_timetable` 和 `match_and_compute_delay` 增加轻量 smoke tests。
5. 补充原始 DVB、DWD、特殊活动和车辆查找表数据的来源与许可说明。
