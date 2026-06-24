"""
Prepare feature matrices for the computed-vs-fixed dwell_time ablation.

Outputs (saved to results/):
  variant1_train.parquet   computed dwell_time, training rows
  variant1_test.parquet    computed dwell_time, test rows
  variant2_train.parquet   fixed dwell_time (per-linie train mean), training rows
  variant2_test.parquet    fixed dwell_time (per-linie train mean), test rows
  split_info.json          test date, row counts
"""

import json
from pathlib import Path

import polars as pl

PARQUET = Path("../../data/processed/core_stop_events.parquet")
OUT     = Path("results")
OUT.mkdir(exist_ok=True)

DWELL_NODOOR_FILL = 40      # seconds, for no_door stops
DWELL_CLIP        = (0, 120)
TRAVEL_CLIP       = (10, 600)

# ── 1. load ──────────────────────────────────────────────────────────────────
df = pl.read_parquet(PARQUET).drop("has_traffic_signal")

print(f"Loaded: {len(df):,} rows")

# ── 2. service_date (UTC+2) ───────────────────────────────────────────────────
df = df.with_columns(
    pl.col("arrival_time")
      .dt.convert_time_zone("Europe/Berlin")
      .dt.date()
      .alias("service_date")
)

# ── 3. dwell_time: fix no_door sentinel, filter leaking negatives ─────────────
df = df.filter(pl.col("dwell_time") >= -1)          # drop anything below -1 (shouldn't exist)
df = df.with_columns(
    pl.when(pl.col("dwell_time") == -1)
      .then(pl.lit(float(DWELL_NODOOR_FILL)))
      .otherwise(pl.col("dwell_time"))
      .alias("dwell_time")
)

# ── 4. time features ──────────────────────────────────────────────────────────
df = df.with_columns([
    pl.col("arrival_time").dt.convert_time_zone("Europe/Berlin")
      .dt.hour().alias("hour_of_day"),
    pl.col("arrival_time").dt.convert_time_zone("Europe/Berlin")
      .dt.weekday().alias("day_of_week"),   # 1=Mon … 7=Sun
])

# ── 5. target: travel_time to next stop ───────────────────────────────────────
df = (
    df
    .sort(["fahrt_id", "service_date", "stop_index"])
    .with_columns(
        pl.col("travel_time")
          .shift(-1)
          .over(["fahrt_id", "service_date"])
          .alias("target")
    )
    .filter(pl.col("target").is_not_null())
)

# ── 6. clip outliers ──────────────────────────────────────────────────────────
df = df.with_columns([
    pl.col("dwell_time").clip(*DWELL_CLIP),
    pl.col("target").clip(*TRAVEL_CLIP),
])

# ── 7. stop_status encoding ───────────────────────────────────────────────────
status_map = {"normal": 0, "multi_door": 1, "no_door": 2}
df = df.with_columns(
    pl.col("stop_status").replace(status_map).cast(pl.Int8)
)

# ── 8. select features + target ───────────────────────────────────────────────
FEATURES = [
    "dwell_time",
    "delay_calculated_sec",
    "stop_index",
    "besetztgrad",
    "linie",
    "ort_nr_start",
    "stop_status",
    "is_peak_hour",
    "is_workday",
    "hour_of_day",
    "day_of_week",
]
KEEP = ["fahrt_id", "service_date"] + FEATURES + ["target"]
df   = df.select(KEEP)

# ── 9. train / test split by date ─────────────────────────────────────────────
dates = sorted(df["service_date"].unique().to_list())
print("\nSamples per service_date:")
for d in dates:
    n = (df["service_date"] == d).sum()
    print(f"  {d}  {n:>8,}")

# skip trailing micro-days (< 10k rows, UTC+2 timezone overflow)
date_counts = {d: (df["service_date"] == d).sum() for d in dates}
full_dates  = [d for d in dates if date_counts[d] >= 10_000]

test_date   = full_dates[-1]
valid_dates = full_dates[-3:-1]   # last 2 full days before test → validation for early stopping

train_df = df.filter(~pl.col("service_date").is_in([test_date] + valid_dates))
valid_df = df.filter(pl.col("service_date").is_in(valid_dates))
test_df  = df.filter(pl.col("service_date") == test_date)

print(f"\nTrain: {len(train_df):,}  |  Valid: {len(valid_df):,}  |  Test: {len(test_df):,}")
print(f"Test date: {test_date}")

# ── 10. variant 2: per-linie dwell mean from train set only ───────────────────
linie_dwell_mean = (
    train_df
    .group_by("linie")
    .agg(pl.col("dwell_time").mean().alias("dwell_mean"))
)

def apply_fixed_dwell(frame: pl.DataFrame) -> pl.DataFrame:
    return (
        frame
        .join(linie_dwell_mean, on="linie", how="left")
        .with_columns(
            pl.col("dwell_mean")
              .fill_null(pl.col("dwell_mean").mean())  # global fallback for unseen linien
              .alias("dwell_time")
        )
        .drop("dwell_mean")
    )

train2_df = apply_fixed_dwell(train_df)
valid2_df = apply_fixed_dwell(valid_df)
test2_df  = apply_fixed_dwell(test_df)

# ── 11. save ──────────────────────────────────────────────────────────────────
train_df.write_parquet(OUT / "variant1_train.parquet")
valid_df.write_parquet(OUT / "variant1_valid.parquet")
test_df.write_parquet(OUT  / "variant1_test.parquet")

train2_df.write_parquet(OUT / "variant2_train.parquet")
valid2_df.write_parquet(OUT / "variant2_valid.parquet")
test2_df.write_parquet(OUT  / "variant2_test.parquet")

info = {
    "test_date":   str(test_date),
    "valid_dates": [str(d) for d in valid_dates],
    "n_train":     len(train_df),
    "n_valid":     len(valid_df),
    "n_test":      len(test_df),
    "features":    FEATURES,
}
(OUT / "split_info.json").write_text(json.dumps(info, indent=2))

print("\nSaved to results/. Done.")
