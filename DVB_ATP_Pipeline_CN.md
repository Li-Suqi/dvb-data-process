# DVB ATP Pipeline — 项目总结（中文版）

> **背景：** 德累斯顿 DVB 公交网络的到站时间预测（ATP）数据预处理流水线。
> 将一周原始 ITCS 车辆遥测数据（2025-07-28 → 2025-08-03，538 辆车，1087 万条位置记录）转化为干净、可分析的停站事件表，并附加天气、车型和特殊活动信息。

---

## 1. 项目目录结构

```
dvb_atp_pipeline/
├── pipeline/                  # 可复用 Python 模块
│   ├── detector.py            # 停站事件检测
│   ├── timetable.py           # 时刻表展开 + 延误计算
│   ├── extensions.py          # 天气 / 车辆 / 特殊活动关联
│   ├── feature_builder.py     # 衍生二值特征
│   ├── expand_timetable.py    # 独立探索脚本（流程第2步）
│   └── match_arrivals.py      # 独立探索脚本（流程第3步）
├── notebooks/
│   ├── 01_pipeline.ipynb      # 端到端核心表构建
│   ├── 02_extensions.ipynb    # 扩展表构建
│   └── explore_vehicle_types.ipynb
├── data/
│   ├── regular_linie_week.csv          # 原始车辆位置数据（1087万行）
│   ├── timetable_trips_2025_07_22.csv  # 原始时刻表（26.4万行）
│   ├── vehicle_data_2025_07_22.csv     # 原始车辆元数据
│   ├── external/
│   │   ├── weather/    DWD气象原始数据 + weather_dresden.parquet（清洗后）
│   │   ├── vehicle/    dvb_fahrzeug_info.csv（类型/容量查找表）+ vehicle_info.parquet
│   │   └── events/     special_events.csv（德累斯顿体育活动）
│   └── processed/
│       ├── core_stop_events.parquet              （核心表，994,117行，18列）
│       ├── core_stop_events_with_weather.parquet （+天气字段）
│       └── core_stop_events_with_vehicle.parquet （+车辆字段）
```

---

## 2. 数据流程图

```
┌──────────────────────────────────────────────────────────────────────┐
│  原始输入                                                             │
│                                                                      │
│  regular_linie_week.csv              timetable_trips_2025_07_22.csv │
│  1,087万行 / 538辆车 / 1周            26.4万行 / 97,982趟行程         │
│  字段：fzg_id, distanz, tuerkriterium  字段：fahrt_id, segmente(JSON) │
└──────────────┬───────────────────────────────┬───────────────────────┘
               │                               │
               ▼                               ▼
   ┌───────────────────────┐       ┌──────────────────────────┐
   │  detect_stop_events() │       │   expand_timetable()     │
   │  detector.py          │       │   timetable.py           │
   │                       │       │                          │
   │  核心逻辑：             │       │  解析 segmente JSON      │
   │  distanz 下降 >100m    │       │  累积 lenkzeit           │
   │  → 触发一次停站事件   │       │  计算每站计划到站时间    │
   │                       │       │                          │
   │  自适应窗口 n∈[3,10]  │       │  238万行（站级别）       │
   │  门状态状态机分析     │       │  97,591趟行程            │
   │                       │       │  2,006个唯一站点         │
   │  102万个停站事件      │       └──────────────┬───────────┘
   │  normal    82.3%      │                      │
   │  no_door   17.4%      │                      │
   │  multi_door 0.3%      │                      │
   └──────────────┬────────┘                      │
                  │                               │
                  └──────────────┬────────────────┘
                                 │
                                 ▼
                ┌────────────────────────────┐
                │  match_and_compute_delay() │
                │  timetable.py              │
                │                            │
                │  按 fahrt_id + ort_nr 关联 │
                │  多重匹配取最小时间差      │
                │  计算 delay / dwell /      │
                │         travel_time        │
                │                            │
                │  994,117 条匹配（97.3%）   │
                └────────────────┬───────────┘
                                 │
                                 ▼
                ┌────────────────────────────┐
                │   add_binary_features()    │
                │   feature_builder.py       │
                │                            │
                │  + is_peak_hour            │
                │  + is_workday              │
                │  + has_traffic_signal(占位)│
                └────────────────┬───────────┘
                                 │
                                 ▼
            ┌────────────────────────────────────┐
            │  核心停站事件表（Core Table）       │
            │  core_stop_events.parquet          │
            │  994,117行 · 18列                  │
            └─────────┬───────────────┬──────────┘
                      │               │
          ────────────┘               └────────────
          │                                        │
          ▼                                        ▼
  ┌───────────────────┐               ┌─────────────────────┐
  │  join_weather()   │               │  join_vehicle_info()│
  │                   │               │                     │
  │ DWD 01048站       │               │ 按 fzg_nr 范围匹配  │
  │ 日级气象数据      │               │ 车型/容量信息       │
  │ 按日期关联        │               │ 86.2% 匹配率        │
  │ 100% 覆盖         │               └─────────────────────┘
  └───────────────────┘
          │
          ▼
  ┌───────────────────────┐
  │  join_special_events()│
  │                       │
  │  德累斯顿体育活动     │
  │  按日期关联           │
  │  + has_special_event  │
  └───────────────────────┘
```

