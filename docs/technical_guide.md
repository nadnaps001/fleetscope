# FleetScope technical guide

For the project overview and live demo, start with the [README](../README.md).
This guide explains how to reproduce the experiment and run the Python API.

Forecast the next hour of **recorded NYC yellow-taxi pickups**, explore the results on official taxi-zone polygons, and inspect errors against seasonal baselines.

This is a historical replay using public TLC trip records. It assumes a hypothetical hourly pickup feed. Target-hour recorded values are shown retrospectively for evaluation; this is not a live dispatch system or a measurement of unmet demand.

## Working first release

- Official TLC source download with checksums and immutable raw files.
- DuckDB aggregation into complete zone-hour observations, explicit data-quality accounting, and UTC keys with New York calendar features.
- Same-local-hour-last-week and training-only seasonal-average baselines.
- Three global Poisson LightGBM experiments: calendar/location, recent history, and weekly history. Selection uses validation MAE.
- Identical evaluation rows, overall MAE/WAPE, error slices, feature ablations, and immutable run artifacts.
- FastAPI replay with a responsive map, fixed color scales, time scrubber, zone selection, historical trends, rankings, measured benchmark table, and guided examples.
- Tests for future-data leakage, observation delay, daylight saving, split boundaries, artifact integrity, and API behavior.

Fleet allocation simulation, calibrated intervals, and a full-year seasonal benchmark remain planned. The current dashboard is a small SVG prototype served by FastAPI; the custom React dashboard is a later milestone.

See [measured first-build results](first_results.md), including the one- and two-hour observation-delay experiments. On the zero-delay starter test, the selected model achieves **3.82 MAE** and **18.22% WAPE**, a **12.88% reduction in MAE** against the validation-selected seasonal baseline. These are measured starter results, not an accuracy guarantee.

![FleetScope historical replay using official TLC zones](screenshots/desktop.png)

## Run the local Python API

First reproduce the experiment using the commands in the next section. This creates `artifacts/latest.json` and a portable derived-data run in `artifacts/runs/`. These generated files are ignored by Git. For a quick demo without training, use the static preview instructions in the [README](../README.md).

From the project directory on Windows:

```powershell
.venv\Scripts\python.exe -m fleetscope.cli serve
```

Open **http://127.0.0.1:8000**. Interactive API documentation is at **http://127.0.0.1:8000/docs**. The server binds only to localhost.

On a new machine with a copied run folder:

```powershell
uv sync --frozen
uv run --frozen fleetscope serve --run artifacts/runs/YOUR_RUN_DIRECTORY
```

The packaged run contains geometry, aggregates, predictions, models, provenance, and metrics; raw trip files are not required to serve it. API startup verifies packaged artifact checksums. The map needs no map API key or tile service. Fonts fall back to local system fonts if the optional web font cannot load.

## Reproduce from raw TLC files

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). The first download needs internet access; subsequent runs verify and reuse cached sources.

```powershell
uv sync --frozen --extra dev
uv run --frozen fleetscope build --config configs/starter.json
uv run --frozen fleetscope serve
```

The `build` command runs these individually available stages:

```powershell
uv run --frozen fleetscope download
uv run --frozen fleetscope prepare
uv run --frozen fleetscope evaluate
```

The starter downloads three monthly trip files plus the official zone lookup and shapefile. Keep at least 1 GB of free disk space for inputs, intermediate artifacts, and runs; the Python environment and dependency cache need additional space. `prepare` stops if any configured source is missing or checksum-mismatched, rather than silently generating zero counts.

To evaluate a delayed observation feed with the same split protocol:

```powershell
uv run --frozen fleetscope evaluate --delay 1
uv run --frozen fleetscope evaluate --delay 2
uv run --frozen python scripts/summarize_runs.py
```

Every evaluation creates a new run and updates `artifacts/latest.json`. The summary script checks that delay experiments use identical test observations, writes `docs/first_results.md`, and restores the zero-delay run as the default demo. Use `--run` to serve a specific earlier run. No automatic model retraining or hyperparameter search occurs.

## Frozen starter protocol

