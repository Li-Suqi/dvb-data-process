"""
match_arrivals.py

Step 3：关联实际到站时间与计划到站时间，计算延误（分析流程第三步）

背景：
  完成停站事件检测（stop_clusters_v2.csv）和时刻表展开（timetable_expanded.csv）后，
  本脚本将两者关联，为每次实际到站事件匹配计划到站时间，并计算两种延误：
    - delay_calculated_sec：实际到站时间（drop_time）− 计划到站时间（scheduled_arrival_time）
    - delay_recorded_sec  ：车辆通过 lage 字段自报的延误值

输入：
  ../data/regular_linie_week.csv  —  原始车辆位置数据（含 fahrt_id、ort_nr_start、lage）
  stop_clusters_v2.csv            —  停站事件列表（由 stop_clusters.py 生成）
  timetable_expanded.csv          —  站级别时刻表（由 expand_timetable.py 生成）

处理步骤：
  1. 从原始数据按 drop_row_idx 取出每次停站对应的 fahrt_id、ort_nr_start、lage
  2. 与 timetable_expanded 在 fahrt_id AND ort_nr_start = ort_nr 上 left join
  3. 若同一趟车同一站出现多条匹配（该站在路线中重复出现），
     取 scheduled_arrival_time 与 drop_time 时间差最小的一条
  4. 计算 delay_calculated_sec（微秒时间戳差 / 1_000_000）
  5. 过滤掉未能匹配到计划时间的行

输出：
  arrivals_matched.csv  —  已关联计划时间的到站事件，每行包含：
                            fzg_id, drop_row_idx, drop_time, linie, fahrt_id,
                            ort_nr_start, stop_index, stop_status,
                            scheduled_arrival_time, delay_calculated_sec, delay_recorded_sec
  控制台打印匹配率统计和两种延误的基本统计信息

逻辑：
  1. 从 regular_linie_week.csv 中，根据 drop_row_idx 取出每次到站事件
     对应的 ort_nr_start 和 fahrt_id
  2. 与 timetable_expanded 在 fahrt_id AND ort_nr_start = ort_nr 上关联
  3. 若同一趟车同一站有多条匹配（该站在路线中出现多次），
     取 scheduled_arrival_time 与 drop_time 最接近的一条
  4. 计算延误 = tatsächliche Ankunft − scheduled_arrival_time（秒）
  5. 同时取 drop 时刻附近的 lage 值作为自报延误

输出文件：
  - arrivals_matched.csv : 每行=一次到站事件，含计划/实际/自报延误
"""

import polars as pl

# ── 读取原始数据 ──────────────────────────────────────────────
print("读取数据...")
df = pl.read_csv(
    "../data/regular_linie_week.csv",
    schema_overrides={"linie_text": pl.Utf8}
).with_columns(
    pl.col("tst_iso").str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z"),
    pl.int_range(pl.len()).over("fzg_id").alias("row_idx")
).select([
    "fzg_id", "row_idx", "tst_iso", "fahrt_id",
    "ort_nr_start", "linie", "lage"
])

print(f"  原始数据: {len(df)} 行")

# ── 读取 stop_clusters_v2 ─────────────────────────────────────
clusters = pl.read_csv("stop_clusters_v2.csv").with_columns(
    pl.col("drop_time").str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z")
)
print(f"  停站事件: {len(clusters)} 行")

# ── 读取 timetable_expanded ───────────────────────────────────
tt = pl.read_csv("timetable_expanded.csv").with_columns(
    pl.col("scheduled_arrival_time")
    .str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f")  # 不带时区
    .dt.replace_time_zone("UTC")                       # 手动标记为UTC
)
print(f"  时刻表展开: {len(tt)} 行")

# ── Step 1：从原始数据取 drop 行的 fahrt_id 和 ort_nr_start ───
print("\n关联 drop 行的 fahrt_id 和 ort_nr_start...")