---

## 3. 函数参考手册

### `pipeline/detector.py`

#### `detect_stop_events(raw_df) → pl.DataFrame`

| 项目         | 说明                                                                                                                                                                                                                                                    |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **输入**     | 原始车辆位置 DataFrame（必须含：`fzg_id`, `tst_iso`, `distanz`, `tuerkriterium`, `linie`）                                                                                                                                                              |
| **输出**     | 每行 = 一个停站事件                                                                                                                                                                                                                                     |
| **输出字段** | `fzg_id`, `drop_row_idx`, `drop_time`, `linie`, `window_lo`, `window_hi`, `arrival_time`, `departure_time`, `stop_status`, `door_open_count`, `door_close_count`, `door_near_drop`, `is_true_multi_door`, `delta_at_drop`, `min_distanz`, `max_distanz` |

**算法说明：**

1. 按 `(fzg_id, tst_iso)` 排序，计算每辆车的 `delta = distanz[t] - distanz[t-1]`。
2. 当 `delta < -100m` 时，认为发生一次"到站"（车辆的累积里程计在每站重置）。
3. 根据相邻两次下降的中位间隔，自适应计算窗口大小 `n ∈ [3, 10]`。
4. 在 ±n 行的窗口内统计门开/关次数。
5. 分类规则：
   - `no_door`：窗口内无门动作 → 司机未开门（掠过站/错误触发）
   - `multi_door`：≥2 次开门且中间无显著里程跳变 → 同一站双次停靠
   - `normal`：其余所有情况

---

### `pipeline/timetable.py`

#### `expand_timetable(raw_tt) → pl.DataFrame`

| 项目     | 说明                                                                                                       |
| -------- | ---------------------------------------------------------------------------------------------------------- |
| **输入** | 原始时刻表 DataFrame（`fahrt_id`, `zp_abfahrt`, `segmente`, `tst_iso`）                                    |
| **输出** | 站级别计划时刻表（`fahrt_id`, `stop_index`, `ort_nr`, `scheduled_arrival_unix`, `scheduled_arrival_time`） |

解析每趟行程的 JSON `segmente` 字段，逐站累积 `lenkzeit`（站间行驶时间，秒），以 `zp_abfahrt`（第0站出发时间）为基准推算每站的计划到站时刻。

#### `match_and_compute_delay(stop_events, raw_df, timetable) → pl.DataFrame`

| 项目             | 说明                                                                                                                                                   |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **输入**         | 停站事件表 + 原始位置数据 + 展开时刻表                                                                                                                 |
| **输出**         | 15列已匹配事件表                                                                                                                                       |
| **关键输出字段** | `delay_calculated_sec`（计算延误）, `delay_recorded_sec`（车载自报延误）, `dwell_time`（驻留时间）, `travel_time`（行驶时间）, `besetztgrad`（载客率） |

处理步骤：从原始数据按 `drop_row_idx` 取出 `fahrt_id` + `ort_nr_start` → 与时刻表左连接 → 多重匹配时保留时间差最小的一条 → 计算所有衍生时间字段。

---

### `pipeline/extensions.py`

#### `load_weather(raw_path) → pl.DataFrame`

| 项目     | 说明                                                                                                                                                    |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **输入** | DWD 原始气象文件路径（分号分隔）                                                                                                                        |
| **输出** | `MESS_DATUM`（日期）, `TMK/TXK/TNK/TGK`（气温℃）, `RSK`（降水mm）, `RSKF`（降水类型）, `SDK`（日照时数h）, `SHK_TAG`（积雪深度cm）, `FM`（平均风速m/s） |

