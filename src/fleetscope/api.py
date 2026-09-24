"""Read-only, versioned historical replay API. No live inference is implied."""

import json
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .storage import NY, sha256


def create_app(root: Path, run: Path | None = None) -> FastAPI:
    if run is None:
        pointer = root / "artifacts/latest.json"
        if not pointer.exists():
            raise ValueError("No evaluated run exists. Run 'fleetscope build' first.")
        run = root / json.loads(pointer.read_text())["run"]
    elif not run.is_absolute():
        run = root / run
    metadata = json.loads((run / "metadata.json").read_text())
    for filename, expected in metadata["artifacts"].items():
        if sha256(run / filename) != expected:
            raise ValueError(f"Packaged artifact checksum mismatch: {filename}")
    predictions = pd.read_parquet(run / "predictions.parquet")
    predictions = predictions.loc[predictions.split.eq("test")].copy()
    hourly = pd.read_parquet(run / "hourly.parquet")
    geometry = json.loads((run / "zones.geojson").read_text())
    zone_meta = {f["id"]: f["properties"] for f in geometry["features"]}
    report = json.loads((run / "metrics.json").read_text())
    hours = sorted(predictions.hour_start.unique())
    available = set(hours)
    app = FastAPI(title="FleetScope", version="0.1.0", description=metadata["feed_assumption"])
    web = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=web), name="static")

    def selected_hour(value: str | None):
        if value is None:
            return pd.Timestamp(hours[0])
        try:
            timestamp = pd.Timestamp(value)
            if timestamp.tzinfo is None:
                raise ValueError("timezone required")
            timestamp = timestamp.tz_convert("UTC")
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "hour must be an ISO timestamp with a timezone") from exc
        if timestamp not in available:
            raise HTTPException(404, "Hour is outside the eligible held-out replay")
        return timestamp

    def versions():
        return {key: metadata[key] for key in ("model_version", "data_version")}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(web / "index.html")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "historical_replay", **versions()}

    @app.get("/api/metadata")
    def get_metadata():
        return {
            **metadata,
            "hours": [pd.Timestamp(t).isoformat() for t in hours],
            "timezone": NY,
            "zone_count": len(zone_meta),
            "legend_max": max(
                1, int(predictions[["pickup_count", "predicted_pickups"]].max().max())
            ),
            "error_max": max(
                1, int(abs(predictions.predicted_pickups - predictions.pickup_count).max())
            ),
        }

    @app.get("/api/zones")
    def zones():
        return geometry

    @app.get("/api/metrics")
    def get_metrics():
        return report

    @app.get("/api/quality")
    def quality():
        return json.loads((run / "quality.json").read_text())

    @app.get("/api/forecast")
    def forecast(hour: str | None = Query(default=None)):
        target = selected_hour(hour)
        rows = predictions.loc[predictions.hour_start.eq(target)]
        result = []
        for row in rows.itertuples():
            result.append(
                {
                    **zone_meta[row.zone_id],
                    "zone_id": int(row.zone_id),
                    "predicted_pickups": float(row.predicted_pickups),
                    "recorded_pickups": int(row.pickup_count),
                    "previous_week": float(row.baseline_week),
                    "seasonal_average": float(row.baseline_seasonal),
                    "error": float(row.predicted_pickups - row.pickup_count),
                }
            )
        return {
            "prediction_time": target.isoformat(),
            "target_hour": target.isoformat(),
            "observation_cutoff": (
                target - pd.Timedelta(hours=metadata["config"]["observation_delay_hours"])
            ).isoformat(),
            "observation_delay_hours": metadata["config"]["observation_delay_hours"],
            "recorded_available_at": (target + pd.Timedelta(hours=1)).isoformat(),
            "recorded_values": "retrospective evaluation values, unavailable at prediction_time",
            "units": "pickups/hour",
            "zones": result,
            **versions(),
        }

    @app.get("/api/zones/{zone_id}")
    def zone_detail(zone_id: int, hour: str | None = Query(default=None)):
        if zone_id not in zone_meta:
            raise HTTPException(404, "Unknown modeled zone")
        target = selected_hour(hour)
        cutoff = target - pd.Timedelta(hours=metadata["config"]["observation_delay_hours"])
        rows = hourly.loc[
            hourly.zone_id.eq(zone_id)
            & (hourly.hour_start < cutoff)
            & (hourly.hour_start >= cutoff - pd.Timedelta(hours=24))
        ]
        history = [
            {
                "hour": r.hour_start.isoformat(),
                "recorded_pickups": None if pd.isna(r.pickup_count) else int(r.pickup_count),
            }
            for r in rows.itertuples()
        ]
        return {
            **zone_meta[zone_id],
            "history": history,
            "target_hour": target.isoformat(),
            "observation_cutoff": cutoff.isoformat(),
            **versions(),
        }

    return app