drop_info = (
    clusters.join(
        df.select(["fzg_id", "row_idx", "fahrt_id", "ort_nr_start", "lage"]),
        left_on  = ["fzg_id", "drop_row_idx"],
        right_on = ["fzg_id", "row_idx"],
        how      = "left"
    )
    .with_columns([
        pl.col("fahrt_id").cast(pl.Int64),
        pl.col("ort_nr_start").cast(pl.Int64),
    ])
)

print(f"  drop事件中 fahrt_id 非空: {drop_info['fahrt_id'].drop_nulls().len()}")
print(f"  drop事件中 ort_nr_start 非空: {drop_info['ort_nr_start'].drop_nulls().len()}")

# ── Step 2：与 timetable 关联 ─────────────────────────────────
print("\n与时刻表关联...")

matched = (
    drop_info
    .join(
        tt.select(["fahrt_id", "stop_index", "ort_nr", "scheduled_arrival_time"]),
        left_on  = ["fahrt_id", "ort_nr_start"],
        right_on = ["fahrt_id", "ort_nr"],
        how      = "left"
    )
)

print(f"  关联后行数（含多重匹配）: {len(matched)}")

# ── Step 3：多重匹配时取最接近 drop_time 的计划时间 ───────────
print("处理多重匹配...")

matched = matched.with_columns(
    (
        (pl.col("drop_time").cast(pl.Int64) -
         pl.col("scheduled_arrival_time").cast(pl.Int64))
        .abs()
        .alias("time_diff_us")
    )
)

matched = (
    matched
    .sort(["fzg_id", "drop_row_idx", "time_diff_us"])
    .unique(subset=["fzg_id", "drop_row_idx"], keep="first")
)

print(f"  去重后行数: {len(matched)}")

# ── Step 4：计算延误 ──────────────────────────────────────────
print("计算延误...")

matched = matched.with_columns([
    # 实际延误（秒）= 实际到站 - 计划到站
    (
        (pl.col("drop_time").cast(pl.Int64) -
         pl.col("scheduled_arrival_time").cast(pl.Int64))
        / 1_000_000
    ).alias("delay_calculated_sec"),

    # lage 是自报延误（秒），直接取
    pl.col("lage").alias("delay_recorded_sec"),
])

# ── Step 5：过滤掉未匹配的行 ──────────────────────────────────
matched_valid = matched.filter(
    pl.col("scheduled_arrival_time").is_not_null()
)

print(f"\n═" * 50)
print("📊 匹配结果")
print(f"═" * 50)
print(f"总到站事件:          {len(clusters):>8,}")
print(f"成功匹配计划时间:    {len(matched_valid):>8,}  ({len(matched_valid)/len(clusters)*100:.1f}%)")
print(f"未匹配:              {len(clusters)-len(matched_valid):>8,}  ({(len(clusters)-len(matched_valid))/len(clusters)*100:.1f}%)")

# 延误统计
print(f"\n计算延误（秒）基本统计（匹配成功的）:")
print(
    matched_valid
    .select("delay_calculated_sec")
    .describe()
)

print(f"\n自报延误（lage）基本统计:")
print(
    matched_valid
    .filter(pl.col("delay_recorded_sec").is_not_null())
    .select("delay_recorded_sec")
    .describe()
)

# 两者差异
matched_both = matched_valid.filter(
    pl.col("delay_recorded_sec").is_not_null()
).with_columns(
    (pl.col("delay_calculated_sec") - pl.col("delay_recorded_sec"))
    .alias("delay_diff_sec")
)

print(f"\n两种延误差异（calculated - recorded）统计:")
print(matched_both.select("delay_diff_sec").describe())

# ── 保存 ─────────────────────────────────────────────────────
output = matched_valid.select([
    "fzg_id", "drop_row_idx", "drop_time",
    "linie", "fahrt_id", "ort_nr_start", "stop_index",
    "stop_status", "scheduled_arrival_time",
    "delay_calculated_sec", "delay_recorded_sec",
])

output.write_csv("arrivals_matched.csv")
print(f"\n已保存为 arrivals_matched.csv")