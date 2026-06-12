# DVB ATP Pipeline — Project Summary

> **Context:** Arrival Time Prediction (ATP) preprocessing pipeline for Dresden's DVB public transit network.
> One week of raw ITCS vehicle telemetry (2025-07-28 → 2025-08-03, 538 vehicles, 10.87 M position records) is transformed into a clean, analysis-ready stop event table enriched with weather, vehicle type, and special event data.

---

## 1. Repository Layout

```
dvb_atp_pipeline/
├── pipeline/                  # Reusable Python modules
│   ├── detector.py            # Stop-event detection
│   ├── timetable.py           # Timetable expansion + delay computation
│   ├── extensions.py          # Weather / vehicle / special-event joins
│   ├── feature_builder.py     # Derived binary features
│   ├── expand_timetable.py    # Standalone EDA script (Step 2)
│   └── match_arrivals.py      # Standalone EDA script (Step 3)
├── notebooks/
│   ├── 01_pipeline.ipynb      # End-to-end core table construction
│   ├── 02_extensions.ipynb    # Extension table construction
│   └── explore_vehicle_types.ipynb
├── data/
│   ├── regular_linie_week.csv          # Raw vehicle positions (10.87 M rows)
│   ├── timetable_trips_2025_07_22.csv  # Raw timetable (263,865 rows)
│   ├── vehicle_data_2025_07_22.csv     # Raw vehicle metadata
│   ├── vehicle_positions_*.csv         # Auxiliary position snapshots
│   ├── external/
│   │   ├── weather/    produkt_klima_tag_*.txt  (DWD station 01048)
│   │   │               weather_dresden.parquet  (cleaned)
│   │   ├── vehicle/    dvb_fahrzeug_info.csv    (type/capacity lookup)
│   │   │               vehicle_info.parquet     (matched)
│   │   └── events/     special_events.csv       (Dresden sports events)
│   └── processed/
│       ├── core_stop_events.parquet              (994,117 rows, 18 cols)
│       ├── core_stop_events_with_weather.parquet (994,117 rows, +10 cols)
│       └── core_stop_events_with_vehicle.parquet (994,117 rows, +6 cols)
```

---

## 2. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  RAW INPUTS                                                             │
│                                                                         │
│  regular_linie_week.csv          timetable_trips_2025_07_22.csv        │
│  10,869,683 rows                 263,865 rows / 97,982 unique trips     │
│  538 vehicles, 1 week            segmente: JSON stop sequences          │
└───────────┬──────────────────────────────┬──────────────────────────────┘
            │                              │
            ▼                              ▼
┌───────────────────────┐      ┌───────────────────────────┐
│  detect_stop_events() │      │    expand_timetable()     │
│  detector.py          │      │    timetable.py           │
│                       │      │                           │
│  • Distance-drop      │      │  • Parse JSON segmente    │
│    threshold: 100 m   │      │  • Accumulate lenkzeit    │
│  • Dynamic window n   │      │  • Produce scheduled      │
│    (3–10 rows)        │      │    arrival per stop       │
│  • Door state FSM     │      │                           │
│  • Multi-door check   │      │  2,381,003 rows           │
│                       │      │  97,591 trips             │
│  1,021,431 events     │      │  2,006 unique stops       │
│  normal  82.3 %       │      └──────────────┬────────────┘
│  no_door 17.4 %       │                     │
│  multi   0.3 %        │                     │
└───────────┬───────────┘                     │
            │                                 │
            └──────────────┬──────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ match_and_compute_     │
              │ delay()                │
              │ timetable.py           │
              │                        │
              │ • Left-join on         │
              │   fahrt_id + ort_nr    │
              │ • Resolve duplicates   │
              │   by min |time diff|   │
              │ • delay_calculated     │
              │ • dwell_time           │
              │ • travel_time          │
              │                        │
              │ 994,117 matched (97.3%)│
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  add_binary_features() │
              │  feature_builder.py    │
              │                        │
              │ + is_peak_hour         │
              │ + is_workday           │
              │ + has_traffic_signal   │
              │   (null — placeholder) │
              └────────────┬───────────┘
                           │
                           ▼
          ┌────────────────────────────────────┐
          │  CORE STOP EVENT TABLE             │
          │  core_stop_events.parquet          │
          │  994,117 rows · 18 columns         │
          └──────┬───────────────┬─────────────┘
                 │               │
        ─────────┘               └──────────────
        │                                       │
        ▼                                       ▼
