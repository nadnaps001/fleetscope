"""Download only official TLC sources; validate cached bytes on every reuse."""

import shutil
import urllib.request
from pathlib import Path

from .storage import sha256, utc_now, write_json

BASE = "https://d37ci6vzurychx.cloudfront.net"


def download_sources(config: dict, root: Path) -> dict:
    import json

    raw = root / "data/raw"
    raw.mkdir(parents=True, exist_ok=True)
    manifest_path = raw / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": {}}
    sources = {
        "taxi_zone_lookup.csv": f"{BASE}/misc/taxi_zone_lookup.csv",
        "taxi_zones.zip": f"{BASE}/misc/taxi_zones.zip",
        **{
            f"yellow_tripdata_{m}.parquet": f"{BASE}/trip-data/yellow_tripdata_{m}.parquet"
            for m in config["months"]
        },
    }
    for filename, url in sources.items():
        destination = raw / filename
        prior = manifest["files"].get(filename)
        if destination.exists():
            if not prior or sha256(destination) != prior["sha256"]:
                raise ValueError(f"Unverified or changed raw file: {destination}")
            print(f"Verified cached source: {filename}", flush=True)
            continue
        print(f"Downloading {filename} ...", flush=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        request = urllib.request.Request(url, headers={"User-Agent": "FleetScope/0.1"})
        try:
            with (
                urllib.request.urlopen(request, timeout=120) as response,
                temporary.open("wb") as out,
            ):
                expected = response.headers.get("Content-Length")
                shutil.copyfileobj(response, out, length=1024 * 1024)
            if temporary.stat().st_size == 0 or (
                expected is not None and temporary.stat().st_size != int(expected)
            ):
                raise ValueError(f"Incomplete source download: {filename}")
            digest = sha256(temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        manifest["files"][filename] = {
            "url": url,
            "sha256": digest,
            "bytes": destination.stat().st_size,
            "retrieved_at": utc_now(),
        }
        write_json(manifest_path, manifest)
    return manifest
