import polars as pl

_WEATHER_NUMERIC_COLS = ["TMK", "TXK", "TNK", "TGK", "RSK", "RSKF", "SDK", "SHK_TAG", "FM"]


def load_weather(raw_path: str) -> pl.DataFrame:
    """
    Load and clean DWD daily weather data for Dresden (station 01048).

    Parameters
    ----------
    raw_path : str
        Path to the raw DWD file (produkt_klima_tag_*.txt)

    Returns
    -------
    pl.DataFrame
        Cleaned weather table with columns:
          MESS_DATUM (date),
          TMK, TXK, TNK, TGK (temperature °C),
          RSK (precipitation mm), RSKF (precipitation type),
          SDK (sunshine hours), SHK_TAG (snow depth cm),
          FM (mean wind speed m/s)
        DWD missing value -999 replaced with null.
    """
    raw = pl.read_csv(
        raw_path,
        separator=";",
        infer_schema_length=1000,
    )

    # Strip whitespace from column names
    raw = raw.rename({col: col.strip() for col in raw.columns})

    return (
        raw
        .select(["MESS_DATUM"] + _WEATHER_NUMERIC_COLS)
        # Convert YYYYMMDD to date type
        .with_columns(
            pl.col("MESS_DATUM").cast(pl.Utf8).str.strip_chars().str.to_date(format="%Y%m%d")
        )
        # Cast to float (handles leading/trailing whitespace in values)
        .with_columns([
            pl.col(c).cast(pl.Utf8).str.strip_chars().cast(pl.Float64).alias(c)
            for c in _WEATHER_NUMERIC_COLS
        ])
        # Replace DWD missing value indicator with null
        .with_columns([
            pl.when(pl.col(c) == -999.0).then(None).otherwise(pl.col(c)).alias(c)
            for c in _WEATHER_NUMERIC_COLS
        ])
    )


def join_weather(core: pl.DataFrame, weather: pl.DataFrame) -> pl.DataFrame:
    """
    Join weather data to the core stop event table by date.

    The core table is NOT modified. A new table is returned.

    Parameters
    ----------
    core : pl.DataFrame
        Core stop event table. Required columns: arrival_time (datetime)
    weather : pl.DataFrame
        Output of load_weather(). Required columns: MESS_DATUM (date)

    Returns
    -------
    pl.DataFrame
        New table with all core columns plus weather columns appended.
        Every stop event gets the weather observation for its arrival date.
    """
    return (
        core
        .with_columns(
            pl.col("arrival_time").dt.date().alias("MESS_DATUM")
        )
        .join(weather, on="MESS_DATUM", how="left")
        .drop("MESS_DATUM")
    )


# ── Vehicle information ───────────────────────────────────────────────────────

def _extract_fzg_nr_int(fzg_nr_str: str) -> int | None:
    """
    Extract a matchable integer from a raw fzg_nr string.
    Tram:  '232 905-8' -> 2905  (digits at positions 2-5)
    Bus:   '464 001-3' -> 464001 (first 6 digits)
    """
    if fzg_nr_str is None:
        return None
    import re
    digits = re.sub(r"[^0-9]", "", str(fzg_nr_str))
    if not digits:
        return None
    if digits.startswith("232"):
        return int(digits[2:6])
    else:
        return int(digits[:6])