┌────────────────────┐              ┌──────────────────────┐
│  join_weather()    │              │  join_vehicle_info() │
│  extensions.py     │              │  extensions.py       │
│                    │              │                      │
│ DWD station 01048  │              │ Range-join fzg_nr    │
│ daily observations │              │ → type, capacity     │
│ joined by date     │              │ 86.2% matched        │
│                    │              │                      │
│ core_stop_events   │              │ core_stop_events     │
│ _with_weather      │              │ _with_vehicle        │
│ .parquet           │              │ .parquet             │
│ 100% coverage      │              │ 994,117 rows         │
└────────────────────┘              └──────────────────────┘
                 │
                 ▼
        ┌─────────────────────┐
        │ join_special_events │
        │ extensions.py       │
        │                     │
        │ Dresden sports      │
        │ events joined       │
        │ by date             │
        │ + has_special_event │
        └─────────────────────┘
```

---

## 3. Function Reference

### `pipeline/detector.py`

#### `detect_stop_events(raw_df)`

| | |
|---|---|
| **Input** | `pl.DataFrame` — raw vehicle positions (`fzg_id`, `tst_iso`, `distanz`, `tuerkriterium`, `linie`) |
| **Output** | `pl.DataFrame` — one row per stop event |
| **Output columns** | `fzg_id`, `drop_row_idx`, `drop_time`, `linie`, `window_lo`, `window_hi`, `arrival_time`, `departure_time`, `stop_status`, `door_open_count`, `door_close_count`, `door_near_drop`, `is_true_multi_door`, `delta_at_drop`, `min_distanz`, `max_distanz` |

**Algorithm:**
1. Sort by `(fzg_id, tst_iso)`, compute per-vehicle `delta = distanz[t] - distanz[t-1]`.
2. A stop event is triggered when `delta < -100 m` (vehicle resets its cumulative-distance odometer at each stop).
3. Adaptive window `n ∈ [3, 10]` rows is derived from the median inter-drop interval.
4. Within each ±n window, door-open/close transitions are counted.
5. Classification:
   - `no_door` — no door activity near the drop.
   - `multi_door` — ≥2 door openings with no intermediate distance jump (true double-stop).
   - `normal` — everything else.

---

### `pipeline/timetable.py`

#### `expand_timetable(raw_tt)`

| | |
|---|---|
| **Input** | `pl.DataFrame` — raw timetable (`fahrt_id`, `zp_abfahrt`, `segmente`, `tst_iso`) |
| **Output** | `pl.DataFrame` — stop-level schedule (`fahrt_id`, `stop_index`, `ort_nr`, `scheduled_arrival_unix`, `scheduled_arrival_time`) |

Parses the JSON `segmente` array per trip; accumulates `lenkzeit` (link travel time in seconds) to compute each stop's scheduled arrival relative to `zp_abfahrt` (departure of stop 0).

#### `match_and_compute_delay(stop_events, raw_df, timetable)`

| | |
|---|---|
| **Input** | stop events, raw positions, expanded timetable |
| **Output** | `pl.DataFrame` — 15-column matched event table |
| **Key output columns** | `delay_calculated_sec`, `delay_recorded_sec`, `dwell_time`, `travel_time`, `besetztgrad` |

Steps: enrich stop events with `fahrt_id` + `ort_nr_start` from raw positions → left-join to timetable → resolve duplicate matches by minimum `|arrival_time − scheduled_arrival_time|` → compute all derived time fields.

---

### `pipeline/extensions.py`

#### `load_weather(raw_path) → pl.DataFrame`

| | |
|---|---|
| **Input** | Path to DWD semicolon-delimited file |
| **Output** | `MESS_DATUM` (date), `TMK`, `TXK`, `TNK`, `TGK` (°C), `RSK` (mm), `RSKF`, `SDK` (h), `SHK_TAG` (cm), `FM` (m/s) |

Strips whitespace from column names, casts all numeric fields to `Float64`, replaces DWD missing-value sentinel `−999` with `null`.

#### `join_weather(core, weather) → pl.DataFrame`

Left-joins `weather` to `core` by extracting `arrival_time.date()`. Returns core + all weather columns. No rows dropped.

#### `load_vehicle_info(raw_vehicle_path, lookup_path) → pl.DataFrame`

| | |
|---|---|
| **Input** | `vehicle_data_2025_07_22.csv` + `dvb_fahrzeug_info.csv` |
| **Output** | `fzg_id`, `fzg_nr`, `fzg_nr_int`, `typ`, `fahrzueg_type`, `fahrgasttüren`, `länge_m`, `sitzplätze`, `stehplätze`, `kapazitaet` |

Extracts a matchable integer from the raw `fzg_nr` string (tram: digits 2–5; bus: first 6 digits), then performs a cross-join + filter range match against the lookup table. Unmatched vehicles are kept with null attributes.

#### `join_vehicle_info(core, vehicle_info) → pl.DataFrame`

Left-joins vehicle attributes to core on `fzg_id`. 86.2% of stop events receive vehicle type information.

#### `load_special_events(events_path) → pl.DataFrame`

Loads `special_events.csv` (columns: `event_id`, `date`, `event_type`, `location`, `latitude`, `longitude`), casts `date` to date type.

#### `join_special_events(core, events) → pl.DataFrame`

Left-joins events to core by date. Adds `has_special_event` (bool), `event_type`, `event_location`, `event_latitude`, `event_longitude`.

---

### `pipeline/feature_builder.py`

#### `add_binary_features(df) → pl.DataFrame`

| | |
|---|---|
| **Input** | Any DataFrame with `arrival_time` (datetime, UTC) |
| **Output** | Input + `is_peak_hour` (Int8), `is_workday` (Int8), `has_traffic_signal` (Int8, all null) |

| Feature | Definition |
|---|---|
| `is_peak_hour` | 1 if hour ∈ [7,9) ∪ [16,19) |
| `is_workday` | 1 if weekday ∈ {Mon, Tue, Wed, Thu, Fri} |
| `has_traffic_signal` | Placeholder — infrastructure data pending |

---

## 4. Core Stop Event Table Schema

> Saved as `data/processed/core_stop_events.parquet` — **994,117 rows, 18 columns**

| Column | Type | Nulls | Description |
|---|---|---|---|
| `fzg_id` | Int64 | 0 % | Vehicle ID |
| `drop_row_idx` | Int64 | 0 % | Row index in raw data where the stop was detected |
| `arrival_time` | Datetime(UTC) | 0 % | Actual arrival timestamp |
| `departure_time` | Datetime(UTC) | 0 % | Actual departure timestamp (last door close) |
| `linie` | Int64 | 0 % | Route number |
| `fahrt_id` | Int64 | 0 % | Trip ID |
| `ort_nr_start` | Int64 | 0 % | Stop number |
| `stop_index` | Int64 | 0 % | Stop sequence position within the trip |
| `stop_status` | String | 0 % | `normal` / `no_door` / `multi_door` |
| `scheduled_arrival_time` | Datetime(UTC) | 0 % | Planned arrival from timetable |
| `delay_calculated_sec` | Float64 | 0 % | Calculated delay: actual − scheduled (seconds) |
| `delay_recorded_sec` | Int64 | 0 % | Self-reported delay via ITCS `lage` field (seconds) |
| `dwell_time` | Float64 | 0 % | departure − arrival (seconds) |
| `travel_time` | Float64 | < 0.1 % | arrival − previous departure (seconds) |
| `besetztgrad` | Int64 | 0 % | Occupancy level |
| `is_peak_hour` | Int8 | 0 % | 1 = peak hour |
| `is_workday` | Int8 | 0 % | 1 = Monday–Friday |
| `has_traffic_signal` | Int8 | 100 % | Placeholder; infrastructure data pending |

---

## 5. Extension Tables

| Table | Additional Columns | Coverage |
|---|---|---|
| `core_stop_events_with_weather.parquet` | `TMK`, `TXK`, `TNK`, `TGK`, `RSK`, `RSKF`, `SDK`, `SHK_TAG`, `FM` | 100 % |
| `core_stop_events_with_vehicle.parquet` | `fahrzueg_type`, `fahrgasttüren`, `länge_m`, `sitzplätze`, `stehplätze`, `kapazitaet` | 86.2 % |
| *(in-memory)* with special events | `has_special_event`, `event_type`, `event_location`, `event_latitude`, `event_longitude` | 4 event dates |

---

## 6. Public API — Callable Functions

These are the functions intended to be imported and used by downstream analysis notebooks:

```python
from pipeline.detector       import detect_stop_events
from pipeline.timetable      import expand_timetable, match_and_compute_delay
from pipeline.feature_builder import add_binary_features
from pipeline.extensions     import (
    load_weather,        join_weather,
    load_vehicle_info,   join_vehicle_info,
    load_special_events, join_special_events,
)
```

### Minimal end-to-end usage

```python
import polars as pl
from pipeline.detector       import detect_stop_events
from pipeline.timetable      import expand_timetable, match_and_compute_delay
from pipeline.feature_builder import add_binary_features
from pipeline.extensions     import load_weather, join_weather, load_vehicle_info, join_vehicle_info

