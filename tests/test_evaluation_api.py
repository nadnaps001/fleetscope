import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fleetscope.api import create_app
from fleetscope.evaluation import evaluate, metrics
from fleetscope.storage import sha256, write_json


@pytest.fixture(scope="module")
def evaluated_run(tmp_path_factory):
    # Synthetic observations exist only in tests, never in the product demo.
    root = tmp_path_factory.mktemp("synthetic_evaluation")
    processed = root / "data/processed"
    processed.mkdir(parents=True)
    hours = pd.date_range(
        "2025-01-01", "2025-02-01", freq="h", inclusive="left", tz="America/New_York"
    ).tz_convert("UTC")
    frame = pd.MultiIndex.from_product([hours, [4, 12]], names=["hour_start", "zone_id"]).to_frame(
        index=False
    )
    frame["pickup_count"] = ((frame.hour_start.dt.hour + frame.zone_id) % 25).astype(float)
    frame.to_parquet(processed / "hourly.parquet", index=False)
    pd.DataFrame(
        {
            "LocationID": [4, 12],
            "Borough": ["Manhattan", "Manhattan"],
            "Zone": ["Test zone A", "Test zone B"],
        }
    ).to_csv(processed / "zones.csv", index=False)
    write_json(
        processed / "zones.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": z,
                    "properties": {"zone_id": z, "name": f"Test zone {z}", "borough": "Manhattan"},
                    "geometry": {"type": "Polygon", "coordinates": []},
                }
                for z in [4, 12]
            ],
        },
    )
    write_json(
        processed / "quality.json",
        {"data_version": "synthetic-test-only", "months": [{"month": "2025-01"}]},
    )
    write_json(processed / "sources.json", {"files": {}})
    write_json(
        processed / "prepared.json",
        {"artifacts": {p.name: sha256(p) for p in processed.iterdir() if p.is_file()}},
    )
    config = {
        "name": "synthetic-test-only",
        "months": ["2025-01"],
        "timezone": "America/New_York",
        "train_start": "2025-01-08T00:00:00-05:00",
        "validation_start": "2025-01-20T00:00:00-05:00",
        "test_start": "2025-01-25T00:00:00-05:00",
        "test_end": "2025-02-01T00:00:00-05:00",
        "observation_delay_hours": 0,
        "seed": 42,
        "n_estimators": 5,
        "learning_rate": 0.1,
        "num_leaves": 7,
        "n_jobs": 1,
        "evaluation_status": "synthetic-test-only",
    }
    return root, evaluate(config, root)


def test_wape_zero_denominator_is_explicit():
    assert metrics([0, 0], [1, 2])["wape"] is None
    with pytest.raises(ValueError, match="finite"):
        metrics([1, np.nan], [1, 2])


def test_model_selection_and_metrics_use_identical_rows(evaluated_run):
    _, run = evaluated_run
    report = json.loads((run / "metrics.json").read_text())
    for split in ["validation", "test"]:
        results = report["overall"][split]
        assert len({v["rows"] for v in results.values()}) == 1
        assert len({v["recorded_pickups_sum"] for v in results.values()}) == 1
    best = min(
        ("calendar", "recent", "weekly"),
        key=lambda n: report["overall"]["validation"][f"model_{n}"]["mae"],
    )
    assert report["selected_model"] == best


def test_replay_api_and_artifact_contract(evaluated_run):
    root, run = evaluated_run
    with TestClient(create_app(root, run)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/health").json()["mode"] == "historical_replay"
        metadata = client.get("/api/metadata").json()
        response = client.get("/api/forecast", params={"hour": metadata["hours"][0]})
        assert response.status_code == 200
        forecast = response.json()
        assert len(forecast["zones"]) == 2
        assert forecast["model_version"] == metadata["model_version"]
        assert forecast["recorded_available_at"] > forecast["prediction_time"]
        detail = client.get("/api/zones/4", params={"hour": metadata["hours"][0]}).json()
        assert len(detail["history"]) == 24
        assert all(h["hour"] < detail["observation_cutoff"] for h in detail["history"])
        assert client.get("/api/forecast", params={"hour": "2025-01-25"}).status_code == 422
        assert client.get("/api/forecast", params={"hour": "2020-01-01T00:00Z"}).status_code == 404
        assert client.get("/api/zones/999").status_code == 404


def test_api_rejects_tampered_run(evaluated_run, tmp_path):
    import shutil

    root, source = evaluated_run
    run = tmp_path / "altered-run"
    shutil.copytree(source, run)
    (run / "zones.csv").write_text("altered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        create_app(root, run)


def test_static_export_preserves_api_predictions_and_history(evaluated_run, tmp_path):
    import runpy
    import shutil
    from pathlib import Path

    export_demo = runpy.run_path("scripts/export_demo.py")["export_demo"]
    build_site = runpy.run_path("scripts/build_site.py")["build_site"]
    root, run = evaluated_run
    destination = tmp_path / "demo/data"
    manifest = export_demo(root, run, destination)
    with TestClient(create_app(root, run)) as client:
        metadata = client.get("/api/metadata").json()
        assert manifest["hours"] == len(metadata["hours"])
        for index in (0, len(metadata["hours"]) - 1):
            hour = metadata["hours"][index]
            saved = json.loads((destination / f"hours/{index}.json").read_text())
            assert saved == client.get("/api/forecast", params={"hour": hour}).json()
            history = json.loads((destination / "history/zone-4.json").read_text())
            cutoff = pd.Timestamp(hour)
            visible = [
                row
                for row in history
                if cutoff - pd.Timedelta(hours=24) <= pd.Timestamp(row["hour"]) < cutoff
            ]
            assert visible == client.get("/api/zones/4", params={"hour": hour}).json()["history"]
    write_json(
        tmp_path / "demo/project.json", {"repository_url": "https://github.com/example/fleetscope"}
    )
    shutil.copytree(Path("src/fleetscope/web"), tmp_path / "src/fleetscope/web")
    site = build_site(tmp_path)
    page = (site / "index.html").read_text(encoding="utf-8")
    assert 'data-mode="static"' in page
    assert 'href="/static/' not in page
    assert 'href="/api/' not in page
    assert 'href="./static/style.css"' in page
    assert (site / "data/manifest.json").exists()
    (destination / "hours/0.json").write_text("altered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        build_site(tmp_path)
