import numpy as np
import pandas as pd
import pytest

from fleetscope.data import complete_grid, localize_hours
from fleetscope.features import WEEKLY, build_features, seasonal_baseline, split_labels


def observations(start="2025-01-01", periods=24 * 30, zones=(4, 12)):
    hours = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    index = pd.MultiIndex.from_product([hours, zones], names=["hour_start", "zone_id"])
    frame = index.to_frame(index=False)
    frame["pickup_count"] = np.arange(len(frame), dtype=float)
    return frame


@pytest.mark.parametrize("delay", [0, 1, 2])
def test_target_and_future_observations_cannot_change_features(delay):
    original = observations()
    target = pd.Timestamp("2025-01-20T12:00Z")
    modified = original.copy()
    cutoff = target - pd.Timedelta(hours=delay)
    modified.loc[modified.hour_start >= cutoff, "pickup_count"] = 999999
    before = build_features(original, delay)
    after = build_features(modified, delay)
    pd.testing.assert_frame_equal(
        before.loc[before.hour_start.eq(target), WEEKLY],
        after.loc[after.hour_start.eq(target), WEEKLY],
    )


def test_delay_uses_latest_completed_available_hour():
    raw = observations()
    target = pd.Timestamp("2025-01-20T12:00Z")
    row = build_features(raw, 2).query("zone_id == 4").set_index("hour_start").loc[target]
    expected = raw.loc[
        raw.zone_id.eq(4) & raw.hour_start.eq(target - pd.Timedelta(hours=3)), "pickup_count"
    ].iloc[0]
    assert row.lag_1 == expected


def test_lags_never_cross_zone_boundaries():
    frame = build_features(observations())
    assert frame.groupby("zone_id").head(1).lag_1.isna().all()


def test_grid_rejects_missing_hours_instead_of_shifting_row_positions():
    raw = observations().drop(index=20)
    with pytest.raises(ValueError, match="hourly grid"):
        build_features(raw)


def test_ambiguous_and_nonexistent_local_timestamps_are_not_guessed():
    raw = pd.Series(pd.to_datetime(["2025-03-09 02:30", "2025-11-02 01:30", "2025-03-09 03:30"]))
    resolved = localize_hours(raw)
    assert resolved.isna().tolist() == [True, True, False]
    assert resolved.iloc[2] == pd.Timestamp("2025-03-09T07:30Z")


def test_fall_back_hours_are_unavailable_not_zero():
    counts = pd.DataFrame(
        {"hour_start": pd.to_datetime(["2025-11-02T04:00Z"]), "zone_id": [4], "pickup_count": [3]}
    )
    frame = complete_grid(
        counts,
        [4, 12],
        pd.Timestamp("2025-11-02", tz="America/New_York"),
        pd.Timestamp("2025-11-03", tz="America/New_York"),
    )
    assert len(frame) == 25 * 2
    assert frame.loc[~frame.available, "pickup_count"].isna().all()
    assert (~frame.available).sum() == 4
    assert frame.loc[frame.available & frame.zone_id.eq(12), "pickup_count"].eq(0).all()


def test_previous_week_uses_local_time_across_spring_dst():
    raw = observations(start="2025-03-01", periods=24 * 20)
    target = pd.Timestamp("2025-03-10T16:00Z")  # noon EDT
    row = build_features(raw).query("zone_id == 4").set_index("hour_start").loc[target]
    reference = pd.Timestamp("2025-03-03T17:00Z")  # noon EST
    expected = raw.loc[raw.zone_id.eq(4) & raw.hour_start.eq(reference), "pickup_count"].iloc[0]
    assert row.previous_week == expected
    assert row.previous_week != row.lag_168


def test_all_zones_for_an_hour_stay_in_one_split():
    raw = observations()
    config = {
        "train_start": "2025-01-02T00:00Z",
        "validation_start": "2025-01-10T00:00Z",
        "test_start": "2025-01-20T00:00Z",
        "test_end": "2025-01-25T00:00Z",
    }
    raw["split"] = split_labels(raw.hour_start, config)
    assert raw.groupby("hour_start").split.nunique().eq(1).all()
    assert raw.loc[raw.hour_start.eq(pd.Timestamp(config["test_start"])), "split"].eq("test").all()
    assert (
        raw.loc[raw.hour_start.eq(pd.Timestamp(config["test_end"])), "split"].eq("excluded").all()
    )


def test_seasonal_average_only_uses_explicit_training_rows():
    frame = build_features(observations())
    training = frame.loc[frame.hour_start < pd.Timestamp("2025-01-15T00:00Z")]
    targets = frame.loc[frame.hour_start >= pd.Timestamp("2025-01-20T00:00Z")].copy()
    expected = seasonal_baseline(training, targets)
    targets["pickup_count"] = 1e9
    np.testing.assert_array_equal(expected, seasonal_baseline(training, targets))
