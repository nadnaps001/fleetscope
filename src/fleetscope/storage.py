"""Small, explicit artifact and configuration helpers."""

import hashlib
import json
import re
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pandas as pd

NY = "America/New_York"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def read_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    months = config["months"]
    if not months or any(not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m) for m in months):
        raise ValueError("months must contain YYYY-MM values")
    expected = pd.period_range(months[0], months[-1], freq="M").astype(str).tolist()
    if months != expected:
        raise ValueError("months must be unique, sorted, consecutive calendar months")
    if config.get("timezone") != NY:
        raise ValueError(f"timezone must be {NY}")
    dates = [
        pd.Timestamp(config[k])
        for k in ("train_start", "validation_start", "test_start", "test_end")
    ]
    if any(t.tzinfo is None or t != t.floor("h") for t in dates):
        raise ValueError("split boundaries must be timezone-aware whole hours")
    if not all(a < b for a, b in pairwise(dates)):
        raise ValueError("split boundaries must be strictly increasing")
    start = pd.Timestamp(months[0]).tz_localize(NY)
    end = (pd.Timestamp(months[-1]) + pd.offsets.MonthBegin()).tz_localize(NY)
    if dates[0] < start or dates[-1] > end:
        raise ValueError("split dates must lie within downloaded months")
    delay = config.get("observation_delay_hours", 0)
    if type(delay) is not int or not 0 <= delay <= 24:
        raise ValueError("observation_delay_hours must be an integer from 0 to 24")
    if config.get("n_jobs", 1) < 1 or config.get("n_estimators", 1) < 1:
        raise ValueError("n_jobs and n_estimators must be positive")
    return config


def data_version(manifest: dict) -> str:
    fingerprints = {k: v["sha256"] for k, v in sorted(manifest["files"].items())}
    return hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()[:16]
