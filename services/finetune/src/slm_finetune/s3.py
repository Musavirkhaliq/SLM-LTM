from __future__ import annotations

import asyncio
from typing import Any
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import structlog

from slm_finetune.config import FinetuneSettings

logger = structlog.get_logger(__name__)


def create_boto_client(settings: FinetuneSettings) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )


async def ensure_buckets(settings: FinetuneSettings) -> None:
    """Ensure required buckets exist in MinIO/S3."""
    def _ensure():
        client = create_boto_client(settings)
        existing_buckets = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
        for bucket in (settings.s3_datasets_bucket, settings.s3_models_bucket, settings.s3_checkpoints_bucket):
            if bucket not in existing_buckets:
                try:
                    client.create_bucket(Bucket=bucket)
                    logger.info("s3_bucket_created", bucket=bucket)
                except ClientError as e:
                    logger.warning("s3_bucket_create_failed", bucket=bucket, error=str(e))

    await asyncio.to_thread(_ensure)


async def upload_bytes(
    settings: FinetuneSettings,
    bucket: str,
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload byte buffer to S3/MinIO and return the s3:// URI."""
    def _upload():
        client = create_boto_client(settings)
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return f"s3://{bucket}/{key}"

    return await asyncio.to_thread(_upload)
