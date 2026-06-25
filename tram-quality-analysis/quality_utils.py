import polars as pl
import json
from pathlib import Path

DATA_PATH = "../data/vehicle_positions_week.csv"
OUTPUT_DIR = "quality_report"

def analyze_redundant(df: pl.DataFrame, output_dir: str = "quality_report") -> dict:
    """
    A1: Redundant Data Analysis
    
    Detects two types of duplicate records in raw DVB AVL data:
    - Hard duplicates: rows where every field is identical
    - Soft duplicates: rows sharing the same (fzg_id, tst_iso) key
      but differing in at least one other field (e.g. caused by MQTT retransmission)

    Parameters
    ----------
    df : pl.DataFrame
        Raw DVB vehicle position data.
    output_dir : str
        Directory to save CSV and JSON outputs.

    Returns
    -------
    dict with keys:
        total_rows            : int
        hard_dup_count        : int
        hard_dup_rate         : float
        soft_dup_extra_rows   : int  — number of redundant rows beyond the first occurrence
        soft_dup_affected_groups : int  — number of (fzg_id, tst_iso) groups with conflicts
        soft_dup_rate         : float
        soft_dup_by_line      : list[dict]  — per-line breakdown
    """
    Path(output_dir).mkdir(exist_ok=True)
    total_rows = len(df)

    # --- Hard duplicates ---
    hard_dup_count = int(df.is_duplicated().sum())

    # --- Soft duplicates ---
    soft_dup_groups = (
        df.group_by(["fzg_id", "tst_iso"])
        .agg(pl.count().alias("count"))
        .filter(pl.col("count") > 1)
    )
    soft_dup_extra_rows = int((soft_dup_groups["count"] - 1).sum())
    soft_dup_affected_groups = len(soft_dup_groups)

    # --- Per-line breakdown ---
    soft_dup_keys = soft_dup_groups.select(["fzg_id", "tst_iso"])
    soft_dup_row_df = df.join(soft_dup_keys, on=["fzg_id", "tst_iso"], how="inner")
    total_by_line = df.group_by("linie").agg(pl.count().alias("total_rows"))

    soft_dup_by_line = (
        soft_dup_row_df
        .group_by("linie")
        .agg(pl.count().alias("soft_dup_rows"))
        .join(total_by_line, on="linie", how="left")
        .with_columns(
            (pl.col("soft_dup_rows") / pl.col("total_rows")).alias("soft_dup_rate")
        )
        .sort("soft_dup_rate", descending=True)
    )

    summary = {
        "total_rows": total_rows,
        "hard_dup_count": hard_dup_count,
        "hard_dup_rate": round(hard_dup_count / total_rows, 6),
        "soft_dup_extra_rows": soft_dup_extra_rows,
        "soft_dup_affected_groups": soft_dup_affected_groups,
        "soft_dup_rate": round(soft_dup_extra_rows / total_rows, 6),
        "soft_dup_by_line": soft_dup_by_line.to_dicts(),
    }

    # --- Save outputs ---
    with open(f"{output_dir}/A1_redundant_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    soft_dup_by_line.write_csv(f"{output_dir}/A1_soft_dup_by_line.csv")

    return summary

def analyze_missing(df: pl.DataFrame, output_dir: str = "quality_report",
                    gap_threshold_sec: int = 30) -> dict:
    """
    A2: Missing Data Analysis

    Detects missing values at three levels:
    1. Field level: null rate per column, grouped by importance
    2. Trip level: timestamp gaps > gap_threshold_sec within the same fahrt_id,
       indicating lost AVL records mid-trip
    3. Line level: null rates of core fields grouped by linie

    Core fields (directly required by the stop detection pipeline):
        distanz, tuerkriterium, fahrt_id, ort_nr_start

    Parameters
    ----------
    df : pl.DataFrame
        Raw DVB vehicle position data.
    output_dir : str
        Directory to save CSV and JSON outputs.
    gap_threshold_sec : int
        Threshold in seconds above which a within-trip timestamp gap is
        considered a record interruption. Default is 30.

    Returns
    -------
    dict with keys:
        total_rows              : int
        field_missing           : list[dict]  — null rate per column
        core_completeness_rate  : float  — share of rows where all 4 core fields are non-null
        trip_gap_count          : int  — number of within-trip gaps > threshold
        trip_gap_rate           : float  — gap_count / total_rows
        affected_trips          : int  — number of fahrt_id with at least one gap
        missing_by_line         : list[dict]  — per-line core field null rates
    """
    Path(output_dir).mkdir(exist_ok=True)
    total_rows = len(df)

    CORE_FIELDS = ["distanz", "tuerkriterium", "fahrt_id", "ort_nr_start"]
    ANALYSIS_FIELDS = ["lage", "besetztgrad", "pos_lat", "pos_lon"]
    SECONDARY_FIELDS = ["linie_text", "zieltext", "status_text"]
    IGNORE_FIELDS = ["topic", "qos", "retain", "payloadlen"]

    all_groups = {
        "core": CORE_FIELDS,
        "analysis": ANALYSIS_FIELDS,
        "secondary": SECONDARY_FIELDS,
        "ignore": IGNORE_FIELDS,
    }

    # --- Field-level null rates ---
    field_missing = []
    for group, cols in all_groups.items():
        for col in cols:
            if col not in df.columns:
                continue
            null_count = int(df[col].is_null().sum())
            field_missing.append({
                "column": col,
                "group": group,
                "null_count": null_count,
                "null_rate": round(null_count / total_rows, 6),
            })

    # --- Core completeness: all 4 core fields non-null ---
    existing_core = [c for c in CORE_FIELDS if c in df.columns]
    core_complete_mask = pl.all_horizontal(
        [pl.col(c).is_not_null() for c in existing_core]
    )
    core_completeness_rate = round(
        df.filter(core_complete_mask).height / total_rows, 6
    )

    # --- Trip-level gaps ---
    gaps = (
        df.sort(["fahrt_id", "tst_iso"])
        .with_columns(
            pl.col("tst_iso").diff().over("fahrt_id")
            .dt.total_seconds()
            .alias("gap_sec")
        )
        .filter(pl.col("gap_sec") > gap_threshold_sec)
    )
    trip_gap_count = len(gaps)
    affected_trips = gaps["fahrt_id"].n_unique()

    # Save gap details
    gaps.select(["fahrt_id", "fzg_id", "linie", "tst_iso", "gap_sec"]) \
        .write_csv(f"{output_dir}/A2_trip_gaps.csv")

    # --- Per-line core field null rates ---
    missing_by_line = (
        df.group_by("linie")
        .agg([
            pl.count().alias("total_rows"),
            *[
                pl.col(c).is_null().mean().alias(f"{c}_null_rate")
                for c in existing_core
            ],
        ])
        .sort("linie")
    )
    missing_by_line.write_csv(f"{output_dir}/A2_missing_by_line.csv")

    summary = {
        "total_rows": total_rows,
        "field_missing": field_missing,
        "core_completeness_rate": core_completeness_rate,
        "trip_gap_count": trip_gap_count,
        "trip_gap_rate": round(trip_gap_count / total_rows, 6),
        "affected_trips": int(affected_trips),
        "missing_by_line": missing_by_line.to_dicts(),
    }

    with open(f"{output_dir}/A2_missing_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return summary

# ── A3 private helpers ──────────────────────────────────────────────────────

def _detect_gps_zero(df: pl.DataFrame) -> pl.DataFrame:
    """
    N1: Detect records where GPS coordinates are exactly zero.
    These indicate an uninitialised GPS module (observed as a known
    vehicle-specific artefact in the DVB dataset).

    Returns the subset of rows where pos_lat == 0 or pos_lon == 0.
    """
    return df.filter(
        (pl.col("pos_lat") == 0) | (pl.col("pos_lon") == 0)
    )


def _detect_distanz_spike(
    df: pl.DataFrame,
    threshold: int = 300,
) -> pl.DataFrame:
    """
    N2: Detect single-step distanz increases exceeding the threshold.
    ...
    """
    return (
        df.sort(["fahrt_id", "tst_iso"])
        .with_columns(
            pl.col("distanz")
            .diff()
            .over("fahrt_id")
            .alias("distanz_step")
        )
        .filter(pl.col("distanz_step") > threshold)
        .drop("distanz_step")  
    )


def _detect_distanz_small_drop(
    df: pl.DataFrame,
    threshold: int = 50,
) -> pl.DataFrame:
    """
    N3: Detect distanz drops where the value before the drop is below
    the threshold...
    """
    return (
        df.sort(["fahrt_id", "tst_iso"])
        .with_columns(
            pl.col("distanz")
            .diff()
            .over("fahrt_id")
            .alias("distanz_diff")
        )
        .filter(pl.col("distanz_diff") < 0)
        .with_columns(
            (pl.col("distanz") - pl.col("distanz_diff"))
            .alias("distanz_before_drop")
        )
        .filter(pl.col("distanz_before_drop") < threshold)
        .drop(["distanz_diff", "distanz_before_drop"])
    )

def _detect_lage_extreme(
    df: pl.DataFrame,
    threshold: int = 900,
) -> pl.DataFrame:
    """
    N4: Detect records with an extreme self-reported delay magnitude.
    Values where |lage| exceeds `threshold` seconds (default 900 s = 15 min)
    are considered unreliable for downstream modelling. In the DVB dataset,
    these are predominantly associated with dispatch-intervention events
    (status_text == 'dispo') rather than genuine operational delays.

    Returns rows where abs(lage) > threshold.
    """
    return df.filter(pl.col("lage").abs() > threshold)


def _detect_lage_default_zero(df: pl.DataFrame) -> pl.DataFrame:
    """
    N5: Detect records where lage == 0 but the vehicle is not in a
    normal on-schedule state (status_text != 'planm').
    In these cases, zero is a system placeholder rather than a true
    on-time reading, making the value misleading for delay analysis.

    Returns rows where lage == 0 and status_text is not 'planm'.
    """
    return df.filter(
        (pl.col("lage") == 0) &
        (pl.col("status_text") != "planm")
    )


def _detect_bad_status(df: pl.DataFrame) -> pl.DataFrame:
    """
    N6: Detect records with a status_text indicating unreliable data.
    The following statuses are treated as noisy:
      - posUnklar  : vehicle position unknown (GPS signal lost)
      - ohneFahrt  : vehicle moving without an associated scheduled trip
      - keinFunk   : radio communication lost

    Returns rows matching any of these statuses.
    """
    bad_statuses = ["posUnklar", "ohneFahrt", "keinFunk"]
    return df.filter(pl.col("status_text").is_in(bad_statuses))


# ── A3 public function ───────────────────────────────────────────────────────

def analyze_noisy(
    df: pl.DataFrame,
    output_dir: str = "quality_report",
    distanz_increase_threshold: int = 300,
    distanz_drop_threshold: int = 50,
    lage_extreme_threshold: int = 900,
) -> dict:
    """
    A3: Noisy Data Analysis.

    Detects six categories of physically or operationally implausible
    values in raw DVB AVL data, using private helper functions for each
    category. Results are aggregated globally and broken down by line.

    Detection categories:
        N1  GPS zero coordinates     (_detect_gps_zero)
        N2  distanz single-step spike(_detect_distanz_spike)
        N3  distanz small drop       (_detect_distanz_small_drop)
        N4  lage extreme value       (_detect_lage_extreme)
        N5  lage default zero        (_detect_lage_default_zero)
        N6  bad status_text          (_detect_bad_status)

    Parameters
    ----------
    df : pl.DataFrame
        Raw DVB vehicle position data.
    output_dir : str
        Directory to save CSV and JSON outputs.
    distanz_increase_threshold : int
        Single-step distanz increase above which a record is flagged
        as a GPS spike. Default 300 m (above P99.0 of observed increases).
    distanz_drop_threshold : int
        distanz_before_drop below which a drop is flagged as spurious.
        Default 50 m.
    lage_extreme_threshold : int
        Absolute lage value above which a record is flagged as extreme.
        Default 900 s (15 min).

    Returns
    -------
    dict with keys:
        total_rows           : int
        n1_gps_zero          : dict  — count, rate, affected_vehicles
        n2_distanz_spike     : dict  — count, rate
        n3_distanz_small_drop: dict  — count, rate
        n4_lage_extreme      : dict  — count, rate
        n5_lage_default_zero : dict  — count, rate, by_status (list[dict])
        n6_bad_status        : dict  — count, rate, by_status (list[dict])
        noisy_union_count    : int   — row count of the union of all flagged rows
        noisy_union_rate     : float
        noisy_by_line        : list[dict]
    """
    Path(output_dir).mkdir(exist_ok=True)
    total_rows = len(df)

    # --- Run each detector ---
    n1 = _detect_gps_zero(df)
    n2 = _detect_distanz_spike(df, distanz_increase_threshold)
    n3 = _detect_distanz_small_drop(df, distanz_drop_threshold)
    n4 = _detect_lage_extreme(df, lage_extreme_threshold)
    n5 = _detect_lage_default_zero(df)
    n6 = _detect_bad_status(df)

    # --- Build per-category summaries ---
    def _summary(subset: pl.DataFrame) -> dict:
        return {
            "count": len(subset),
            "rate": round(len(subset) / total_rows, 6),
        }

    n1_summary = {
        **_summary(n1),
        "affected_vehicles": n1["fzg_id"].unique().to_list(),
    }
    n5_summary = {
        **_summary(n5),
        "by_status": (
            n5.group_by("status_text")
            .len()
            .sort("len", descending=True)
            .to_dicts()
        ),
    }
    n6_summary = {
        **_summary(n6),
        "by_status": (
            n6.group_by("status_text")
            .len()
            .sort("len", descending=True)
            .to_dicts()
        ),
    }

    # --- Union of all flagged rows (avoid double-counting) ---
    flagged_idx = (
        pl.concat([
            n1.with_row_index("_row_idx"),
            n2.with_row_index("_row_idx"),
            n3.with_row_index("_row_idx"),
            n4.with_row_index("_row_idx"),
            n5.with_row_index("_row_idx"),
            n6.with_row_index("_row_idx"),
        ])
        .select("_row_idx")
        .unique()
    )
    noisy_union_count = len(flagged_idx)

    # --- Per-line breakdown ---
    def _flag_col(subset: pl.DataFrame, name: str) -> pl.DataFrame:
        return subset.select(
            pl.col("fzg_id"), pl.col("tst_iso"), pl.col("linie")
        ).with_columns(pl.lit(True).alias(name))

    noisy_by_line = (
        df.with_row_index("_row_idx")
        .join(
            df.with_row_index("_row_idx")
            .with_columns([
                ((pl.col("pos_lat") == 0) | (pl.col("pos_lon") == 0))
                .alias("n1"),
                pl.lit(False).alias("n2"),  # spike needs diff, added below
                pl.lit(False).alias("n3"),
                (pl.col("lage").abs() > lage_extreme_threshold)
                .alias("n4"),
                ((pl.col("lage") == 0) & (pl.col("status_text") != "planm"))
                .alias("n5"),
                pl.col("status_text").is_in(["posUnklar", "ohneFahrt", "keinFunk"])
                .alias("n6"),
            ])
            .with_columns(
                pl.col("distanz").diff().over("fahrt_id").alias("_step")
            )
            .with_columns(
                (pl.col("_step") > distanz_increase_threshold).alias("n2")
            )
            .select(["_row_idx", "n1", "n2", "n3", "n4", "n5", "n6"]),
            on="_row_idx",
            how="left",
        )
        .group_by("linie")
        .agg([
            pl.count().alias("total_rows"),
            pl.col("n1").sum().alias("n1_count"),
            pl.col("n2").sum().alias("n2_count"),
            pl.col("n4").sum().alias("n4_count"),
            pl.col("n5").sum().alias("n5_count"),
            pl.col("n6").sum().alias("n6_count"),
            (pl.col("n1") | pl.col("n2") |
             pl.col("n4") | pl.col("n5") | pl.col("n6"))
            .sum().alias("any_noisy_count"),
        ])
        .with_columns(
            (pl.col("any_noisy_count") / pl.col("total_rows"))
            .alias("noisy_rate")
        )
        .sort("noisy_rate", descending=True)
    )

    noisy_by_line.write_csv(f"{output_dir}/A3_noisy_by_line.csv")

    summary = {
        "total_rows": total_rows,
        "n1_gps_zero": n1_summary,
        "n2_distanz_spike": _summary(n2),
        "n3_distanz_small_drop": _summary(n3),
        "n4_lage_extreme": _summary(n4),
        "n5_lage_default_zero": n5_summary,
        "n6_bad_status": n6_summary,
        "noisy_union_count": noisy_union_count,
        "noisy_union_rate": round(noisy_union_count / total_rows, 6),
        "noisy_by_line": noisy_by_line.to_dicts(),
    }

    with open(f"{output_dir}/A3_noisy_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return summary