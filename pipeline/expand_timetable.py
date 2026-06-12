"""
expand_timetable.py

Step 2：将 timetable_trips 的 segmente 字段展开为站级别时刻表（分析流程第二步）

背景：
  原始时刻表 timetable_trips_2025_07_22.csv 中，每趟行程（fahrt_id）的途经站
  信息以 JSON 数组存储在 segmente 列中，每个元素包含：
    - ort_nr   ：站点编号
    - lenkzeit ：从本站到下一站的行驶时间（秒，不含驻留时间）
  计划到站时间需要从 zp_abfahrt（第0站出发时间）
  累积相加各段 lenkzeit 推算。

输入：
  ../data/timetable_trips_2025_07_22.csv  —  时刻表原始数据

处理步骤：
  1. 读取时刻表，去重：同一 fahrt_id 保留 tst_iso 最新的一条记录
  2. 过滤掉 segmente 为空的行（无站点信息的行程）
  3. 解析 segmente JSON，逐站计算 scheduled_arrival_unix：
       stop_0: zp_abfahrt
       stop_n: zp_abfahrt + sum(lenkzeit[0..n-1])
  4. 转换为可读时间格式（UTC），统计行程数、站点数、每趟平均站数

输出：
  timetable_expanded.csv  —  展开后的站级别时刻表，每行对应一趟行程的一个停靠站，
                              包含字段：fahrt_id, stop_index, ort_nr,
                              scheduled_arrival_unix, scheduled_arrival_time

逻辑：
  - 第0站：scheduled_arrival = zp_abfahrt
  - 第n站：scheduled_arrival = zp_abfahrt + sum(lenkzeit[0..n-1])
  - segmente 为空的行跳过
  - 同一 fahrt_id 多次出现时取 tst_iso 最新的一条

输出文件：
  - timetable_expanded.csv : 每行=一趟车的一个站的计划到站时间
"""

import polars as pl
import json
from datetime import datetime, timezone

# ── 读取 timetable_trips ──────────────────────────────────────
print("读取 timetable_trips...")
tt = pl.read_csv(
    "../data/timetable_trips_2025_07_22.csv",
    infer_schema_length=10000
).with_columns(
    pl.col("tst_iso").str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z")
)

# ── 去重：同一 fahrt_id 保留最新一条 ──────────────────────────
print("去重（同一 fahrt_id 取最新记录）...")
tt = (
    tt.sort("tst_iso", descending=True)
      .unique(subset=["fahrt_id"], keep="first")
)
print(f"去重后行数: {len(tt)}")

# ── 过滤掉 segmente 为空的行 ──────────────────────────────────
tt = tt.filter(pl.col("segmente") != "[]")
print(f"过滤空segmente后行数: {len(tt)}")

# ── 展开 segmente ─────────────────────────────────────────────
print("展开 segmente...")

rows = []

for record in tt.iter_rows(named=True):
    fahrt_id             = record["fahrt_id"]
    scheduled_departure_time = record["zp_abfahrt"]

    try:
        segmente = json.loads(record["segmente"])
    except Exception:
        continue

    if not segmente:
        continue

    # 累计 lenkzeit 计算每站到站时间
    cumulative_sec = 0

    for stop_index, seg in enumerate(segmente):
        ort_nr   = seg.get("ort_nr")
        lenkzeit = seg.get("lenkzeit", 0)

        rows.append({
            "fahrt_id":                int(fahrt_id),
            "stop_index":              stop_index,
            "ort_nr":                  ort_nr,
            "scheduled_arrival_unix":  scheduled_departure_time + cumulative_sec,
        })

        cumulative_sec += lenkzeit

# ── 転为 DataFrame ────────────────────────────────────────────
print(f"\n展开后总行数（站级别）: {len(rows)}")

expanded = pl.DataFrame(rows).with_columns(
    pl.from_epoch(pl.col("scheduled_arrival_unix"), time_unit="s").alias("scheduled_arrival_time")
)

# ── 基本统计 ─────────────────────────────────────────────────
print("\n" + "═" * 50)
print("📋 展开结果预览")
print("═" * 50)
print(expanded.head(10))

print("\n" + "═" * 50)
print("📋 基本统计")
print("═" * 50)
print(f"唯一 fahrt_id 数量:  {expanded['fahrt_id'].n_unique()}")
print(f"唯一站点数量:        {expanded['ort_nr'].n_unique()}")
print(f"每趟车平均站数:      {len(expanded) / expanded['fahrt_id'].n_unique():.1f}")
print(f"scheduled_arrival 范围:")
print(f"  最早: {expanded['scheduled_arrival_time'].min()}")
print(f"  最晚: {expanded['scheduled_arrival_time'].max()}")

print("\n每趟车站数分布:")
print(
    expanded.group_by("fahrt_id")
    .len()
    .rename({"len": "stop_count"})
    .select("stop_count")
    .describe()
)

# ── 保存 ─────────────────────────────────────────────────────
expanded.write_csv("timetable_expanded.csv")
print("\n已保存为 timetable_expanded.csv")
