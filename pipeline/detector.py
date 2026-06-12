import numpy as np
import polars as pl

DROP_THRESHOLD = 100  # metres; distanz drop that signals a new stop


def detect_stop_events(raw_df: pl.DataFrame) -> pl.DataFrame:
    """
    Detect and classify stop events from raw vehicle position data.

    Parameters
    ----------
    raw_df : pl.DataFrame
        Raw vehicle positions. Required columns:
          fzg_id, tst_iso, distanz, tuerkriterium, linie

    Returns
    -------
    pl.DataFrame
        One row per stop event:
          fzg_id, drop_row_idx, drop_time, linie,
          window_lo, window_hi,
          arrival_time, departure_time,
          stop_status  (normal | multi_door | no_door),
          door_open_count, door_close_count,
          door_near_drop, is_true_multi_door,
          delta_at_drop, min_distanz, max_distanz
    """
    # ── Step 1: 准备数据 ──────────────────────────────────────
    df = raw_df
    if raw_df["tst_iso"].dtype == pl.String:
        df = df.with_columns(
            pl.col("tst_iso").str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z")
        )

    df = df.sort(["fzg_id", "tst_iso"]).with_columns([
        pl.int_range(pl.len()).over("fzg_id").alias("row_idx"),
        (pl.col("distanz") - pl.col("distanz").shift(1).over("fzg_id")).alias("delta"),
    ])

    # ── Step 2: 找出所有 drop 事件 ───────────────────────────
    drops = (
        df.filter(pl.col("delta") < -DROP_THRESHOLD)
        .select(["fzg_id", "row_idx", "tst_iso"])
        .rename({"tst_iso": "drop_time"})
    )

    # ── Step 3: 计算窗口大小 n ───────────────────────────────
    drop_intervals = (
        drops
        .with_columns(
            pl.col("row_idx").diff().over("fzg_id").alias("rows_between_drops")
        )
        .filter(pl.col("rows_between_drops") > 0)
        .select("rows_between_drops")
    )

    if len(drop_intervals) == 0:
        n = 5
    else:
        median_interval = drop_intervals.select(pl.col("rows_between_drops").median()).item()
        n = int(np.clip(median_interval / 2, 3, 10))

    # ── Step 4: 逐辆车处理 ───────────────────────────────────
    vehicles = df.select("fzg_id").unique().to_series().to_list()
    results  = []

    for vid in vehicles:
        vdf     = df.filter(pl.col("fzg_id") == vid).sort("row_idx")
        rows    = vdf.to_dicts()
        max_idx = len(rows) - 1

        vdrops = (
            drops.filter(pl.col("fzg_id") == vid)
            .select("row_idx").to_series().to_list()
        )

        for drop_idx in vdrops:
            lo = max(0, drop_idx - n)
            hi = min(max_idx, drop_idx + n)

            window = rows[lo: hi + 1]

            # ── 窗口内门状态转换计数 ──────────────────────────
            door_open_count     = 0
            door_close_count    = 0
            open_door_positions = []
            last_close_time     = None

            for j in range(1, len(window)):
                prev = window[j - 1]["tuerkriterium"]
                curr = window[j]["tuerkriterium"]
                if prev == False and curr == True:
                    door_open_count += 1
                    open_door_positions.append(j)
                if prev == True and curr == False:
                    door_close_count += 1
                    last_close_time = window[j]["tst_iso"]

            # ── drop 前后一行的门状态检测 ─────────────────────
            row_before     = rows[max(0, drop_idx - 1)]["tuerkriterium"]
            row_drop       = rows[drop_idx]["tuerkriterium"]
            row_after      = rows[min(max_idx, drop_idx + 1)]["tuerkriterium"]
            door_near_drop = bool(row_before or row_drop or row_after)

            # ── multi_door 真伪判断 ───────────────────────────
            is_true_multi_door = False
            if door_open_count >= 2:
                all_pairs_valid = True
                for k in range(len(open_door_positions) - 1):
                    pos_a = open_door_positions[k]
                    pos_b = open_door_positions[k + 1]

                    between_distanz = [window[m]["distanz"] for m in range(pos_a, pos_b + 1)]
                    between_deltas  = [
                        between_distanz[m] - between_distanz[m - 1]
                        for m in range(1, len(between_distanz))
                    ]

                    has_drop         = len(between_deltas) > 0 and min(between_deltas) < -30
                    distanz_growth   = max(between_distanz) - min(between_distanz)
                    has_large_growth = distanz_growth >= DROP_THRESHOLD

                    if has_drop or has_large_growth:
                        all_pairs_valid = False
                        break

                is_true_multi_door = all_pairs_valid

            # ── arrival / departure 时间 ──────────────────────
            arrival_time   = rows[drop_idx]["tst_iso"]
            departure_time = last_close_time if last_close_time is not None else arrival_time

            distanz_vals = [r["distanz"] for r in window]

            results.append({
                "fzg_id":             vid,
                "drop_row_idx":       drop_idx,
                "drop_time":          rows[drop_idx]["tst_iso"],
                "linie":              rows[drop_idx]["linie"],
                "window_lo":          lo,
                "window_hi":          hi,
                "arrival_time":       arrival_time,
                "departure_time":     departure_time,
                "door_open_count":    door_open_count,
                "door_close_count":   door_close_count,
                "door_near_drop":     door_near_drop,
                "is_true_multi_door": is_true_multi_door,
                "delta_at_drop":      rows[drop_idx]["delta"],
                "min_distanz":        min(distanz_vals),
                "max_distanz":        max(distanz_vals),
            })

    # ── Step 5: 分类 ─────────────────────────────────────────
    result_df = pl.DataFrame(results).with_columns(
        pl.when(
            (pl.col("door_open_count") == 0) & (pl.col("door_near_drop") == False)
        ).then(pl.lit("no_door"))
        .when(
            (pl.col("door_open_count") >= 2) & (pl.col("is_true_multi_door") == True)
        ).then(pl.lit("multi_door"))
        .otherwise(pl.lit("normal"))
        .alias("stop_status")
    )

    return result_df