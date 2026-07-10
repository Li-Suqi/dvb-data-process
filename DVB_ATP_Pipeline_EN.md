# DVB ATP Pipeline - English README

Arrival Time Prediction (ATP) data pipeline for the Dresden DVB public transport network.

The project turns raw ITCS/AVL vehicle telemetry and timetable data into a stop-event table for downstream delay analysis and travel-time modelling. The repository also contains data-quality notebooks, bus/tram-specific diagnostics, stop-geometry extraction, traffic-signal experiments, and a dwell-time ablation study.

See the bilingual entry point in `README.md` and the Chinese version in `DVB_ATP_Pipeline_CN.md`.

---

## 1. What This Repository Contains

```text
dvb_atp_pipeline/
├── README.md                         # Bilingual project entry point
├── DVB_ATP_Pipeline_EN.md            # Detailed English documentation
├── DVB_ATP_Pipeline_CN.md            # Detailed Chinese documentation
├── pipeline/                         # Reusable Python pipeline modules
│   ├── detector.py                   # Stop-event detection from distance drops and door states
│   ├── timetable.py                  # Timetable expansion, matching, delay/dwell/travel-time logic
│   ├── extensions.py                 # Weather, vehicle, and special-event joins
│   ├── feature_builder.py            # Derived binary features
│   ├── rescue.py                     # Helper logic for recovering/aligning stop transitions
│   ├── expand_timetable.py           # Earlier standalone timetable-expansion script
│   └── match_arrivals.py             # Earlier standalone matching script
├── notebooks/
│   ├── 01_pipeline.ipynb             # Core stop-event table construction
│   ├── 02_extensions.ipynb           # Weather, vehicle, and event enrichment
│   ├── 03_stop_geo.ipynb             # Stop geometry extraction
│   ├── 04_pipeline_with_ort_signal.ipynb
│   └── explore_vehicle_types.ipynb
├── data_preparation/
│   └── 03_regular_lines.ipynb        # Bus/tram split and regular-line preparation
├── quality-analysis/                 # Full-network data-quality and delay diagnostics
├── bus-quality-analysis/             # Bus-only quality diagnostics
├── tram-quality-analysis/            # Tram-only quality diagnostics
├── ablation/
│   ├── computed-vs-fixed-dwell-time.ipynb
│   └── experiment_a/                 # LightGBM dwell-time ablation scripts and results
└── data/
    ├── regular_linie_week.csv
    ├── regular_lines_0728_0803.parquet
    ├── bus_2025-07-28_2025-08-03.parquet
    ├── tram_2025-07-28_2025-08-03.parquet
    ├── timetable_trips_2025_07_22.csv
    ├── vehicle_data_2025_07_22.csv
    ├── stop_geometry.csv / stop_geometry.parquet
    ├── external/
    │   ├── weather/
    │   ├── vehicle/
    │   └── events/
    ├── processed/
    └── processed_with_signal_info/
```

---

## 2. Data Assets

Current local data files include:

| File | Role | Current size / shape observed locally |
|---|---|---|
| `data/regular_linie_week.csv` | Raw one-week vehicle telemetry used by older notebooks | Large CSV |
| `data/regular_lines_0728_0803.parquet` | Regular-line raw telemetry in Parquet format | 10,708,589 rows, 25 columns |
| `data/bus_2025-07-28_2025-08-03.parquet` | Bus subset | 5,728,505 rows, 25 columns |
| `data/tram_2025-07-28_2025-08-03.parquet` | Tram subset | 4,980,084 rows, 25 columns |
| `data/timetable_trips_2025_07_22.csv` | Raw timetable trips with JSON stop sequences in `segmente` | CSV |
| `data/vehicle_data_2025_07_22.csv` | Raw vehicle metadata | CSV |
| `data/stop_geometry.parquet` | Stop coordinates and names | 3,583 rows, 6 columns |
| `data/external/weather/weather_dresden.parquet` | Cleaned DWD weather observations | Parquet |
| `data/external/vehicle/vehicle_info.parquet` | Matched vehicle lookup output | Parquet |
| `data/external/events/special_events.csv` | Manually curated event dates | CSV |

Generated outputs:

