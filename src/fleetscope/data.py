"""Validated raw trips -> complete, UTC-keyed zone-hour observations."""

import io
import json
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
import shapefile
from pyproj import CRS, Transformer

from .storage import NY, data_version, sha256, write_json

BOROUGHS = {"Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"}


def verify_sources(config: dict, root: Path) -> dict:
    raw = root / "data/raw"
    manifest_file = raw / "manifest.json"
    if not manifest_file.exists():
        raise ValueError("Source manifest missing. Run fleetscope download first.")
    manifest = json.loads(manifest_file.read_text())
    required = ["taxi_zone_lookup.csv", "taxi_zones.zip"] + [
        f"yellow_tripdata_{m}.parquet" for m in config["months"]
    ]
    selected = {"files": {}}
    for name in required:
        path = raw / name
        record = manifest["files"].get(name)
        if not path.exists() or not record or sha256(path) != record["sha256"]:
            raise ValueError(f"Missing or checksum-mismatched source: {name}; refusing zero-fill")
        selected["files"][name] = record
    return selected


def prepare_geometry(raw: Path, destination: Path) -> pd.DataFrame:
    lookup = pd.read_csv(raw / "taxi_zone_lookup.csv")
    features = []
    with zipfile.ZipFile(raw / "taxi_zones.zip") as archive:

        def member(suffix):
            return next(n for n in archive.namelist() if n.endswith(suffix))

        projection = CRS.from_wkt(archive.read(member(".prj")).decode())
        transform = Transformer.from_crs(projection, "EPSG:4326", always_xy=True)
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(member(".shp"))),
            shx=io.BytesIO(archive.read(member(".shx"))),
            dbf=io.BytesIO(archive.read(member(".dbf"))),
        )
        for record in reader.iterShapeRecords():
            props = record.record.as_dict()
            zone_id = int(props["LocationID"])
            row = lookup.loc[lookup.LocationID.eq(zone_id)]
            if row.empty or row.iloc[0].Borough not in BOROUGHS:
                continue
            geometry = record.shape.__geo_interface__

            def project(coords):
                if isinstance(coords[0], (int, float)):
                    return list(transform.transform(coords[0], coords[1]))
                return [project(c) for c in coords]

            features.append(
                {
                    "type": "Feature",
                    "id": zone_id,
                    "properties": {
                        "zone_id": zone_id,
                        "name": str(row.iloc[0].Zone),
                        "borough": str(row.iloc[0].Borough),
                    },
                    "geometry": {
                        "type": geometry["type"],
                        "coordinates": project(geometry["coordinates"]),
                    },
                }
            )
    if not features or len({f["id"] for f in features}) != len(features):
        raise ValueError("Official geometry is empty or has duplicate zone IDs")
    write_json(destination, {"type": "FeatureCollection", "features": features})
    return lookup.loc[lookup.LocationID.isin([f["id"] for f in features])].copy()