def load_vehicle_info(raw_vehicle_path: str, lookup_path: str) -> pl.DataFrame:
    """
    Build vehicle information table by matching each vehicle's fzg_nr
    against the lookup table's number ranges.

    Parameters
    ----------
    raw_vehicle_path : str
        Path to vehicle_data_2025_07_22.csv
    lookup_path : str
        Path to dvb_fahrzeug_info.csv with columns:
          typ, fzg_nr_from, fzg_nr_to, fahrzueg_type,
          fahrgasttüren, länge_m, sitzplätze, stehplätze

    Returns
    -------
    pl.DataFrame
        One row per vehicle (fzg_id), columns:
          fzg_id, fzg_nr, fzg_nr_int, typ,
          fahrzueg_type, fahrgasttüren, länge_m,
          sitzplätze, stehplätze, kapazitaet
    """
    # ── Step 1: load and clean raw vehicle data ───────────────
    raw = pl.read_csv(raw_vehicle_path)

    # Keep only real vehicles (non-numeric typ, fzg_nr not null)
    vehicles = (
        raw
        .filter(
            pl.col("typ").is_not_null() &
            ~pl.col("typ").str.contains(r"^\d+$") &
            pl.col("fzg_nr").is_not_null()
        )
        .select(["fzg_id", "fzg_nr", "typ"])
        .unique(subset=["fzg_id"])
    )

    # Extract matchable integer from fzg_nr
    fzg_nr_ints = [
        _extract_fzg_nr_int(v)
        for v in vehicles["fzg_nr"].to_list()
    ]
    vehicles = vehicles.with_columns(
        pl.Series("fzg_nr_int", fzg_nr_ints, dtype=pl.Int64)
    )

    # ── Step 2: load lookup table ─────────────────────────────
    lookup = (
        pl.read_csv(lookup_path)
        .rename({col: col.strip() for col in
                 pl.read_csv(lookup_path).columns})
        .with_columns([
            pl.col("fzg_nr_from").cast(pl.Int64),
            pl.col("fzg_nr_to").cast(pl.Int64),
            (pl.col("sitzplätze") + pl.col("stehplätze"))
            .alias("kapazitaet"),
        ])
    )

    # ── Step 3: range join — match fzg_nr_int to lookup ranges ─
    # Polars doesn't support range joins natively, so we do a
    # cross join and filter — efficient enough for small lookup tables
    matched = (
        vehicles
        .join(lookup, how="cross")
        .filter(
            (pl.col("fzg_nr_int") >= pl.col("fzg_nr_from")) &
            (pl.col("fzg_nr_int") <= pl.col("fzg_nr_to"))
        )
        .select([
            "fzg_id", "fzg_nr", "fzg_nr_int", "typ",
            "fahrzueg_type", "fahrgasttüren", "länge_m",
            "sitzplätze", "stehplätze", "kapazitaet",
        ])
    )

    # Vehicles that didn't match any range — keep with nulls
    unmatched = (
    vehicles
    .filter(~pl.col("fzg_id").is_in(matched["fzg_id"]))
    .with_columns([
        pl.lit(None).cast(pl.Utf8).alias("fahrzueg_type"),
        pl.lit(None).cast(pl.Int64).alias("fahrgasttüren"),
        pl.lit(None).cast(pl.Float64).alias("länge_m"),
        pl.lit(None).cast(pl.Int64).alias("sitzplätze"),
        pl.lit(None).cast(pl.Int64).alias("stehplätze"),
        pl.lit(None).cast(pl.Int64).alias("kapazitaet"),
    ])
    .select([
        "fzg_id", "fzg_nr", "fzg_nr_int", "typ",
        "fahrzueg_type", "fahrgasttüren", "länge_m",
        "sitzplätze", "stehplätze", "kapazitaet",
    ])
)

    return pl.concat([matched, unmatched]).sort("fzg_id")


def join_vehicle_info(core: pl.DataFrame, vehicle_info: pl.DataFrame) -> pl.DataFrame:
    """
    Join vehicle information to the core stop event table by fzg_id.

    The core table is NOT modified. A new table is returned.

    Parameters
    ----------
    core : pl.DataFrame
        Core stop event table. Required columns: fzg_id
    vehicle_info : pl.DataFrame
        Output of load_vehicle_info().

    Returns
    -------
    pl.DataFrame
        New table with all core columns plus vehicle info columns appended.
    """
    vehicle_cols = [
        "fzg_id", "fahrzueg_type", "fahrgasttüren",
        "länge_m", "sitzplätze", "stehplätze", "kapazitaet",
    ]
    return core.join(
    vehicle_info.select(vehicle_cols).with_columns(
        pl.col("fzg_id").cast(pl.Int64)
    ),
    on="fzg_id",
    how="left",
)


def load_special_events(events_path: str) -> pl.DataFrame:
    """
    Load special events table.

    Parameters
    ----------
    events_path : str
        Path to special_events.csv with columns:
          event_id, date, event_type, location, latitude, longitude

    Returns
    -------
    pl.DataFrame
        Cleaned special events table with date as date type.
    """
    return (
        pl.read_csv(events_path)
        .with_columns(
            pl.col("date").str.to_date(format="%Y-%m-%d")
        )
    )


def join_special_events(core: pl.DataFrame, events: pl.DataFrame) -> pl.DataFrame:
    """
    Join special events to the core stop event table by date.

    The core table is NOT modified. A new table is returned.
    Adds a boolean column 'has_special_event' and event details.

    Parameters
    ----------
    core : pl.DataFrame
        Core stop event table. Required columns: arrival_time (datetime)
    events : pl.DataFrame
        Output of load_special_events().

    Returns
    -------
    pl.DataFrame
        New table with all core columns plus:
          has_special_event (bool),
          event_type, event_location, event_latitude, event_longitude
    """
    events_renamed = events.rename({
        "location":  "event_location",
        "latitude":  "event_latitude",
        "longitude": "event_longitude",
    }).drop("event_id")

    return (
        core
        .with_columns(
            pl.col("arrival_time").dt.date().alias("date")
        )
        .join(events_renamed, on="date", how="left")
        .with_columns(
            pl.col("event_type").is_not_null().alias("has_special_event")
        )
        .drop("date")
    )