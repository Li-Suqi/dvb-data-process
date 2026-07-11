"""Generate paper-figure previews from the retained signal-aware outputs."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from matplotlib.patches import FancyArrowPatch, Patch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = Path(__file__).resolve().parent / "previews"
CORE_PATH = DATA / "processed_with_signal_info" / "core_stop_events.parquet"
RAW_PATH = DATA / "regular_lines_0728_0803.parquet"
QUALITY = ROOT / "quality-analysis" / "quality_report"
BUS_QUALITY = ROOT / "bus-quality-analysis" / "quality_report"
TRAM_QUALITY = ROOT / "tram-quality-analysis" / "quality_report"
ABLATION = ROOT / "ablation" / "experiment_a" / "results"

BLUE = "#2878B5"
ORANGE = "#E8912D"
TEAL = "#2A9D8F"
RED = "#C44536"
GREY = "#7A7A7A"
LIGHT_GREY = "#D8D8D8"


def style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "legend.fontsize": 9,
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def save(fig: plt.Figure, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight")
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)


def short_number(value: int) -> str:
    return f"{value / 1_000_000:.2f}M" if value >= 1_000_000 else f"{value / 1_000:.0f}k"


def plot_pipeline_overview() -> None:
    fig, ax = plt.subplots(figsize=(13, 4.3))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 5.1)
    ax.axis("off")

    stages = [
        (0.4, 3.25, 2.1, 1.05, "Vehicle position\nstream", "Distance, door state,\nstop identifiers"),
        (0.4, 0.75, 2.1, 1.05, "Timetable", "Scheduled trip-stop\narrivals"),
        (3.35, 3.25, 2.25, 1.05, "Signal-aware\nstop detection", "Distance drops +\ndoor transitions"),
        (6.3, 3.25, 2.25, 1.05, "Quality gates", "Rescue layer +\nstop-transition filter"),
        (9.25, 3.25, 2.25, 1.05, "Timetable\nmatching", "Service date, collisions,\ncross-day filtering"),
        (12.15, 3.25, 1.45, 1.05, "Core\ntable", "950,531\nevents"),
    ]
    for x, y, w, h, title, detail in stages:
        face = "#E8F1F8" if title not in {"Quality gates", "Core\ntable"} else "#E7F4F1"
        ax.add_patch(Rectangle((x, y), w, h, facecolor=face, edgecolor=BLUE, linewidth=1.2))
        ax.text(x + w / 2, y + h * 0.67, title, ha="center", va="center", weight="bold")
        ax.text(x + w / 2, y + h * 0.28, detail, ha="center", va="center", fontsize=8.5)

    arrows = [
        ((2.5, 3.78), (3.35, 3.78)),
        ((5.6, 3.78), (6.3, 3.78)),
        ((8.55, 3.78), (9.25, 3.78)),
        ((11.5, 3.78), (12.15, 3.78)),
        ((2.5, 1.28), (9.25, 3.45)),
    ]
    for start, end in arrows:
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=13, linewidth=1.2, color=GREY))

    ax.text(7.42, 2.35, "999,722 candidate events after stop-transition filtering", ha="center", va="center", color=BLUE, weight="bold")
    ax.text(12.87, 2.48, "95.08%\nmatched", ha="center", va="center", color=TEAL, weight="bold")
    ax.text(7, 0.15, "Derived operational features: delay, dwell time, travel time, occupancy, and temporal indicators", ha="center", va="bottom", fontsize=9)
    save(fig, "fig_4_1_pipeline_overview")


def plot_signal_trace(core: pl.DataFrame) -> None:
    event = (
        core.filter((pl.col("stop_status") == "normal") & (pl.col("dwell_time") >= 12) & (pl.col("dwell_time") <= 45))
        .sort(["fzg_id", "drop_row_idx"])
        .row(0, named=True)
    )
    vehicle = event["fzg_id"]
    raw = (
        pl.scan_parquet(RAW_PATH)
        .filter(pl.col("fzg_id") == vehicle)
        .select(["tst_iso", "distanz", "tuerkriterium", "ort_nr_start"])
        .collect()
        .with_columns(pl.col("tst_iso").str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z").alias("time"))
        .sort("time")
        .with_row_index("row_idx")
    )
    center = event["drop_row_idx"]
    window = raw.filter((pl.col("row_idx") >= max(0, center - 18)) & (pl.col("row_idx") <= center + 18))
    x = window["row_idx"].to_numpy()
    times = window["time"].dt.epoch("ms").to_numpy()
    arrival_ms = int(event["arrival_time"].timestamp() * 1000)
    departure_ms = int(event["departure_time"].timestamp() * 1000)
    arrival_x = x[np.argmin(np.abs(times - arrival_ms))]
    departure_x = x[np.argmin(np.abs(times - departure_ms))]

    fig, axes = plt.subplots(2, 1, figsize=(11.5, 5.5), sharex=True, height_ratios=[3, 1])
    axes[0].plot(x, window["distanz"].to_numpy(), color=BLUE, marker="o", markersize=3, linewidth=1.8, label="Cumulative distance")
    axes[0].axvline(center, color=RED, linestyle="--", linewidth=1.5, label="Distance drop")
    axes[0].axvline(arrival_x, color=TEAL, linestyle="-", linewidth=1.6, label="Inferred arrival")
    axes[0].axvline(departure_x, color=ORANGE, linestyle="-", linewidth=1.6, label="Inferred departure")
    axes[0].set_ylabel("Cumulative distance (m)")
    axes[0].set_title("Dual-signal stop-event inference for one vehicle passage")
    axes[0].legend(ncol=4, loc="upper left", frameon=False)
    axes[0].grid(axis="y", alpha=0.25)

    door = window["tuerkriterium"].cast(pl.Int8).to_numpy()
    axes[1].step(x, door, where="mid", color=TEAL, linewidth=2)
    axes[1].fill_between(x, 0, door, step="mid", color=TEAL, alpha=0.18)
    for mark, color in [(center, RED), (arrival_x, TEAL), (departure_x, ORANGE)]:
        axes[1].axvline(mark, color=color, linestyle="--" if mark == center else "-", linewidth=1.5)
    axes[1].set_yticks([0, 1], ["Closed", "Open"])
    axes[1].set_ylabel("Door state")
    axes[1].set_xlabel("Observation index within vehicle stream")
    axes[1].grid(axis="y", alpha=0.25)
    fig.text(0.5, 0.005, f"Illustrative matched event: dwell time = {event['dwell_time']:.1f} s", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    save(fig, "fig_4_2_dual_signal_trace")


def sampling_intervals() -> np.ndarray:
    frames = (
        pl.scan_parquet(RAW_PATH)
        .select(["fahrt_id", "tst_iso"])
        .with_columns(pl.col("tst_iso").str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.f%z").alias("time"))
        .sort(["fahrt_id", "time"])
        .with_columns(pl.col("time").diff().over("fahrt_id").dt.total_seconds().alias("interval_s"))
        .filter((pl.col("interval_s") > 0) & (pl.col("interval_s") <= 7200))
        .select("interval_s")
        .collect()
    )
    return frames["interval_s"].to_numpy()


def plot_sampling_gap(intervals: np.ndarray) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.1))
    median = np.median(intervals)
    axes[0].hist(intervals[intervals <= 120], bins=60, color=BLUE, edgecolor="white")
    axes[0].axvline(median, color=TEAL, linewidth=1.8, label=f"Median = {median:.1f} s")
    axes[0].axvline(30, color=RED, linestyle="--", linewidth=1.4, label="Gap threshold = 30 s")
    axes[0].set(xlim=(0, 120), xlabel="Sampling interval (s)", ylabel="Observations", title="Typical sampling intervals")
    axes[0].legend(frameon=False)
    axes[0].grid(axis="y", alpha=0.25)
    axes[1].hist(intervals, bins=np.geomspace(1, 7200, 80), color=ORANGE, edgecolor="white")
    axes[1].set_xscale("log")
    axes[1].set_yscale("log")
    axes[1].axvline(30, color=RED, linestyle="--", linewidth=1.4)
    axes[1].set(xlabel="Sampling interval (s, logarithmic scale)", ylabel="Observations (log scale)", title="Long-tail sampling gaps")
    axes[1].grid(alpha=0.25)
    fig.suptitle("Sampling continuity in the vehicle position stream", y=1.02, weight="bold")
    fig.tight_layout()
    save(fig, "fig_5_1_sampling_and_gaps")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def plot_noise_and_mode() -> None:
    whole = load_json(QUALITY / "A3_noisy_summary.json")
    bus = load_json(BUS_QUALITY / "A3_noisy_summary.json")
    tram = load_json(TRAM_QUALITY / "A3_noisy_summary.json")
    labels = ["GPS zero", "Distance spikes", "Small distance drops", "Extreme recorded delay", "Default-zero recorded delay", "Unreliable status"]
    keys = ["n1_gps_zero", "n2_distanz_spike", "n3_distanz_small_drop", "n4_lage_extreme", "n5_lage_default_zero", "n6_bad_status"]
    rates = [whole[key]["rate"] * 100 for key in keys]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4), gridspec_kw={"width_ratios": [1.5, 1]})
    ypos = np.arange(len(labels))
    axes[0].scatter(rates, ypos, color=RED, s=55, zorder=3)
    axes[0].hlines(ypos, 0, rates, color=LIGHT_GREY, linewidth=2)
    axes[0].set_yticks(ypos, labels)
    axes[0].invert_yaxis()
    axes[0].set_xlabel("Rate in raw observations (%)")
    axes[0].set_title("Noise indicators (categories overlap)")
    axes[0].grid(axis="x", alpha=0.25)
    for y, rate in zip(ypos, rates):
        axes[0].text(rate + 0.04, y, f"{rate:.3f}%", va="center", fontsize=9)
    modes = ["Bus", "Tram"]
    mode_rates = [bus["noisy_union_rate"] * 100, tram["noisy_union_rate"] * 100]
    bars = axes[1].bar(modes, mode_rates, color=[ORANGE, TEAL], width=0.55)
    axes[1].set_ylabel("Noisy-record rate (%)")
    axes[1].set_ylim(0, max(mode_rates) * 1.28)
    axes[1].set_title("Mode-specific quality")
    axes[1].grid(axis="y", alpha=0.25)
    for bar, rate in zip(bars, mode_rates):
        axes[1].text(bar.get_x() + bar.get_width() / 2, rate + 0.1, f"{rate:.3f}%", ha="center", weight="bold")
    fig.suptitle("Noise profile and bus-tram comparison", y=1.02, weight="bold")
    fig.tight_layout()
    save(fig, "fig_5_2_noise_and_mode")


def plot_stop_diagnostics(core: pl.DataFrame) -> None:
    status_order = ["normal", "no_door", "multi_door"]
    status_counts = core.group_by("stop_status").len().to_dict(as_series=False)
    status_map = dict(zip(status_counts["stop_status"], status_counts["len"]))
    status_values = [status_map[name] for name in status_order]
    dwell_values = [
        core.filter(pl.col("dwell_time") > 0).height,
        core.filter(pl.col("dwell_time") == 0).height,
        core.filter(pl.col("dwell_time") < 0).height,
    ]
    fig, axes = plt.subplots(2, 1, figsize=(10.5, 3.8), sharex=True)
    groups = [
        ("Stop-event status", ["Normal", "No door signal", "Multiple door cycles"], status_values, [BLUE, GREY, ORANGE]),
        ("Dwell-time availability", ["Positive dwell time", "Zero dwell time", "No-door sentinel"], dwell_values, [TEAL, ORANGE, GREY]),
    ]
    total = core.height
    for ax, (title, labels, values, colors) in zip(axes, groups):
        left = 0.0
        for label, value, color in zip(labels, values, colors):
            pct = value / total * 100
            ax.barh([0], [pct], left=left, color=color, height=0.5)
            if pct >= 5:
                ax.text(
                    left + pct / 2,
                    0,
                    f"{label}\n{pct:.1f}%",
                    ha="center",
                    va="center",
                    color="white",
                    weight="bold",
                    fontsize=9,
                )
            left += pct
        ax.set_yticks([])
        ax.set_xlim(0, 100)
        ax.set_title(title, loc="left")
    axes[1].set_xlabel("Share of final matched stop events (%)")
    fig.suptitle("Stop-event detection diagnostics", y=1.04, weight="bold")
    fig.tight_layout()
    save(fig, "fig_5_3_stop_event_diagnostics")


def plot_delay_validation(core: pl.DataFrame) -> None:
    delay = core.select(["delay_recorded_sec", "delay_calculated_sec"]).drop_nulls()
    x_all = delay["delay_recorded_sec"].to_numpy()
    y_all = delay["delay_calculated_sec"].to_numpy()
    corr = np.corrcoef(x_all, y_all)[0, 1]
    display = (x_all >= -180) & (x_all <= 600) & (y_all >= -180) & (y_all <= 600)
    x, y = x_all[display], y_all[display]
    fig, ax = plt.subplots(figsize=(7.5, 6.2))
    hb = ax.hexbin(x, y, gridsize=65, mincnt=1, bins="log", cmap="YlOrRd", linewidths=0)
    ax.plot([-180, 600], [-180, 600], color=BLUE, linestyle="--", linewidth=1.5, label="Equal-delay reference")
    slope, intercept = np.polyfit(x, y, 1)
    grid = np.array([-180, 600])
    ax.plot(grid, slope * grid + intercept, color="black", linewidth=1.5, label="Linear trend (displayed range)")
    ax.set(xlim=(-180, 600), ylim=(-180, 600), xlabel="Vehicle-reported delay (s)", ylabel="Calculated delay (s)", title="Delay validation against the vehicle-reported signal")
    ax.legend(loc="upper left", frameon=True)
    ax.grid(alpha=0.2)
    cbar = fig.colorbar(hb, ax=ax)
    cbar.set_label("Observation density (log scale)")
    ax.text(0.98, 0.03, f"All valid matched events: Pearson r = {corr:.3f}\nDisplay window: -180 to 600 s", transform=ax.transAxes, ha="right", va="bottom", fontsize=9, bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.85})
    fig.tight_layout()
    save(fig, "fig_5_4_delay_validation")


def plot_experiment_timeline() -> None:
    fig, ax = plt.subplots(figsize=(11.5, 2.7))
    periods = [
        (0, 4, BLUE, "Training\n28-31 July", "572,325 rows"),
        (4, 2, TEAL, "Validation\n1-2 August", "266,479 rows"),
        (6, 1, ORANGE, "Test\n3 August", "113,519 rows"),
    ]
    for left, width, color, label, count in periods:
        ax.barh(0, width, left=left, color=color, height=0.5)
        ax.text(left + width / 2, 0.05, label, ha="center", va="center", color="white", weight="bold")
        ax.text(left + width / 2, -0.47, count, ha="center", va="center", fontsize=9)
    ax.text(3.5, 0.83, "Identical LightGBM setup and temporal split for both dwell-time variants", ha="center", weight="bold")
    ax.text(3.5, -0.88, "Computed dwell time versus route-level fixed mean dwell time", ha="center")
    ax.set_xlim(0, 7)
    ax.set_ylim(-1.1, 1.15)
    ax.set_xticks(np.arange(7) + 0.5, ["28 Jul", "29 Jul", "30 Jul", "31 Jul", "1 Aug", "2 Aug", "3 Aug"])
    ax.set_yticks([])
    ax.spines[["left", "right", "top"]].set_visible(False)
    ax.set_title("Chronological experimental design", loc="left")
    fig.tight_layout()
    save(fig, "fig_6_1_temporal_split")


def plot_prediction_results() -> None:
    metrics = load_json(ABLATION / "metrics_ablation.json")["configs"]
    configs = ["Full", "No-location", "Ops-only"]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
    x = np.arange(len(configs))
    width = 0.34
    for ax, metric, title in zip(axes, ["mae", "rmse"], ["Mean absolute error", "Root mean squared error"]):
        computed = [metrics[c]["variant1"][metric] for c in configs]
        fixed = [metrics[c]["variant2"][metric] for c in configs]
        left = ax.bar(x - width / 2, computed, width, label="Computed dwell time", color=BLUE)
        right = ax.bar(x + width / 2, fixed, width, label="Fixed dwell-time baseline", color=ORANGE)
        for bars in [left, right]:
            for bar in bars:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.18, f"{bar.get_height():.2f}", ha="center", fontsize=9)
        ax.set_xticks(x, configs)
        ax.set_ylabel("Error (s)")
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.25)
        ax.legend(frameon=False)
        ax.set_ylim(0, max(fixed) * 1.18)
    fig.suptitle("Test-set prediction error across feature configurations", y=1.02, weight="bold")
    fig.tight_layout()
    save(fig, "fig_7_1_prediction_error")


def plot_feature_importance_preview() -> None:
    source = plt.imread(ABLATION / "feature_importance_all_configs.png")
    fig, ax = plt.subplots(figsize=(13, 4.6))
    ax.imshow(source)
    ax.axis("off")
    ax.set_title("Feature-importance preview from the ablation analysis", loc="left", pad=10)
    fig.tight_layout()
    save(fig, "fig_7_2_feature_importance_preview")


def plot_rescue_exploration() -> None:
    total_detected = 1_002_942
    case_1 = 38_791
    case_2 = 163_563
    candidates = case_1 + case_2
    patched = 171_464
    unresolved = candidates - patched
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.2), gridspec_kw={"width_ratios": [1.15, 1]})
    values = [case_1, case_2]
    labels = ["Door opening found,\nclosing missed", "No opening found\nin local window"]
    bars = axes[0].bar(labels, values, color=[ORANGE, GREY], width=0.6)
    axes[0].set_ylabel("Candidate events")
    axes[0].set_title("Why events enter the rescue layer")
    axes[0].grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, values):
        axes[0].text(bar.get_x() + bar.get_width() / 2, value + 3500, f"{value:,}", ha="center", weight="bold")
    stages = ["Initial detector", "Need rescue", "Successfully\npatched", "Unresolved"]
    counts = [total_detected, candidates, patched, unresolved]
    colors = [BLUE, ORANGE, TEAL, RED]
    bars = axes[1].bar(stages, counts, color=colors, width=0.65)
    axes[1].set_ylabel("Events")
    axes[1].set_title("Rescue-layer outcome")
    axes[1].grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, counts):
        axes[1].text(bar.get_x() + bar.get_width() / 2, value + 20000, short_number(value), ha="center", weight="bold")
    axes[1].text(0.5, 0.93, f"{patched / candidates * 100:.1f}% of rescue candidates patched", transform=axes[1].transAxes, ha="center", color=TEAL, weight="bold")
    fig.suptitle("Exploratory diagnostic: effect of the inter-drop rescue layer", y=1.03, weight="bold")
    fig.tight_layout()
    save(fig, "appendix_rescue_layer_effect")


def main() -> None:
    style()
    core = pl.read_parquet(CORE_PATH)
    plot_pipeline_overview()
    plot_signal_trace(core)
    plot_sampling_gap(sampling_intervals())
    plot_noise_and_mode()
    plot_stop_diagnostics(core)
    plot_delay_validation(core)
    plot_experiment_timeline()
    plot_prediction_results()
    plot_feature_importance_preview()
    plot_rescue_exploration()
    print(f"Wrote previews to {OUT}")


if __name__ == "__main__":
    main()
