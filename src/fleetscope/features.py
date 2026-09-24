"""The shared feature contract for training and historical replay."""

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from .storage import NY

CALENDAR = ["zone_id", "hour", "weekday", "weekend", "month", "holiday"]
RECENT = CALENDAR + ["lag_1", "lag_2", "lag_24", "rolling_6", "rolling_24"]
WEEKLY = RECENT + ["previous_week", "lag_168"]
FEATURE_SETS = {"calendar": CALENDAR, "recent": RECENT, "weekly": WEEKLY}


def build_features(hourly: pd.DataFrame, delay: int = 0) -> pd.DataFrame:
    if type(delay) is not int or not 0 <= delay <= 24:
        raise ValueError("delay must be an integer between 0 and 24")
    frame = hourly.sort_values(["zone_id", "hour_start"]).copy().reset_index(drop=True)
    if frame.duplicated(["zone_id", "hour_start"]).any():
        raise ValueError("Duplicate zone-hour keys")
    if frame.hour_start.dt.tz is None:
        raise ValueError("hour_start must be timezone-aware")
    diffs = frame.groupby("zone_id").hour_start.diff().dropna()
    if not diffs.eq(pd.Timedelta(hours=1)).all():
        raise ValueError(
            "Features require an hourly grid; missing observations must remain explicit"
        )
    if (frame.pickup_count.dropna() < 0).any():
        raise ValueError("Pickup counts cannot be negative")
    local = frame.hour_start.dt.tz_convert(NY)
    frame["hour"] = local.dt.hour
    frame["weekday"] = local.dt.dayofweek
    frame["weekend"] = (frame.weekday >= 5).astype(int)
    frame["month"] = local.dt.month
    holidays = USFederalHolidayCalendar().holidays(start=local.min().date(), end=local.max().date())
    frame["holiday"] = local.dt.tz_localize(None).dt.normalize().isin(holidays).astype(int)
    # Lag 1 at target t is the completed interval [t-1h, t); delay shifts the cutoff.
    groups = frame.groupby("zone_id", sort=False).pickup_count
    for lag in (1, 2, 24, 168):
        frame[f"lag_{lag}"] = groups.shift(lag + delay)
    for window in (6, 24):
        frame[f"rolling_{window}"] = groups.transform(
            lambda values, window=window: (
                values.shift(1 + delay).rolling(window, min_periods=window).mean()
            )
        )
    # Same local wall-clock hour, not necessarily 168 elapsed hours across DST.
    reference = (
        (local.dt.tz_localize(None) - pd.Timedelta(days=7))
        .dt.tz_localize(NY, ambiguous="NaT", nonexistent="NaT")
        .dt.tz_convert("UTC")
    )
    index = pd.MultiIndex.from_arrays([reference, frame.zone_id], names=["hour_start", "zone_id"])
    observations = frame.set_index(["hour_start", "zone_id"]).pickup_count
    frame["previous_week"] = observations.reindex(index).to_numpy()
    frame["prediction_time"] = frame.hour_start
    frame["observation_cutoff"] = frame.hour_start - pd.Timedelta(hours=delay)
    return frame


def split_labels(times: pd.Series, config: dict) -> pd.Series:
    labels = pd.Series("excluded", index=times.index)
    boundaries = [
        pd.Timestamp(config[key])
        for key in ("train_start", "validation_start", "test_start", "test_end")
    ]
    for name, start, end in zip(("train", "validation", "test"), boundaries, boundaries[1:]):
        labels.loc[(times >= start) & (times < end)] = name
    return labels


def seasonal_baseline(train: pd.DataFrame, targets: pd.DataFrame) -> np.ndarray:
    seasonal = train.groupby(["zone_id", "weekday", "hour"]).pickup_count.mean()
    keys = pd.MultiIndex.from_frame(targets[["zone_id", "weekday", "hour"]])
    values = pd.Series(seasonal.reindex(keys).to_numpy(), index=targets.index)
    zone_fallback = targets.zone_id.map(train.groupby("zone_id").pickup_count.mean())
    return values.fillna(zone_fallback).fillna(train.pickup_count.mean()).to_numpy()