| Interval | Local New York dates, start inclusive / end exclusive |
| --- | --- |
| Source files | January 1 – April 1, 2025 |
| Training targets | January 8 – March 1, 2025 |
| Validation targets | March 1 – March 15, 2025 |
| Test targets | March 15 – April 1, 2025 |

The first week supplies historical features. Models and the seasonal-average baseline are frozen at the start of validation. The best feature set and reference baseline are selected using validation MAE only. Completed prior observations inside validation/test may supply lag features, under the explicitly assumed hourly feed. An observation delay also limits training labels available at the fitting cutoff.

This is a **Q1 starter benchmark**, not a final full-year portfolio result. Expand the protocol to held-out windows across seasons before making seasonal generalization claims. Hyperparameters are in `configs/starter.json`; do not tune them against reported test results.

Feature ablations use the same eligible rows and training budget. Rows missing any feature required by the full comparison are excluded from all methods, with exclusion counts reported. Volume groups are defined from training data. Rush hours are weekdays 07:00–09:59 and 16:00–18:59. The holiday feature uses the US federal observed-holiday calendar; it is not a complete NYC event calendar. WAPE is the sum of absolute errors divided by recorded pickups, and is `null` when the denominator is zero.

## Time and geography rules

- Only TLC zones with usable geometry in the five NYC boroughs are modeled; Newark Airport and unknown/outside-NYC locations are excluded and counted.
- Monthly source membership is determined from the local pickup timestamp. Out-of-month records are quarantined; duplicate-looking trips are not automatically removed, because the selected fields do not establish trip identity.
- Ambiguous autumn and nonexistent spring local timestamps are quarantined. Both repeated autumn hours are marked unavailable across all zones, never zero-filled. Calendar features are local; stored hour keys are UTC.
- The weekly baseline refers to the same local weekday/hour seven days earlier. The elapsed-hour `lag_168` is a separate feature and can differ around DST. Unresolvable weekly references are unavailable.
- Source-file completeness does not establish that every vendor-reported trip is present. The quality report accounts for filtering, not unknown underreporting.

## Artifacts and project layout

```text
configs/starter.json        Frozen starter experiment
src/fleetscope/download.py  Official source download and integrity checks
src/fleetscope/data.py      Geography, aggregation, completeness, DST
src/fleetscope/features.py  Shared feature and time-split definitions
src/fleetscope/evaluation.py Models, selection, metrics, immutable runs
src/fleetscope/api.py       Read-only historical replay
src/fleetscope/web/         Map prototype
tests/                     Synthetic fixtures used only for verification
data/raw/                  Immutable TLC files and source manifest (generated)
data/processed/            Aggregates, geometry, quality report (generated)
artifacts/runs/<run>/       Portable evaluated demo and provenance (generated)
```

Each run records source SHA-256 values, a source-code fingerprint, dependency versions, feature lists, observation delay, split dates, models, predictions, quality report, metrics, and a readable `report.md`. Model values are nonnegative expected pickup counts and are not rounded before evaluation. Guided cases include the first matching weekday/weekend examples and the retrospectively identified hour with largest zone-average error.

## Checks

```powershell
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check src tests scripts
node --check src/fleetscope/web/app.js
```

The tests use clearly isolated synthetic fixtures. Product data and reported results come from the downloaded TLC records.

## Next milestones

1. Implement the simulator capacity and fleet-conservation contract, then compare stay-put, historical, forecast, constrained, and hindsight policies under identical assumptions.
2. Expand the completed observation-delay experiment to longer rolling evaluation windows across seasons.
3. Evaluate calibrated prediction intervals and finish the custom dashboard and shareable walkthrough.

The expanded specification is in [the project plan](../FleetScope_NYC_Taxi_Demand_Forecasting_Project_Plan.md). See the [hosting guide](hosting.md) for the public GitHub Pages deployment.

Sources: [TLC trip records, lookup and geometry](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page), [yellow-taxi data dictionary](https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf), [LightGBM](https://lightgbm.readthedocs.io/en/stable/), [pandas timezone handling](https://pandas.pydata.org/docs/reference/api/pandas.Series.dt.tz_localize.html).
