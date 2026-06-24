from bisect import bisect_left

import polars as pl


def rescue_dwell_times(
    stop_events: pl.DataFrame,
    raw_df: pl.DataFrame,
) -> pl.DataFrame:
    """
    Post-processing rescue for normal/multi_door events where departure_time == arrival_time.

    Called after detect_stop_events(), before match_and_compute_delay().
    Corrects arrival_time, departure_time, and stop_status.

    Case 1 (arrival != drop_time): open was found but close missed.
            Scans [arrival_row_idx+1, next_drop_idx) for last 1→0.
    Case 2 (arrival == drop_time): neither open nor close found in window.
            Scans (prev_drop_idx, next_drop_idx) for first 0→1 then last 1→0.
            If still no open found, reclassifies to no_door.
    """
    # ── Step 1: compute prev/next drop boundaries per vehicle ────
    se = (
        stop_events
        .sort(["fzg_id", "drop_row_idx"])
        .with_columns([
            pl.col("drop_row_idx").shift(1).over("fzg_id").alias("_prev_drop"),
            pl.col("drop_row_idx").shift(-1).over("fzg_id").alias("_next_drop"),
        ])
        .join(
            raw_df.group_by("fzg_id").agg(pl.col("row_idx").max().alias("_max_row")),
            on="fzg_id",
            how="left",
        )
    )

    # ── Step 2: label rescue cases ───────────────────────────────
    needs = (
        (pl.col("stop_status") != "no_door") &
        (pl.col("departure_time") == pl.col("arrival_time"))
    )
    se = se.with_columns(
        pl.when(needs & (pl.col("arrival_time") == pl.col("drop_time")))
          .then(pl.lit(2))
          .when(needs & (pl.col("arrival_time") != pl.col("drop_time")))
          .then(pl.lit(1))
          .otherwise(pl.lit(0))
          .alias("_case")
    )

    rescue_df = se.filter(pl.col("_case") > 0)
    if len(rescue_df) == 0:
        return stop_events

    print(f"[rescue] Case 1 (open found, close missed): {(se['_case'] == 1).sum():,}")
    print(f"[rescue] Case 2 (no open found):            {(se['_case'] == 2).sum():,}")

    # ── Step 3: build per-vehicle data structures ────────────────
    raw_sorted = raw_df.sort(["fzg_id", "row_idx"])

    rows_by_vehicle  = {}   # vid -> list of {row_idx, tst_iso, tuerkriterium}
    ridxs_by_vehicle = {}   # vid -> sorted list of row_idx (for bisect)
    ts_to_ridx       = {}   # vid -> {tst_iso: row_idx} for Case 1 lookup

    for vdf in raw_sorted.partition_by("fzg_id", maintain_order=True):
        vid   = vdf["fzg_id"][0]
        rows  = vdf.select(["row_idx", "tst_iso", "tuerkriterium"]).to_dicts()
        rows_by_vehicle[vid]  = rows
        ridxs_by_vehicle[vid] = [r["row_idx"] for r in rows]
        ts_to_ridx[vid]       = {r["tst_iso"]: r["row_idx"] for r in rows}

    # ── Step 4: process rescue events ────────────────────────────
    updates = {}   # (fzg_id, drop_row_idx) -> {arrival_time, departure_time, stop_status}

    for row in rescue_df.iter_rows(named=True):
        vid      = row["fzg_id"]
        drop_idx = row["drop_row_idx"]
        case     = row["_case"]

        prev_drop = row["_prev_drop"]
        next_drop = row["_next_drop"]
        max_row   = row["_max_row"]

        lo = (int(prev_drop) if prev_drop is not None else -1) + 1
        hi = int(next_drop)  if next_drop is not None else int(max_row) + 1

        rows  = rows_by_vehicle.get(vid, [])
        ridxs = ridxs_by_vehicle.get(vid, [])

        def get_range(lo, hi):
            l = bisect_left(ridxs, lo)
            r = bisect_left(ridxs, hi)
            return rows[l:r]

        if case == 2:
            search = get_range(lo, hi)

            first_open = None
            last_close = None
            prev_door  = None

            for r in search:
                curr = r["tuerkriterium"]
                if prev_door is not None:
                    if not prev_door and curr and first_open is None:
                        first_open = r["tst_iso"]
                    if prev_door and not curr and first_open is not None:
                        last_close = r["tst_iso"]
                prev_door = curr

            if first_open is None:
                updates[(vid, drop_idx)] = dict(
                    arrival_time   = row["drop_time"],
                    departure_time = row["drop_time"],
                    stop_status    = "no_door",
                )
            else:
                updates[(vid, drop_idx)] = dict(
                    arrival_time   = first_open,
                    departure_time = last_close if last_close is not None else first_open,
                    stop_status    = row["stop_status"],
                )

        elif case == 1:
            arrival_ts      = row["arrival_time"]
            arrival_row_idx = ts_to_ridx.get(vid, {}).get(arrival_ts)

            if arrival_row_idx is None:
                continue

            search = get_range(arrival_row_idx + 1, hi)

            last_close = None
            prev_door  = True   # door was open at arrival_row_idx

            for r in search:
                curr = r["tuerkriterium"]
                if prev_door and not curr:
                    last_close = r["tst_iso"]
                prev_door = curr

            if last_close is not None:
                updates[(vid, drop_idx)] = dict(
                    arrival_time   = arrival_ts,
                    departure_time = last_close,
                    stop_status    = row["stop_status"],
                )

    print(f"[rescue] Events successfully patched: {len(updates):,}")

    if not updates:
        return stop_events

    # ── Step 5: apply updates ────────────────────────────────────
    dt_dtype = stop_events["arrival_time"].dtype

    update_df = (
        pl.DataFrame([
            {"fzg_id": k[0], "drop_row_idx": k[1], **v}
            for k, v in updates.items()
        ])
        .with_columns([
            pl.col("arrival_time").cast(dt_dtype),
            pl.col("departure_time").cast(dt_dtype),
        ])
    )

    result = (
        se
        .join(update_df, on=["fzg_id", "drop_row_idx"], how="left", suffix="_r")
        .with_columns([
            pl.when(pl.col("arrival_time_r").is_not_null())
              .then(pl.col("arrival_time_r"))
              .otherwise(pl.col("arrival_time"))
              .alias("arrival_time"),
            pl.when(pl.col("departure_time_r").is_not_null())
              .then(pl.col("departure_time_r"))
              .otherwise(pl.col("departure_time"))
              .alias("departure_time"),
            pl.when(pl.col("stop_status_r").is_not_null())
              .then(pl.col("stop_status_r"))
              .otherwise(pl.col("stop_status"))
              .alias("stop_status"),
        ])
        .drop(["arrival_time_r", "departure_time_r", "stop_status_r",
               "_prev_drop", "_next_drop", "_max_row", "_case"])
    )

    return result