去除列名首尾空白，将所有数值列转为 Float64，将 DWD 缺失值标记 `-999` 替换为 `null`。

#### `join_weather(core, weather) → pl.DataFrame`

提取 `arrival_time` 的日期部分，与 `weather` 左连接，返回核心表 + 所有天气列，无行丢失。

#### `load_vehicle_info(raw_vehicle_path, lookup_path) → pl.DataFrame`

| 项目     | 说明                                                                                                                           |
| -------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **输入** | `vehicle_data_2025_07_22.csv` + `dvb_fahrzeug_info.csv`                                                                        |
| **输出** | `fzg_id`, `fzg_nr`, `fzg_nr_int`, `typ`, `fahrzueg_type`, `fahrgasttüren`, `länge_m`, `sitzplätze`, `stehplätze`, `kapazitaet` |

从原始 `fzg_nr` 字符串提取可比对整数（有轨电车：取第2-5位数字；公共汽车：取前6位），再对查找表做交叉连接 + 范围过滤匹配。未匹配车辆保留但属性字段为 null。

#### `join_vehicle_info(core, vehicle_info) → pl.DataFrame`

按 `fzg_id` 左连接车辆属性，86.2% 的停站事件获得车型信息。

#### `load_special_events(events_path) → pl.DataFrame`

加载 `special_events.csv`（字段：`event_id`, `date`, `event_type`, `location`, `latitude`, `longitude`），将 `date` 字段转为日期类型。

#### `join_special_events(core, events) → pl.DataFrame`

按日期左连接，新增 `has_special_event`（布尔）、`event_type`、`event_location`、`event_latitude`、`event_longitude`。

---

### `pipeline/feature_builder.py`

#### `add_binary_features(df) → pl.DataFrame`

| 项目     | 说明                                                                                      |
| -------- | ----------------------------------------------------------------------------------------- |
| **输入** | 含 `arrival_time`（datetime, UTC）的任意 DataFrame                                        |
| **输出** | 原表 + `is_peak_hour`（Int8）, `is_workday`（Int8）, `has_traffic_signal`（Int8，全null） |

| 特征                 | 定义                                        |
| -------------------- | ------------------------------------------- |
| `is_peak_hour`       | 1 = 早高峰 07:00–09:00 或晚高峰 16:00–19:00 |
| `is_workday`         | 1 = 周一至周五                              |
| `has_traffic_signal` | 占位符，等待路网基础设施数据接入            |

---

## 4. 核心停站事件表字段说明

> 文件：`data/processed/core_stop_events.parquet`，**994,117行，18列**

| 字段                     | 类型          | 空值率 | 含义                                 |
| ------------------------ | ------------- | ------ | ------------------------------------ |
| `fzg_id`                 | Int64         | 0%     | 车辆ID                               |
| `drop_row_idx`           | Int64         | 0%     | 检测到停站的原始数据行索引           |
| `arrival_time`           | Datetime(UTC) | 0%     | 实际到站时刻                         |
| `departure_time`         | Datetime(UTC) | 0%     | 实际离站时刻（最后一次关门）         |
| `linie`                  | Int64         | 0%     | 线路号                               |
| `fahrt_id`               | Int64         | 0%     | 行程ID                               |
| `ort_nr_start`           | Int64         | 0%     | 站点编号                             |
| `stop_index`             | Int64         | 0%     | 该站在行程中的顺序位置               |
| `stop_status`            | String        | 0%     | `normal` / `no_door` / `multi_door`  |
| `scheduled_arrival_time` | Datetime(UTC) | 0%     | 时刻表计划到站时刻                   |
| `delay_calculated_sec`   | Float64       | 0%     | 计算延误 = 实际 − 计划（秒）         |
| `delay_recorded_sec`     | Int64         | 0%     | 车载自报延误（lage字段，秒）         |
| `dwell_time`             | Float64       | 0%     | 驻留时间 = 离站 − 到站（秒）         |
| `travel_time`            | Float64       | <0.1%  | 行驶时间 = 本次到站 − 上次离站（秒） |
| `besetztgrad`            | Int64         | 0%     | 载客率等级                           |
| `is_peak_hour`           | Int8          | 0%     | 1 = 高峰时段                         |
| `is_workday`             | Int8          | 0%     | 1 = 工作日                           |
| `has_traffic_signal`     | Int8          | 100%   | 占位符，路网数据待接入               |

---

## 5. 扩展表说明

