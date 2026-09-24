import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import shapefile
from pyproj import CRS

from fleetscope.data import prepare, verify_sources
from fleetscope.storage import read_config, sha256, write_json


def test_missing_source_stops_before_zero_filling(tmp_path):
    write_json(tmp_path / "data/raw/manifest.json", {"files": {}})
    with pytest.raises(ValueError, match="refusing zero-fill"):
        verify_sources({"months": ["2025-01"]}, tmp_path)


def test_modified_source_fails_checksum(tmp_path):
    raw = tmp_path / "data/raw"
    raw.mkdir(parents=True)
    source = raw / "taxi_zone_lookup.csv"
    source.write_text("original")
    write_json(raw / "manifest.json", {"files": {source.name: {"sha256": sha256(source)}}})
    source.write_text("changed")
    with pytest.raises(ValueError, match="checksum-mismatched"):
        verify_sources({"months": ["2025-01"]}, tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        {"months": ["2025-01", "2025-03"]},
        {"test_end": "2025-02-01T00:00:00-05:00"},
        {"test_start": "2025-03-15T00:00:00"},
        {"observation_delay_hours": -1},
    ],
)
def test_invalid_protocol_is_rejected(tmp_path, change):
    config = json.loads(Path("configs/starter.json").read_text())
    config.update(change)
    path = tmp_path / "config.json"
    write_json(path, config)
    with pytest.raises(ValueError):
        read_config(path)


def test_ingestion_accounts_for_every_record_and_fills_only_verified_month(tmp_path):
    raw = tmp_path / "data/raw"
    raw.mkdir(parents=True)
    # Deliberately tiny synthetic geography, used only to exercise the pipeline contract.
    shape_base = tmp_path / "fixture"
    with shapefile.Writer(str(shape_base)) as writer:
        writer.field("LocationID", "N")
        for zone_id, x in [(4, -74.0), (12, -73.9)]:
            writer.poly([[(x, 40.7), (x, 40.71), (x + 0.01, 40.71), (x + 0.01, 40.7), (x, 40.7)]])
            writer.record(zone_id)
    shape_base.with_suffix(".prj").write_text(CRS.from_epsg(4326).to_wkt())
    with zipfile.ZipFile(raw / "taxi_zones.zip", "w") as archive:
        for suffix in (".shp", ".shx", ".dbf", ".prj"):
            archive.write(shape_base.with_suffix(suffix), arcname="fixture" + suffix)
    pd.DataFrame(
        {
            "LocationID": [4, 12, 1, 264],
            "Borough": ["Manhattan", "Manhattan", "EWR", "Unknown"],
            "Zone": ["Test A", "Test B", "Newark", "Unknown"],
        }
    ).to_csv(raw / "taxi_zone_lookup.csv", index=False)
    pd.DataFrame(
        {
            "tpep_pickup_datetime": [
                "2025-01-01 00:15",
                "2025-01-01 00:45",
                "bad",
                "2025-02-01 00:00",
                "2025-01-02 00:00",
                "2025-01-02 00:00",
            ],
            "PULocationID": [4, 4, 4, 4, 1, 264],
        }
    ).to_parquet(raw / "yellow_tripdata_2025-01.parquet")
    write_json(
        raw / "manifest.json", {"files": {p.name: {"sha256": sha256(p)} for p in raw.iterdir()}}
    )
    processed = prepare({"months": ["2025-01"]}, tmp_path)
    quality = json.loads((processed / "quality.json").read_text())["months"][0]
    assert quality["raw_records"] == 6
    assert quality["accepted_records"] == 2
    assert sum(quality["rejected"].values()) == 4
    frame = pd.read_parquet(processed / "hourly.parquet")
    assert len(frame) == 31 * 24 * 2
    assert frame.pickup_count.sum() == 2
    assert frame.loc[frame.zone_id.eq(12), "pickup_count"].eq(0).all()
