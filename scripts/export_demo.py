"""Export an evaluated run as static JSON using the same replay API contract."""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from fleetscope.api import create_app
from fleetscope.storage import sha256


def compact_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def export_demo(root: Path, run: Path, destination: Path) -> dict:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Export destination must be empty; use a fresh folder for a new snapshot")
    with TestClient(create_app(root, run)) as client:
        metadata = client.get("/api/metadata").json()
        metadata["delivery"] = (
            "Saved historical replay exported for static hosting. No live inference."
        )
        for name in ("zones", "metrics", "quality"):
            response = client.get(f"/api/{name}")
            response.raise_for_status()
            compact_json(destination / f"{name}.json", response.json())
        for index, hour in enumerate(metadata["hours"]):
            response = client.get("/api/forecast", params={"hour": hour})
            response.raise_for_status()
            compact_json(destination / "hours" / f"{index}.json", response.json())
        compact_json(destination / "metadata.json", metadata)
    # Only export the observed history needed by the held-out replay, not trip-level data.
    hourly = pd.read_parquet(run / "hourly.parquet")
    delay = pd.Timedelta(hours=metadata["config"]["observation_delay_hours"])
    start = pd.Timestamp(metadata["hours"][0]) - delay - pd.Timedelta(hours=24)
    end = pd.Timestamp(metadata["hours"][-1]) - delay
    history = hourly.loc[(hourly.hour_start >= start) & (hourly.hour_start < end)]
    for zone_id, rows in history.groupby("zone_id"):
        records = [
            {
                "hour": row.hour_start.isoformat(),
                "recorded_pickups": None if pd.isna(row.pickup_count) else int(row.pickup_count),
            }
            for row in rows.sort_values("hour_start").itertuples()
        ]
        compact_json(destination / "history" / f"zone-{zone_id}.json", records)
    sources = json.loads((run / "sources.json").read_text(encoding="utf-8"))
    compact_json(destination / "sources.json", sources)
    files = {
        path.relative_to(destination).as_posix(): sha256(path)
        for path in sorted(destination.rglob("*.json"))
    }
    manifest = {
        "model_version": metadata["model_version"],
        "data_version": metadata["data_version"],
        "hours": len(metadata["hours"]),
        "zone_count": metadata["zone_count"],
        "source": "NYC TLC public yellow-taxi records; January–March 2025",
        "contents": "Zone-level aggregates, official geometry, saved predictions, and evaluation metadata",
        "files": files,
        "snapshot_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
    }
    compact_json(destination / "manifest.json", manifest)
    return manifest


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--output", type=Path, default=root / "demo/data")
    args = parser.parse_args()
    run = args.run or root / json.loads((root / "artifacts/latest.json").read_text())["run"]
    if not run.is_absolute():
        run = root / run
    manifest = export_demo(root, run, args.output)
    print(f"Exported {manifest['hours']} hours and {manifest['zone_count']} zones to {args.output}")


if __name__ == "__main__":
    main()
