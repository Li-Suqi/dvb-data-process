import polars as pl


def add_binary_features(df: pl.DataFrame) -> pl.DataFrame:
    """
    Add derived binary features to the stop event table.

    Parameters
    ----------
    df : pl.DataFrame
        Stop event table. Required columns:
          arrival_time (datetime, UTC)

    Returns
    -------
    pl.DataFrame
        Input dataframe with three additional columns:
          is_peak_hour        (Int8, 0/1)
          is_workday          (Int8, 0/1)
          has_traffic_signal  (Int8, always null — awaiting infrastructure data)
    """
    return df.with_columns([
        _is_peak_hour(),
        _is_workday(),
        _has_traffic_signal(),
    ])


# ── internal helpers ──────────────────────────────────────────────────────────

def _is_peak_hour() -> pl.Expr:
    """
    1 if arrival_time falls within:
      - Morning peak: 07:00 – 09:00
      - Evening peak: 16:00 – 19:00
    """
    hour = pl.col("arrival_time").dt.hour()
    morning = (hour >= 7)  & (hour < 9)
    evening = (hour >= 16) & (hour < 19)
    return (
        (morning | evening)
        .cast(pl.Int8)
        .alias("is_peak_hour")
    )


def _is_workday() -> pl.Expr:
    """
    1 if arrival_time falls on Monday–Friday (weekday() 0=Mon, 6=Sun).
    Note: public holidays are not excluded — requires a separate calendar.
    """
    return (
        (pl.col("arrival_time").dt.weekday() < 5)
        .cast(pl.Int8)
        .alias("is_workday")
    )


def _has_traffic_signal() -> pl.Expr:
    """
    Placeholder — infrastructure data not yet available.
    Will be 1 if the link between this stop and the previous stop
    passes through a traffic signal, matched from infrastructure data.
    """
    return pl.lit(None).cast(pl.Int8).alias("has_traffic_signal")