| File | Description | Current shape observed locally |
|---|---|---|
| `data/processed/core_stop_events.parquet` | Main core stop-event table | 976,393 rows, 18 columns |
| `data/processed/core_stop_events_with_weather.parquet` | Core table joined with daily weather | 994,117 rows, 27 columns |
| `data/processed/core_stop_events_with_vehicle.parquet` | Core table joined with vehicle attributes | 994,117 rows, 24 columns |
| `data/processed_with_signal_info/core_stop_events.parquet` | Core table variant with signal information | 950,531 rows, 18 columns |

Note: the current processed files were generated at different times. The core table has 976,393 rows, while the weather and vehicle extension files have 994,117 rows. Re-run `notebooks/02_extensions.ipynb` after regenerating the core table if you need synchronized extension outputs.

---

## 3. Pipeline Flow

```text
Raw vehicle telemetry                     Raw timetable trips
regular_linie_week.csv / parquet          timetable_trips_2025_07_22.csv
        │                                           │
        ▼                                           ▼
detect_stop_events()                       expand_timetable()
pipeline/detector.py                       pipeline/timetable.py
        │                                           │
        └────────────────────┬──────────────────────┘
                             ▼
                  match_and_compute_delay()
                  pipeline/timetable.py
                             │
                             ▼
                   add_binary_features()
                  pipeline/feature_builder.py
                             │
                             ▼
              data/processed/core_stop_events.parquet
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   join_weather()     join_vehicle_info()   join_special_events()
   extensions.py      extensions.py         extensions.py
```

Main stages:

1. Detect stop events from cumulative-distance drops and door-state transitions.
2. Expand timetable JSON stop sequences into one scheduled-arrival row per trip stop.
3. Match actual stop events to scheduled stops by `fahrt_id`, stop number, and service date.
4. Compute calculated delay, recorded delay, dwell time, and travel time.
5. Add binary time/context features.
6. Optionally join weather, vehicle, special-event, stop-geometry, or traffic-signal attributes.

---

## 4. Core Modules

### `pipeline/detector.py`

`detect_stop_events(raw_df)` detects stop events from raw telemetry.

Required input columns:

| Column | Meaning |
|---|---|
| `fzg_id` | Vehicle ID |
| `tst_iso` | Timestamp |
| `distanz` | Cumulative distance along route segment |
| `tuerkriterium` | Door criterion / door-open signal |
| `linie` | Line number |

Algorithm summary:

1. Sort by `(fzg_id, tst_iso)`.
2. Compute per-vehicle `delta = distanz[t] - distanz[t-1]`.
3. Treat `delta < -100 m` as a stop-boundary distance drop.
4. Build an adaptive inspection window of 3 to 10 rows around each drop.
5. Count door-open and door-close transitions inside the window.
6. Classify events as:
   - `normal`: regular stop event.
   - `no_door`: no door activity near the distance drop.
   - `multi_door`: multiple valid door openings at the same stop.

Output columns include `arrival_time`, `departure_time`, `stop_status`, door counts, distance-window diagnostics, and the raw drop location.

### `pipeline/timetable.py`

`expand_timetable(raw_tt)` parses the timetable `segmente` JSON field and creates one scheduled-arrival row per stop in each trip.

`match_and_compute_delay(stop_events, raw_df, timetable)` joins detected stop events to scheduled arrivals and computes modelling fields.

Important matching details:

| Step | Logic |
|---|---|
| Stop metadata | Reads `fahrt_id`, `ort_nr_start`, `lage`, and `besetztgrad` from the original drop row |
| Service date | Uses `(time - 4h).date()` to keep post-midnight trips attached to the same operating day |
| Duplicate stop matches | Keeps the candidate with the smallest absolute arrival/schedule time difference |
| Collision deduplication | If the same vehicle/trip/stop/service-date appears more than once, `normal` is preferred over `multi_door`, then `no_door` |
| Cross-day filter | Removes likely cross-day mismatches with very large calculated delay and strong disagreement with recorded delay |
| Dwell time | `departure_time - arrival_time`; `no_door` rows use `-1.0` as a sentinel |
| Travel time | Current arrival minus previous departure for the same vehicle |

### `pipeline/feature_builder.py`

`add_binary_features(df)` appends:

| Feature | Definition |
|---|---|
| `is_peak_hour` | 1 for 07:00-09:00 or 16:00-19:00 |
| `is_workday` | 1 for Monday-Friday; public holidays are not excluded |
| `has_traffic_signal` | Placeholder in the core pipeline; signal-aware output exists in `data/processed_with_signal_info/` |

### `pipeline/extensions.py`

| Function | Purpose |
|---|---|
| `load_weather(raw_path)` | Load and clean DWD daily weather data, replacing `-999` with null |
| `join_weather(core, weather)` | Join daily weather by `arrival_time.date()` |
| `load_vehicle_info(raw_vehicle_path, lookup_path)` | Match raw vehicle numbers to lookup ranges and capacities |
| `join_vehicle_info(core, vehicle_info)` | Join vehicle attributes by `fzg_id` |
| `load_special_events(events_path)` | Load event dates and locations |
| `join_special_events(core, events)` | Join event information by service date |

---

## 5. Core Table Schema

`data/processed/core_stop_events.parquet` currently has 976,393 rows and 18 columns.

| Column | Meaning |
|---|---|
| `fzg_id` | Vehicle ID |
| `drop_row_idx` | Per-vehicle raw row index where the distance drop was detected |
| `arrival_time` | Actual arrival timestamp |
| `departure_time` | Actual departure timestamp |
| `linie` | Route number |
| `fahrt_id` | Trip ID |
| `ort_nr_start` | Stop number |
| `stop_index` | Stop position in expanded timetable sequence |
| `stop_status` | `normal`, `no_door`, or `multi_door` |
| `scheduled_arrival_time` | Scheduled arrival timestamp |
| `delay_calculated_sec` | Actual arrival minus scheduled arrival, in seconds |
| `delay_recorded_sec` | ITCS self-reported delay from `lage`, in seconds |
| `dwell_time` | Departure minus arrival, in seconds; `-1.0` for `no_door` |
| `travel_time` | Current arrival minus previous departure for the same vehicle |
| `besetztgrad` | Occupancy level |
| `is_peak_hour` | Peak-hour indicator |
| `is_workday` | Weekday indicator |
| `has_traffic_signal` | Placeholder in the core table |

Current local core-table statistics:

| Metric | Value |
|---|---:|
| Rows | 976,393 |
| Vehicles | 360 |
| Trips | 40,146 |
| Unique stops | 1,724 |
| Arrival-time range | 2025-07-27 22:00:06 UTC to 2025-08-03 21:59:59 UTC |
| Mean calculated delay | 157.24 s |
| Peak-hour events | 254,756 |
| Workday events | 584,810 |
| `normal` events | 776,517 |
| `no_door` events | 197,504 |
| `multi_door` events | 2,372 |

---

## 6. How To Run

No environment file is currently committed. Based on imports in the repository, use Python 3.11+ with at least:

```bash
pip install polars numpy pandas matplotlib scikit-learn lightgbm jupyter
```

Optional packages may be needed by mapping or notebook-only cells, depending on which analysis notebook is executed.

Run the core pipeline through notebooks:

1. `notebooks/01_pipeline.ipynb` - build `data/processed/core_stop_events.parquet`.
2. `notebooks/02_extensions.ipynb` - build weather and vehicle extension outputs.
3. `notebooks/03_stop_geo.ipynb` - build stop geometry.
4. `notebooks/04_pipeline_with_ort_signal.ipynb` - build the signal-aware variant.

Run the reusable API from Python:

```python
import polars as pl

from pipeline.detector import detect_stop_events
from pipeline.timetable import expand_timetable, match_and_compute_delay
from pipeline.feature_builder import add_binary_features
from pipeline.extensions import (
    load_weather,
    join_weather,
    load_vehicle_info,
    join_vehicle_info,
)

raw_df = (
    pl.read_parquet("data/regular_lines_0728_0803.parquet")
    .with_columns(
        pl.col("tst_iso").str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z")
    )
    .sort(["fzg_id", "tst_iso"])
    .with_columns(pl.int_range(pl.len()).over("fzg_id").alias("row_idx"))
)

raw_tt = pl.read_csv("data/timetable_trips_2025_07_22.csv", infer_schema_length=10000)

stop_events = detect_stop_events(raw_df)
timetable = expand_timetable(raw_tt)
matched = match_and_compute_delay(stop_events, raw_df, timetable)
core = add_binary_features(matched)

weather = load_weather("data/external/weather/produkt_klima_tag_20241129_20260601_01048.txt")
core_weather = join_weather(core, weather)

vehicle_info = load_vehicle_info(
    "data/vehicle_data_2025_07_22.csv",
    "data/external/vehicle/dvb_fahrzeug_info.csv",
)
core_vehicle = join_vehicle_info(core, vehicle_info)
```