# 1. Load raw data
raw_df = pl.read_csv("data/regular_linie_week.csv",
                     schema_overrides={"linie_text": pl.Utf8}) \
           .with_columns(pl.col("tst_iso")
                           .str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z")) \
           .sort(["fzg_id", "tst_iso"]) \
           .with_columns(pl.int_range(pl.len()).over("fzg_id").alias("row_idx"))

raw_tt = pl.read_csv("data/timetable_trips_2025_07_22.csv",
                     infer_schema_length=10000)

# 2. Core pipeline
stop_events = detect_stop_events(raw_df)
timetable   = expand_timetable(raw_tt)
matched     = match_and_compute_delay(stop_events, raw_df, timetable)
core        = add_binary_features(matched)

# 3. Extensions
weather      = load_weather("data/external/weather/produkt_klima_tag_*.txt")
core_weather = join_weather(core, weather)

vehicle_info    = load_vehicle_info("data/vehicle_data_2025_07_22.csv",
                                    "data/external/vehicle/dvb_fahrzeug_info.csv")
core_vehicle    = join_vehicle_info(core, vehicle_info)
```

---

## 7. Key Statistics (from notebook execution)

| Metric | Value |
|---|---|
| Raw position records | 10,869,683 |
| Vehicles | 538 |
| Data period | 2025-07-28 → 2025-08-03 (7 days) |
| Timetable trips | 97,591 unique |
| Timetable stops (unique) | 2,006 |
| Stop events detected | 1,021,431 |
| — normal | 840,594 (82.3 %) |
| — no_door | 178,212 (17.4 %) |
| — multi_door | 2,625 (0.3 %) |
| Matched to timetable | 994,117 (97.3 %) |
| Mean calculated delay | 164 s |
| Peak-hour events | 259,566 (26.1 %) |
| Workday events | 595,950 (59.9 %) |
| Vehicle type coverage | 86.2 % |
| Weather coverage | 100 % |

---

## 8. Known Limitations & Future Work

| Item | Status |
|---|---|
| `has_traffic_signal` | Placeholder — requires infrastructure link data |
| Public holidays in `is_workday` | Not excluded — needs a calendar integration |
| Vehicle lookup unmatched 27 % | Special vehicles (maintenance, rail-grinders, coaches) outside the lookup range |
| `dwell_time` median = 0 s | ~50 % of events have zero dwell (no_door or instant pass-through); median dwell for `normal` events is higher |
| Special events | Only 4 sport events currently; future work should add concerts, markets, etc. |
