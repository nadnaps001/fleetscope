# FleetScope: measured first-build results

These are Q1 2025 starter results, not a full-year seasonal evaluation. All values below were computed from official TLC yellow-taxi records.

## Data and protocol

- Source records: **11,198,026**; accepted modeled pickups: **11,169,493**.
- Modeled NYC zones: **262**; zone-hours: **565,658**.
- Train: January 8–February 28; validation: March 1–14; test: March 15–31, 2025 (New York time).
- Every delay experiment selects its feature set and reference baseline using validation MAE only.
- Models and seasonal averages remain frozen during validation and test; prior observations update lag features.

## Observation-delay experiment

| Assumed feed delay | Selected features | Test MAE | Test WAPE | MAE improvement vs validation-selected baseline |
| --- | --- | ---: | ---: | ---: |
| 0 hours | weekly | 3.819 | 18.22% | 12.88% |
| 1 hours | weekly | 4.271 | 20.37% | 2.58% |
| 2 hours | weekly | 4.377 | 20.88% | 0.14% |

The comparison uses the same **106,634 test zone-hours** and observations for every delay. The same-local-hour weekly reference is unresolved for March 16 at 02:00 because the prior week crosses the nonexistent spring DST hour; that target hour is excluded consistently.

MAE is measured in pickups per zone-hour. WAPE is a ratio, not an accuracy percentage. The feed is hypothetical: public TLC files are released after a delay.

## Feature ablations and baselines (zero-hour delay)

| Split | Predictor | MAE | WAPE |
| --- | --- | ---: | ---: |
| validation | baseline_week | 4.736 | 21.93% |
| validation | baseline_seasonal | 3.972 | 18.40% |
| validation | model_calendar | 4.995 | 23.13% |
| validation | model_recent | 3.936 | 18.23% |
| validation | model_weekly | 3.704 | 17.16% |
| test | baseline_week | 4.750 | 22.66% |
| test | baseline_seasonal | 4.384 | 20.91% |
| test | model_calendar | 5.212 | 24.86% |
| test | model_recent | 4.018 | 19.17% |
| test | model_weekly | 3.819 | 18.22% |

## Verification and provenance

- Automated tests cover leakage, DST, missing/corrupt source handling, ingestion accounting, and API/artifact validation. An additional publishing test checks that the static demo preserves the API's forecasts and history, and rejects a changed data snapshot.
- Python lint and JavaScript syntax checks pass.
- A headless browser verified map rendering, layer switching, zone selection, historical trends, timeline navigation, guided examples, and desktop/mobile layouts with no JavaScript exceptions.
- Each run includes checksums, configuration, dependency versions, saved model files, and a snapshot of the package source.

- 0-hour delay: `20260924T140718353910Z-331ad1629ee8`
- 1-hour delay: `20260924T140742097490Z-699c40b2ad25`
- 2-hour delay: `20260924T140823136931Z-0c16db03ac16`

The fleet simulator and full seasonal evaluation remain to be implemented.