---

## 7. Quality Analysis

The quality-analysis notebooks are split into full-network, bus-only, and tram-only folders.

Common analyses:

| Notebook group | Purpose |
|---|---|
| `01_redundant.ipynb` | Hard and soft duplicate detection |
| `02_missing.ipynb` | Missing fields and trip-level timestamp gaps |
| `03_noisy.ipynb` / `03_noisy_eda.ipynb` | GPS zeros, distance spikes, small drops, extreme delay values, unreliable statuses |
| `04_window_size.ipynb` | Stop-detection window sensitivity |
| `05_speed_analysis.ipynb` / `06_gps_speed.ipynb` | Distance-based and GPS-based speed sanity checks |
| `07_drop_timing.ipynb` | Timing relationship between distance drops and door events |
| `08_dwell_time_analysis.ipynb` | Dwell-time diagnostics |

Additional full-network notebooks:

| Notebook | Purpose |
|---|---|
| `09_ort_nr_transition.ipynb` | Stop-number transition diagnostics |
| `10_delay_map.ipynb` | Stop-level delay map |
| `11_timetable_collision_check.ipynb` | Timetable collision checks |
| `12_delay_comparison.ipynb` | Delay comparison for signal-aware outputs |
| `13_trip_inspection.ipynb` | Trip-level inspection using stop geometry |

Reports and plots are stored in each folder's `quality_report/` directory.

---

## 8. Ablation Study

`ablation/experiment_a/` evaluates whether computed dwell time improves next-stop travel-time prediction compared with a fixed per-line dwell-time baseline.

Scripts:

| Script | Role |
|---|---|
| `prepare_data.py` | Creates train/validation/test feature matrices for computed vs fixed dwell time |
| `train.py` | Trains LightGBM models with early stopping |
| `evaluate.py` | Computes MAE/RMSE and saves plots |

Current recorded results in `ablation/experiment_a/results/metrics.json`:

| Variant | MAE | RMSE |
|---|---:|---:|
| Computed dwell time | 23.2744 s | 36.9413 s |
| Fixed dwell time | 23.4588 s | 37.3426 s |

Relative RMSE improvement: 1.0747%.

The broader `metrics_ablation.json` also contains `Full`, `No-location`, and `Ops-only` configurations. In the `Full` configuration, computed dwell time records 16.7291 s MAE and 32.3327 s RMSE.

---

## 9. Known Limitations

| Area | Current status |
|---|---|
| Processed output synchronization | Core and extension parquet files currently have different row counts; re-run extensions after regenerating the core table |
| Environment reproducibility | No committed `requirements.txt` or `pyproject.toml` yet |
| Public holidays | `is_workday` only distinguishes weekdays and weekends |
| Traffic signals | Core table keeps `has_traffic_signal` as a placeholder; signal-aware outputs live separately |
| Special events | Event file is manually curated and currently narrow in scope |
| Vehicle metadata | Lookup is range-based and does not cover every vehicle record |
| Notebook paths | Some exploratory notebooks still reference older CSV inputs or bus/tram files directly |

---

## 10. Suggested Next Steps

1. Add a reproducible environment file, preferably `pyproject.toml` or `requirements.txt`.
2. Re-run `notebooks/01_pipeline.ipynb` and `notebooks/02_extensions.ipynb` in sequence so core and extension tables are synchronized.
3. Promote signal-aware feature creation from notebook-only logic into a reusable module.
4. Add lightweight smoke tests for `detect_stop_events`, `expand_timetable`, and `match_and_compute_delay`.
5. Document data provenance and license restrictions for raw DVB, DWD, event, and vehicle lookup data.
