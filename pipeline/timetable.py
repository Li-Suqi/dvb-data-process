import json
import polars as pl


def expand_timetable(raw_tt: pl.DataFrame) -> pl.DataFrame:
    """
    Expand raw timetable trips into stop-level scheduled arrival times.

    Parameters
    ----------
    raw_tt : pl.DataFrame
        Raw timetable. Required columns:
          fahrt_id, zp_abfahrt, segmente, tst_iso

    Returns
    -------
    pl.DataFrame
        One row per stop per trip:
          fahrt_id, stop_index, ort_nr,
          scheduled_arrival_unix, scheduled_arrival_time
    """
    tt = (
        raw_tt
        .with_columns(
            pl.col("tst_iso").str.to_datetime(
                format="%Y-%m-%dT%H:%M:%S%.f%z"
            )
        )
        .sort("tst_iso", descending=True)
        .unique(subset=["fahrt_id"], keep="first")
        .filter(pl.col("segmente") != "[]")
    )

    rows = []
    for record in tt.iter_rows(named=True):
        trip_id       = record["fahrt_id"]
        dep_time_unix = record["zp_abfahrt"]

        try:
            segments = json.loads(record["segmente"])
        except Exception:
            continue

        if not segments:
            continue

        cumulative_sec = 0
        for stop_index, seg in enumerate(segments):
            rows.append({
                "fahrt_id":               int(trip_id),
                "stop_index":             stop_index,
                "ort_nr":                 seg.get("ort_nr"),
                "scheduled_arrival_unix": dep_time_unix + cumulative_sec,
            })
            cumulative_sec += seg.get("lenkzeit", 0)

    return (
        pl.DataFrame(rows)
        .with_columns(
            pl.from_epoch(
                pl.col("scheduled_arrival_unix"), time_unit="s"
            ).alias("scheduled_arrival_time")
        )
    )


