"""Frozen chronological model comparison and portable, traceable run artifacts."""

import hashlib
import importlib.metadata
import json
import platform
import shutil
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from .features import FEATURE_SETS, WEEKLY, build_features, seasonal_baseline, split_labels
from .storage import NY, sha256, write_json


def metrics(actual, predicted) -> dict:
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if actual.size == 0 or actual.shape != predicted.shape:
        raise ValueError("Metrics need nonempty, matching observations")
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValueError("Metrics require finite values on identical eligible rows")
    numerator = float(np.abs(actual - predicted).sum())
    denominator = float(actual.sum())
    return {
        "rows": len(actual),
        "mae": numerator / len(actual),
        "wape": numerator / denominator if denominator > 0 else None,
        "absolute_error_sum": numerator,
        "recorded_pickups_sum": denominator,
        "bias": float((predicted - actual).mean()),
    }


def code_fingerprint() -> str:
    digest = hashlib.sha256()
    package = Path(__file__).parent
    for path in sorted(package.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(package).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def evaluate(config: dict, root: Path) -> Path:
    processed = root / "data/processed"
    prepared = json.loads((processed / "prepared.json").read_text())
    for name, expected in prepared["artifacts"].items():
        if sha256(processed / name) != expected:
            raise ValueError(f"Processed artifact changed or preparation was interrupted: {name}")
    hourly = pd.read_parquet(processed / "hourly.parquet")
    quality = json.loads((processed / "quality.json").read_text())
    if [m["month"] for m in quality["months"]] != config["months"]:
        raise ValueError("Processed months do not match config; run prepare again")
    frame = build_features(hourly, config["observation_delay_hours"])
    frame["split"] = split_labels(frame.hour_start, config)
    eligible = frame[WEEKLY + ["pickup_count"]].notna().all(axis=1)
    exclusions = {
        name: int((frame.split.eq(name) & ~eligible).sum())
        for name in ("train", "validation", "test")
    }
    usable = frame.loc[eligible & ~frame.split.eq("excluded")].copy()
    train = usable.loc[usable.split.eq("train")]
    validation = usable.loc[usable.split.eq("validation")]
    future = usable.loc[usable.split.isin(["validation", "test"])].copy()
    if train.empty or validation.empty or not future.split.eq("test").any():
        raise ValueError("Each split needs eligible rows after history warm-up")
    # Delayed feeds also limit which training labels exist when the model is fitted.
    fit_cutoff = pd.Timestamp(config["validation_start"]) - pd.Timedelta(
        hours=config["observation_delay_hours"]
    )
    train = train.loc[train.hour_start + pd.Timedelta(hours=1) <= fit_cutoff]
    if train.empty:
        raise ValueError("No training labels are available at the fitting cutoff")
    future["baseline_week"] = future.previous_week
    future["baseline_seasonal"] = seasonal_baseline(train, future)
    models = {}
    for name, columns in FEATURE_SETS.items():
        print(f"Training {name} features on {len(train):,} zone-hours ...", flush=True)
        model = lgb.LGBMRegressor(
            objective="poisson",
            n_estimators=config["n_estimators"],
            learning_rate=config["learning_rate"],
            num_leaves=config["num_leaves"],
            random_state=config["seed"],
            n_jobs=config["n_jobs"],
            verbosity=-1,
            deterministic=True,
            force_col_wise=True,
        )
        model.fit(train[columns], train.pickup_count, categorical_feature=["zone_id"])
        future[f"model_{name}"] = model.predict(future[columns])
        models[name] = model
    comparisons = ["baseline_week", "baseline_seasonal"] + [f"model_{n}" for n in FEATURE_SETS]
    overall = {
        split: {name: metrics(rows.pickup_count, rows[name]) for name in comparisons}
        for split, rows in future.groupby("split")
    }
    selected = min(FEATURE_SETS, key=lambda n: overall["validation"][f"model_{n}"]["mae"])
    baseline = min(
        ("baseline_week", "baseline_seasonal"), key=lambda n: overall["validation"][n]["mae"]
    )
    future["predicted_pickups"] = future[f"model_{selected}"]
    for results in overall.values():
        reference_mae = results[baseline]["mae"]
        for stats in results.values():
            stats["relative_mae_improvement"] = (
                1 - stats["mae"] / reference_mae if reference_mae else None
            )
    zones = pd.read_csv(processed / "zones.csv")
    future["borough"] = future.zone_id.map(zones.set_index("LocationID").Borough)
    rank = train.groupby("zone_id").pickup_count.mean().rank(pct=True, method="average")
    volume_groups = pd.Series(
        np.where(rank <= 1 / 3, "low", np.where(rank <= 2 / 3, "medium", "high")), index=rank.index
    )
    future["volume_group"] = future.zone_id.map(volume_groups)
    local = future.hour_start.dt.tz_convert(NY)
    future["week"] = local.dt.tz_localize(None).dt.to_period("W").astype(str)
    future["rush_hour"] = ((future.weekday < 5) & future.hour.isin([7, 8, 9, 16, 17, 18])).astype(
        int
    )
    slices = []
    for dimension in ("borough", "volume_group", "hour", "weekday", "holiday", "rush_hour", "week"):
        for (split, value), rows in future.groupby(["split", dimension]):
            for name in ("predicted_pickups", "baseline_week", "baseline_seasonal"):
                slices.append(
                    {
                        "split": split,
                        "dimension": dimension,
                        "value": str(value),
                        "predictor": name,
                        **metrics(rows.pickup_count, rows[name]),
                    }
                )
    fingerprint = code_fingerprint()
    run_hash = hashlib.sha256(
        json.dumps(
            {"config": config, "code": fingerprint, "data": quality["data_version"]}, sort_keys=True
        ).encode()
    ).hexdigest()[:12]
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + run_hash
    run = root / "artifacts/runs" / run_id
    run.mkdir(parents=True, exist_ok=False)
    for name, model in models.items():
        model.booster_.save_model(str(run / f"model_{name}.txt"))
    future.sort_values(["hour_start", "zone_id"]).to_parquet(
        run / "predictions.parquet", index=False
    )
    for filename in (
        "hourly.parquet",
        "zones.geojson",
        "zones.csv",
        "quality.json",
        "sources.json",
        "prepared.json",
    ):
        shutil.copyfile(processed / filename, run / filename)
    shutil.copytree(
        Path(__file__).parent,
        run / "source/fleetscope",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("pyproject.toml", "uv.lock"):
        if (root / name).exists():
            shutil.copyfile(root / name, run / name)
    report = {
        "selected_model": selected,
        "selected_baseline": baseline,
        "selection_metric": "validation MAE",
        "overall": overall,
        "slices": slices,
        "excluded_zone_hours": exclusions,
        "training_rows": len(train),
        "training_volume_groups": {str(k): str(v) for k, v in volume_groups.items()},
        "status": config["evaluation_status"],
    }
    write_json(run / "metrics.json", report)
    test = future.loc[future.split.eq("test")].copy()
    hourly_errors = (
        test.assign(error=abs(test.predicted_pickups - test.pickup_count))
        .groupby("hour_start")
        .error.mean()
    )
    examples = []
    for title, subset in (
        ("Weekday evening", test.loc[(test.weekday < 5) & test.hour.eq(17)]),
        ("Weekend afternoon", test.loc[(test.weekday >= 5) & test.hour.eq(14)]),
    ):
        if not subset.empty:
            examples.append(
                {
                    "title": title,
                    "hour": subset.hour_start.min().isoformat(),
                    "note": "First matching test-period hour; inspect forecasts against recorded pickups.",
                }
            )
    examples.append(
        {
            "title": "Largest hourly error",
            "hour": hourly_errors.idxmax().isoformat(),
            "note": "Hindsight-selected test hour with the largest mean absolute zone error.",
        }
    )
    versions = {
        name: importlib.metadata.version(name)
        for name in (
            "duckdb",
            "pandas",
            "numpy",
            "pyarrow",
            "lightgbm",
            "scikit-learn",
            "fastapi",
            "tzdata",
            "pyproj",
            "pyshp",
        )
    }
    metadata = {
        "run_id": run_id,
        "model_version": run_id,
        "data_version": quality["data_version"],
        "config": config,
        "code_sha256": fingerprint,
        "python": platform.python_version(),
        "packages": versions,
        "feature_sets": FEATURE_SETS,
        "selected_model": selected,
        "feed_assumption": "Hypothetical hourly pickup feed reconstructed from delayed public TLC files.",
        "calendar": "US federal observed holidays; not a complete NYC event calendar",
        "examples": examples,
    }
    lines = [
        "# FleetScope starter benchmark",
        "",
        f"Run: `{run_id}`",
        "",
        f"Selected on validation: **{selected}**; reference baseline: **{baseline}**.",
        "",
        "Q1 2025 starter evaluation; no claim of full-year seasonal generalization.",
        "",
        "| Split | Predictor | MAE | WAPE |",
        "| --- | --- | ---: | ---: |",
    ]
    for split in ("validation", "test"):
        for name, stats in overall[split].items():
            wape = f"{stats['wape']:.2%}" if stats["wape"] is not None else "undefined"
            lines.append(f"| {split} | {name} | {stats['mae']:.3f} | {wape} |")
    (run / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    metadata["artifacts"] = {
        p.relative_to(run).as_posix(): sha256(p) for p in run.rglob("*") if p.is_file()
    }
    write_json(run / "metadata.json", metadata)
    write_json(root / "artifacts/latest.json", {"run": str(run.relative_to(root))})
    print(f"Saved run: {run}", flush=True)
    print(json.dumps(overall["test"][f"model_{selected}"], indent=2), flush=True)
    return run
