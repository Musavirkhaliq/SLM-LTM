from __future__ import annotations

import csv
import io
import json
from typing import Any

import boto3
from botocore.config import Config

from slm_evaluation.config import EvaluationSettings


def _get_s3_client(settings: EvaluationSettings):
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, s3={"addressing_style": "path"}),
    )


def _parse_content(body: str, filename: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if filename.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(body))
        for row in reader:
            rows.append(dict(row))
    elif filename.endswith(".json") and not filename.endswith(".jsonl"):
        parsed = json.loads(body)
        if isinstance(parsed, list):
            rows = [r for r in parsed if isinstance(r, dict)]
        elif isinstance(parsed, dict):
            rows = [parsed]
    else:
        # Default jsonl
        for line in body.splitlines():
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        rows.append(obj)
                except Exception:
                    continue
    return rows


def download_dataset_rows(storage_uri: str, settings: EvaluationSettings) -> list[dict[str, Any]]:
    """Download and parse dataset rows from S3 or local path."""
    if not storage_uri:
        raise ValueError("storage_uri is empty")

    if storage_uri.startswith("s3://"):
        bucket, _, key = storage_uri[5:].partition("/")
        if not bucket or not key:
            raise ValueError(f"Invalid S3 URI: {storage_uri}")
        client = _get_s3_client(settings)
        resp = client.get_object(Bucket=bucket, Key=key)
        body = resp["Body"].read().decode("utf-8")
        return _parse_content(body, key)

    from pathlib import Path

    clean_path = storage_uri[7:] if storage_uri.startswith("file://") else storage_uri
    file_path = Path(clean_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Dataset file not found at: {storage_uri}")

    body = file_path.read_text(encoding="utf-8")
    return _parse_content(body, file_path.name)

