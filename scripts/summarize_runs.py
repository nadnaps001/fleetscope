"""Publish a small report from completed 0/1/2-hour observation-delay experiments."""

import json
import shutil
from pathlib import Path

import pandas as pd

from fleetscope.storage import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    runs = {}
    for path in sorted((root / "artifacts/runs").glob("*/metadata.json")):
        metadata = json.loads(path.read_text())
        if metadata["config"]["name"] == "yellow-2025-q1-starter":
            runs[metadata["config"]["observation_delay_hours"]] = (path.parent, metadata)
    if not all(delay in runs for delay in (0, 1, 2)):
        raise ValueError("Run the starter evaluation with --delay 0, 1, and 2 first")
    base_path, base_meta = runs[0]
    keys = None
    lines = [
        "# FleetScope: measured first-build results",
        "",
        (
            "These are Q1 2025 starter results, not a full-year seasonal evaluation. "
            "All values below were computed from official TLC yellow-taxi records."
        ),
        "",
        "## Data and protocol",
        "",
    ]
    quality = json.loads((base_path / "quality.json").read_text())
    raw_records = sum(m["raw_records"] for m in quality["months"])
    accepted = sum(m["accepted_records"] for m in quality["months"])
    lines += [
        f"- Source records: **{raw_records:,}**; accepted modeled pickups: **{accepted:,}**.",
        f"- Modeled NYC zones: **{quality['modeled_zones']}**; zone-hours: **{quality['zone_hours']:,}**.",
        "- Train: January 8–February 28; validation: March 1–14; test: March 15–31, 2025 (New York time).",
        "- Every delay experiment selects its feature set and reference baseline using validation MAE only.",
        "- Models and seasonal averages remain frozen during validation and test; prior observations update lag features.",
        "",
        "## Observation-delay experiment",
        "",
        "| Assumed feed delay | Selected features | Test MAE | Test WAPE | MAE improvement vs validation-selected baseline |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for delay in (0, 1, 2):
        path, metadata = runs[delay]
        assert metadata["data_version"] == base_meta["data_version"]
        assert metadata["code_sha256"] == base_meta["code_sha256"]
        config = {k: v for k, v in metadata["config"].items() if k != "observation_delay_hours"}
        baseline_config = {
            k: v for k, v in base_meta["config"].items() if k != "observation_delay_hours"
        }
        assert config == baseline_config
        predictions = pd.read_parquet(path / "predictions.parquet")
        test_keys = predictions.loc[
            predictions.split.eq("test"), ["hour_start", "zone_id", "pickup_count"]
        ].reset_index(drop=True)
        if keys is None:
            keys = test_keys
        else:
            pd.testing.assert_frame_equal(keys, test_keys)
        report = json.loads((path / "metrics.json").read_text())
        selected = report["selected_model"]
        metrics = report["overall"]["test"][f"model_{selected}"]
        lines.append(
            f"| {delay} hours | {selected} | {metrics['mae']:.3f} | {metrics['wape']:.2%} | {metrics['relative_mae_improvement']:.2%} |"
        )
    lines += [
        "",
        (
            f"The comparison uses the same **{len(keys):,} test zone-hours** and observations for every delay. "
            "The same-local-hour weekly reference is unresolved for March 16 at 02:00 because the prior week crosses the nonexistent spring DST hour; that target hour is excluded consistently."
        ),
        "",
        (
            "MAE is measured in pickups per zone-hour. WAPE is a ratio, not an accuracy percentage. "
            "The feed is hypothetical: public TLC files are released after a delay."
        ),
        "",
        "## Feature ablations and baselines (zero-hour delay)",
        "",
    ]
    zero_report = (base_path / "report.md").read_text(encoding="utf-8")
    lines.extend(zero_report[zero_report.index("| Split |") :].strip().splitlines())
    lines += [
        "",
        "## Verification and provenance",
        "",
        "- Automated tests cover leakage, DST, missing/corrupt source handling, ingestion accounting, and API/artifact validation. The publishing test also checks static-export parity and snapshot integrity.",
        "- Python lint and JavaScript syntax checks pass.",
        "- A headless browser verified map rendering, layer switching, zone selection, historical trends, timeline navigation, guided examples, and desktop/mobile layouts with no JavaScript exceptions.",
        "- Each run includes checksums, configuration, dependency versions, saved model files, and a snapshot of the package source.",
        "",
    ]
    for delay in (0, 1, 2):
        lines.append(f"- {delay}-hour delay: `{runs[delay][0].name}`")
    lines += ["", "The fleet simulator and full seasonal evaluation remain to be implemented.", ""]
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "first_results.md").write_text("\n".join(lines), encoding="utf-8")
    shots = docs / "screenshots"
    shots.mkdir(exist_ok=True)
    for name in ("desktop", "mobile"):
        source = root / f"artifacts/dashboard-{name}.png"
        if source.exists():
            shutil.copyfile(source, shots / f"{name}.png")
    # Restore the zero-delay starter as the default demo after the sensitivity runs.
    write_json(root / "artifacts/latest.json", {"run": base_path.relative_to(root).as_posix()})
    print(docs / "first_results.md")


if __name__ == "__main__":
    main()
