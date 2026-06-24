"""
Evaluate both variants on the test set. Outputs metrics.json and plots.
"""

import json
import pickle
from pathlib import Path

import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from sklearn.metrics import mean_absolute_error, mean_squared_error

OUT = Path("results")

info     = json.loads((OUT / "split_info.json").read_text())
FEATURES = info["features"]

# ── load models + test data ───────────────────────────────────────────────────
results = {}

for variant, label in [("variant1", "computed dwell"), ("variant2", "fixed dwell")]:
    with open(OUT / f"{variant}_model.pkl", "rb") as f:
        model = pickle.load(f)

    test = pl.read_parquet(OUT / f"{variant}_test.parquet")
    X    = test[FEATURES].to_pandas()
    y    = test["target"].to_numpy()

    pred = model.predict(X, num_iteration=model.best_iteration)
    pred = np.clip(pred, 10, 600)

    mae  = mean_absolute_error(y, pred)
    rmse = mean_squared_error(y, pred) ** 0.5

    results[variant] = {
        "label":     label,
        "mae":       round(mae, 4),
        "rmse":      round(rmse, 4),
        "model":     model,
        "test_df":   test,
        "pred":      pred,
        "y":         y,
    }
    print(f"{label:20s}  MAE={mae:.3f}s  RMSE={rmse:.3f}s")

# ── relative improvement ──────────────────────────────────────────────────────
rmse1 = results["variant1"]["rmse"]
rmse2 = results["variant2"]["rmse"]
rel   = (rmse2 - rmse1) / rmse2 * 100
print(f"\nRelative RMSE improvement (computed vs fixed): {rel:+.2f}%")

# ── save metrics ──────────────────────────────────────────────────────────────
metrics = {
    "test_date":      info["test_date"],
    "variant1":       {"mae": results["variant1"]["mae"], "rmse": results["variant1"]["rmse"]},
    "variant2":       {"mae": results["variant2"]["mae"], "rmse": results["variant2"]["rmse"]},
    "rmse_improvement_pct": round(rel, 4),
}
(OUT / "metrics.json").write_text(json.dumps(metrics, indent=2))
print(f"Saved → {OUT}/metrics.json")

# ── feature importance (variant 1) ────────────────────────────────────────────
model1 = results["variant1"]["model"]
imp    = model1.feature_importance(importance_type="gain")
names  = model1.feature_name()

order  = np.argsort(imp)
fig, ax = plt.subplots(figsize=(8, 6))
ax.barh([names[i] for i in order], [imp[i] for i in order], color="steelblue")
ax.set_xlabel("Feature importance (gain)")
ax.set_title("Variant 1 (computed dwell_time) — feature importance")
ax.grid(axis="x", alpha=0.3)

# highlight dwell_time bar
dwell_pos = list(names).index("dwell_time")
bar_pos   = list(order).index(dwell_pos)
ax.get_children()[bar_pos].set_color("tomato")

plt.tight_layout()
plt.savefig(OUT / "feature_importance.png", dpi=150)
plt.show()
print(f"Saved → {OUT}/feature_importance.png")

# ── grouped evaluation ────────────────────────────────────────────────────────
test1 = results["variant1"]["test_df"].with_columns(
    pl.Series("pred", results["variant1"]["pred"])
)

print("\n=== MAE by is_peak_hour ===")
for peak in [0, 1]:
    sub = test1.filter(pl.col("is_peak_hour") == peak)
    mae = mean_absolute_error(sub["target"].to_numpy(), sub["pred"].to_numpy())
    print(f"  is_peak_hour={peak}  n={len(sub):,}  MAE={mae:.3f}s")

print("\n=== MAE by stop_status ===")
for ss, name in [(0, "normal"), (1, "multi_door"), (2, "no_door")]:
    sub = test1.filter(pl.col("stop_status") == ss)
    if len(sub) == 0:
        continue
    mae = mean_absolute_error(sub["target"].to_numpy(), sub["pred"].to_numpy())
    print(f"  {name:12s}  n={len(sub):,}  MAE={mae:.3f}s")

# ── MAE comparison plot ───────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(6, 4))
labels  = ["computed dwell\n(variant 1)", "fixed dwell\n(variant 2)"]
maes    = [results["variant1"]["mae"], results["variant2"]["mae"]]
rmses   = [results["variant1"]["rmse"], results["variant2"]["rmse"]]
x       = np.arange(2)
w       = 0.35

ax.bar(x - w/2, maes,  w, label="MAE",  color="steelblue")
ax.bar(x + w/2, rmses, w, label="RMSE", color="salmon")
ax.set_xticks(x)
ax.set_xticklabels(labels)
ax.set_ylabel("seconds")
ax.set_title("Computed vs fixed dwell_time — test set error")
ax.legend()
ax.grid(axis="y", alpha=0.3)
plt.tight_layout()
plt.savefig(OUT / "metric_comparison.png", dpi=150)
plt.show()
print(f"Saved → {OUT}/metric_comparison.png")