| 文件                                    | 新增字段                                                                                 | 覆盖率    |
| --------------------------------------- | ---------------------------------------------------------------------------------------- | --------- |
| `core_stop_events_with_weather.parquet` | `TMK`, `TXK`, `TNK`, `TGK`, `RSK`, `RSKF`, `SDK`, `SHK_TAG`, `FM`                        | 100%      |
| `core_stop_events_with_vehicle.parquet` | `fahrzueg_type`, `fahrgasttüren`, `länge_m`, `sitzplätze`, `stehplätze`, `kapazitaet`    | 86.2%     |
| （内存中）带特殊活动                    | `has_special_event`, `event_type`, `event_location`, `event_latitude`, `event_longitude` | 4个活动日 |

---

## 6. 公开 API — 可直接调用的函数

```python
from pipeline.detector        import detect_stop_events
from pipeline.timetable       import expand_timetable, match_and_compute_delay
from pipeline.feature_builder import add_binary_features
from pipeline.extensions      import (
    load_weather,        join_weather,
    load_vehicle_info,   join_vehicle_info,
    load_special_events, join_special_events,
)
```

### 最简端到端用法

```python
import polars as pl
from pipeline.detector        import detect_stop_events
from pipeline.timetable       import expand_timetable, match_and_compute_delay
from pipeline.feature_builder import add_binary_features
from pipeline.extensions      import load_weather, join_weather, load_vehicle_info, join_vehicle_info

# 1. 加载原始数据
raw_df = (
    pl.read_csv("data/regular_linie_week.csv",
                schema_overrides={"linie_text": pl.Utf8})
    .with_columns(pl.col("tst_iso").str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z"))
    .sort(["fzg_id", "tst_iso"])
    .with_columns(pl.int_range(pl.len()).over("fzg_id").alias("row_idx"))
)

raw_tt = pl.read_csv("data/timetable_trips_2025_07_22.csv",
                     infer_schema_length=10000)

# 2. 核心流水线
stop_events = detect_stop_events(raw_df)         # 停站事件检测
timetable   = expand_timetable(raw_tt)           # 时刻表展开
matched     = match_and_compute_delay(stop_events, raw_df, timetable)  # 关联 + 延误计算
core        = add_binary_features(matched)       # 添加二值特征

# 3. 扩展关联
weather      = load_weather("data/external/weather/produkt_klima_tag_*.txt")
core_weather = join_weather(core, weather)

vehicle_info = load_vehicle_info(
    "data/vehicle_data_2025_07_22.csv",
    "data/external/vehicle/dvb_fahrzeug_info.csv"
)
core_vehicle = join_vehicle_info(core, vehicle_info)
```

---

## 7. 关键统计数字（来自 Notebook 实际运行结果）

| 指标                     | 数值                           |
| ------------------------ | ------------------------------ |
| 原始位置记录数           | 10,869,683 条                  |
| 车辆数                   | 538 辆                         |
| 数据时间范围             | 2025-07-28 → 2025-08-03（7天） |
| 时刻表行程数（去重后）   | 97,591 趟                      |
| 时刻表唯一站点数         | 2,006 个                       |
| 检测到的停站事件总数     | 1,021,431 个                   |
| — normal（正常停站）     | 840,594 个（82.3%）            |
| — no_door（未开门）      | 178,212 个（17.4%）            |
| — multi_door（多次停靠） | 2,625 个（0.3%）               |
| 成功与时刻表匹配         | 994,117 个（97.3%）            |
| 平均计算延误             | 164 秒                         |
| 高峰时段事件数           | 259,566 个（26.1%）            |
| 工作日事件数             | 595,950 个（59.9%）            |
| 车型信息覆盖率           | 86.2%                          |
| 天气数据覆盖率           | 100%                           |

---

## 8. 已知局限与后续工作

| 问题                          | 当前状态                                                                     |
| ----------------------------- | ---------------------------------------------------------------------------- |
| `has_traffic_signal`          | 占位符，需接入路网基础设施链路数据                                           |
| `is_workday` 未排除法定节假日 | 需接入德国节假日日历                                                         |
| 车辆查找表未覆盖率 27%        | 均为特种车辆（轨道维护、磨轨车、包车大巴），超出查找范围                     |
| `dwell_time` 中位数 = 0 秒    | 约 50% 事件无开门记录（no_door 或即停即走），normal 类别的驻留时间中位数更高 |
| 特殊活动数据不完整            | 当前仅覆盖 4 场体育赛事，未来需补充音乐会、集市等                            |
