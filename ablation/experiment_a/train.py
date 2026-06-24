"""
Train LightGBM for both variants. Saves models to results/.
"""

import json
import pickle
from pathlib import Path

import lightgbm as lgb
import polars as pl

OUT = Path("results")

PARAMS = {
    "objective":        "regression",
    "metric":           ["mae", "rmse"],
    "learning_rate":    0.05,
    "num_leaves":       63,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq":     5,
    "verbose":          -1,
}
NUM_ROUNDS           = 500
EARLY_STOPPING       = 50

info     = json.loads((OUT / "split_info.json").read_text())
FEATURES = info["features"]
CAT_FEATURES = ["linie", "ort_nr_start", "stop_status"]


def load(name: str) -> tuple[lgb.Dataset, lgb.Dataset]:
    train = pl.read_parquet(OUT / f"{name}_train.parquet")
    valid = pl.read_parquet(OUT / f"{name}_valid.parquet")

    dtrain = lgb.Dataset(
        train[FEATURES].to_pandas(),
        label=train["target"].to_numpy(),
        categorical_feature=CAT_FEATURES,
        free_raw_data=False,
    )
    dvalid = lgb.Dataset(
        valid[FEATURES].to_pandas(),
        label=valid["target"].to_numpy(),
        categorical_feature=CAT_FEATURES,
        reference=dtrain,
        free_raw_data=False,
    )
    return dtrain, dvalid


for variant in ("variant1", "variant2"):
    print(f"\n{'='*50}")
    print(f"Training {variant} ({'computed' if variant == 'variant1' else 'fixed'} dwell_time)")
    print(f"{'='*50}")

    dtrain, dvalid = load(variant)

    callbacks = [
        lgb.early_stopping(EARLY_STOPPING, verbose=True),
        lgb.log_evaluation(50),
    ]

    model = lgb.train(
        PARAMS,
        dtrain,
        num_boost_round=NUM_ROUNDS,
        valid_sets=[dvalid],
        callbacks=callbacks,
    )

    model_path = OUT / f"{variant}_model.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    print(f"Best iteration: {model.best_iteration}")
    print(f"Saved → {model_path}")

print("\nAll models trained.")