def match_and_compute_delay(
    stop_events: pl.DataFrame,
    raw_df:      pl.DataFrame,
    timetable:   pl.DataFrame,
) -> pl.DataFrame:
    """
    Match stop events to scheduled arrival times and compute delay,
    dwell_time, travel_time, and occupancy.

    Parameters
    ----------
    stop_events : pl.DataFrame
        Output of detect_stop_events(). Required columns:
          fzg_id, drop_row_idx, arrival_time, departure_time, stop_status
    raw_df : pl.DataFrame
        Raw vehicle positions. Required columns:
          fzg_id, row_idx, fahrt_id, ort_nr_start, lage, besetztgrad
    timetable : pl.DataFrame
        Output of expand_timetable(). Required columns:
          fahrt_id, stop_index, ort_nr, scheduled_arrival_time

    Returns
    -------
    pl.DataFrame
        One row per matched stop event:
          fzg_id, drop_row_idx, arrival_time, departure_time,
          linie, fahrt_id, ort_nr_start, stop_index, stop_status,
          scheduled_arrival_time,
          delay_calculated_sec, delay_recorded_sec,
          dwell_time, travel_time, besetztgrad
    """
    # Step 1: 从原始数据取每个drop对应的fahrt_id, ort_nr_start, lage, besetztgrad
    drop_info = (
        stop_events.join(
            raw_df.select([
                "fzg_id", "row_idx",
                "fahrt_id", "ort_nr_start",
                "lage", "besetztgrad",
            ]),
            left_on  = ["fzg_id", "drop_row_idx"],
            right_on = ["fzg_id", "row_idx"],
            how      = "left",
        )
        .with_columns([
            pl.col("fahrt_id").cast(pl.Int64),
            pl.col("ort_nr_start").cast(pl.Int64),
        ])
    )

    # Step 2: 与时刻表关联，以 service_date 为额外 join key 防止跨日错误匹配
    # service_date = (time - 4h).date()，使跨午夜运行（如 23:50→00:10）归入同一服务日
    tt_clean = timetable.with_columns(
        pl.col("scheduled_arrival_time").dt.replace_time_zone("UTC")
    ).with_columns(
        (pl.col("scheduled_arrival_time") - pl.duration(hours=4))
        .dt.date()
        .alias("service_date")
    )

    drop_info = drop_info.with_columns(
        (pl.col("arrival_time") - pl.duration(hours=4))
        .dt.date()
        .alias("service_date")
    )

    matched = drop_info.join(
        tt_clean.select(["fahrt_id", "stop_index", "ort_nr", "scheduled_arrival_time", "service_date"]),
        left_on  = ["fahrt_id", "ort_nr_start", "service_date"],
        right_on = ["fahrt_id", "ort_nr",        "service_date"],
        how      = "left",
    )

    # Step 3: 多重匹配时取时间差最小的一条
    matched = (
        matched
        .with_columns(
            (
                (pl.col("arrival_time").cast(pl.Int64) -
                 pl.col("scheduled_arrival_time").cast(pl.Int64))
                .abs()
                .alias("time_diff_us")
            )
        )
        .sort(["fzg_id", "drop_row_idx", "time_diff_us"])
        .unique(subset=["fzg_id", "drop_row_idx"], keep="first")
    )

    # Step 4: 计算delay
    matched = matched.with_columns([
        (
            (pl.col("arrival_time").cast(pl.Int64) -
             pl.col("scheduled_arrival_time").cast(pl.Int64))
            / 1_000_000
        ).alias("delay_calculated_sec"),
        pl.col("lage").alias("delay_recorded_sec"),
    ])

    # Step 4b: 去重 — 同一辆车匹配到同一时刻表记录时，normal 优先于 no_door
    # key: (fzg_id, fahrt_id, ort_nr_start, service_date)
    # 优先级: normal=0 > multi_door=1 > no_door=2
    _before_dedup = len(matched)
    matched = (
        matched
        .with_columns(
            pl.when(pl.col("stop_status") == "normal").then(pl.lit(0, dtype=pl.Int8))
            .when(pl.col("stop_status") == "multi_door").then(pl.lit(1, dtype=pl.Int8))
            .otherwise(pl.lit(2, dtype=pl.Int8))
            .alias("_status_pri")
        )
        .sort(["fzg_id", "fahrt_id", "ort_nr_start", "service_date", "_status_pri"])
        .unique(subset=["fzg_id", "fahrt_id", "ort_nr_start", "service_date"], keep="first")
        .drop("_status_pri")
    )
    _dedup_removed = _before_dedup - len(matched)
    print(f"[dedup] Removed {_dedup_removed:,} collision events (normal kept over no_door)  →  {len(matched):,} remaining")

    # Step 4c: 过滤跨日错误匹配
    # 条件：delay_calculated_sec > 3600 且 |delay_calculated - delay_recorded| > 3000
    # 两者同时满足说明计算延误异常大且与车载上报值严重不符，极可能是跨日匹配错误
    _before_crossday = len(matched)
    matched = matched.filter(
        ~(
            (pl.col("delay_calculated_sec") > 3600) &
            (
                (pl.col("delay_calculated_sec") - pl.col("delay_recorded_sec"))
                .abs() > 3000
            )
        )
    )
    _crossday_removed = _before_crossday - len(matched)
    print(f"[crossday] Removed {_crossday_removed:,} cross-day mismatch rows  →  {len(matched):,} remaining")

    # Step 5: 计算dwell_time（秒）
    # no_door：无法计算，输出 -1 作为哨兵值
    # normal / multi_door：departure - arrival（门关 - 门开）
    matched = matched.with_columns(
        pl.when(pl.col("stop_status") == "no_door")
        .then(pl.lit(-1.0))
        .otherwise(
            (pl.col("departure_time").cast(pl.Int64) -
             pl.col("arrival_time").cast(pl.Int64))
            / 1_000_000
        )
        .alias("dwell_time")
    )

    # Step 6: 计算travel_time（秒）
    # 同一辆车，当前stop的arrival_time 减去上一个stop的departure_time
    matched = (
        matched
        .sort(["fzg_id", "arrival_time"])
        .with_columns(
            (
                (pl.col("arrival_time").cast(pl.Int64) -
                 pl.col("departure_time").shift(1).over("fzg_id").cast(pl.Int64))
                / 1_000_000
            ).alias("travel_time")
        )
    )

    # Step 7: 过滤未匹配的行，输出最终列
    return (
        matched
        .filter(pl.col("scheduled_arrival_time").is_not_null())
        .select([
            "fzg_id", "drop_row_idx",
            "arrival_time", "departure_time",
            "linie", "fahrt_id", "ort_nr_start", "stop_index",
            "stop_status", "scheduled_arrival_time",
            "delay_calculated_sec", "delay_recorded_sec",
            "dwell_time", "travel_time",
            "besetztgrad",
        ])
    )