def localize_hours(local: pd.Series) -> pd.Series:
    """Naive vendor timestamps cannot resolve repeated autumn hours."""
    return local.dt.tz_localize(NY, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")


def complete_grid(counts: pd.DataFrame, zone_ids: list[int], start, end) -> pd.DataFrame:
    hours = pd.date_range(start, end, freq="h", inclusive="left").tz_convert("UTC")
    grid = pd.MultiIndex.from_product([hours, sorted(zone_ids)], names=["hour_start", "zone_id"])
    indexed = counts.set_index(["hour_start", "zone_id"])["pickup_count"]
    if indexed.index.has_duplicates:
        raise ValueError("Duplicate zone-hour aggregates")
    frame = indexed.reindex(grid, fill_value=0).rename("pickup_count").reset_index()
    local_keys = hours.tz_convert(NY).tz_localize(None)
    ambiguous_hours = hours[local_keys.duplicated(keep=False)]
    frame["available"] = ~frame.hour_start.isin(ambiguous_hours)
    frame.loc[~frame.available, "pickup_count"] = float("nan")
    return frame


def prepare(config: dict, root: Path) -> Path:
    manifest = verify_sources(
        config, root
    )  # Verify every required source BEFORE filling any zeros.
    raw, processed = root / "data/raw", root / "data/processed"
    processed.mkdir(parents=True, exist_ok=True)
    zones = prepare_geometry(raw, processed / "zones.geojson")
    zones.to_csv(processed / "zones.csv", index=False)
    lookup = pd.read_csv(raw / "taxi_zone_lookup.csv")
    modeled_ids = zones.LocationID.tolist()
    lookup["zone_issue"] = "missing_geometry"
    lookup.loc[~lookup.Borough.isin(BOROUGHS), "zone_issue"] = "outside_nyc"
    lookup.loc[lookup.Borough.isin(["Unknown"]) | lookup.Borough.isna(), "zone_issue"] = (
        "unknown_zone"
    )
    lookup.loc[lookup.LocationID.isin(modeled_ids), "zone_issue"] = "accepted"
    report = {
        "data_version": data_version(manifest),
        "months": [],
        "timezone": NY,
        "modeled_zones": len(modeled_ids),
        "dst_policy": "quarantine ambiguous/nonexistent naive timestamps; repeated hours unavailable",
        "completeness": "all configured source files verified; vendor completeness unknown",
    }
    parts = []
    with duckdb.connect() as con:
        con.execute("SET threads=4")
        con.register("zone_lookup", lookup)
        for month in config["months"]:
            filename = raw / f"yellow_tripdata_{month}.parquet"
            print(f"Aggregating {filename.name} ...", flush=True)
            relation = con.read_parquet(str(filename))
            if not {"tpep_pickup_datetime", "PULocationID"}.issubset(relation.columns):
                raise ValueError(f"Missing required columns in {filename.name}")
            relation.create_view("raw_trips", replace=True)
            start = pd.Timestamp(month)
            end = start + pd.offsets.MonthBegin()
            grouped = con.execute(
                """
                WITH typed AS (
                    SELECT TRY_CAST(tpep_pickup_datetime AS TIMESTAMP) AS pickup,
                           TRY_CAST(PULocationID AS DOUBLE) AS zone
                    FROM raw_trips
                ), classified AS (
                    SELECT date_trunc('hour', pickup) AS local_hour, typed.zone,
                        CASE WHEN pickup IS NULL THEN 'invalid_timestamp'
                             WHEN pickup < ? OR pickup >= ? THEN 'outside_source_month'
                             WHEN typed.zone IS NULL OR typed.zone != floor(typed.zone) THEN 'invalid_zone'
                             WHEN l.LocationID IS NULL THEN 'unknown_zone'
                             ELSE l.zone_issue END AS reason
                    FROM typed LEFT JOIN zone_lookup l ON typed.zone = l.LocationID
                )
                SELECT local_hour, zone, reason, count(*) AS pickup_count
                FROM classified GROUP BY ALL
            """,
                [start.to_pydatetime(), end.to_pydatetime()],
            ).df()
            total = int(grouped.pickup_count.sum())
            accepted = grouped.loc[grouped.reason.eq("accepted")].copy()
            accepted["hour_start"] = localize_hours(accepted.local_hour)
            unresolved = accepted.hour_start.isna()
            rejected = {
                str(k): int(v)
                for k, v in grouped.loc[~grouped.reason.eq("accepted")]
                .groupby("reason")
                .pickup_count.sum()
                .items()
            }
            rejected["ambiguous_or_nonexistent_local_time"] = int(
                accepted.loc[unresolved, "pickup_count"].sum()
            )
            accepted = accepted.loc[~unresolved].rename(columns={"zone": "zone_id"})
            accepted["zone_id"] = accepted.zone_id.astype(int)
            valid_total = int(accepted.pickup_count.sum())
            if valid_total + sum(rejected.values()) != total:
                raise AssertionError("Quality accounting did not reconcile")
            parts.append(accepted[["hour_start", "zone_id", "pickup_count"]])
            report["months"].append(
                {
                    "month": month,
                    "raw_records": total,
                    "accepted_records": valid_total,
                    "rejected": rejected,
                }
            )
    begin = pd.Timestamp(config["months"][0]).tz_localize(NY)
    end = (pd.Timestamp(config["months"][-1]) + pd.offsets.MonthBegin()).tz_localize(NY)
    frame = complete_grid(pd.concat(parts, ignore_index=True), modeled_ids, begin, end)
    report.update(
        {
            "zone_hours": len(frame),
            "unavailable_zone_hours": int((~frame.available).sum()),
            "zero_zone_hours": int(frame.pickup_count.eq(0).sum()),
        }
    )
    frame.to_parquet(processed / "hourly.parquet", index=False)
    write_json(processed / "quality.json", report)
    write_json(processed / "sources.json", manifest)
    write_json(
        processed / "prepared.json",
        {
            "data_version": report["data_version"],
            "artifacts": {
                name: sha256(processed / name)
                for name in (
                    "hourly.parquet",
                    "zones.geojson",
                    "zones.csv",
                    "quality.json",
                    "sources.json",
                )
            },
        },
    )
    print(f"Prepared {len(frame):,} zone-hours across {len(modeled_ids)} NYC zones.", flush=True)
    return processed
