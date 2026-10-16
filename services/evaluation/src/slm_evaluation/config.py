from __future__ import annotations

from functools import lru_cache

from slm_common.config import BaseServiceSettings


class EvaluationSettings(BaseServiceSettings):
    """Settings specific to the evaluation service."""

    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "slmadmin"
    s3_secret_key: str = "slmadmin"
    s3_datasets_bucket: str = "datasets"

    gateway_url: str = "http://gateway:8000"
    vllm_url: str = "http://vllm:8000"
    judge_url: str = "http://vllm:8000"
    mlflow_uri: str = "http://mlflow:5000"


@lru_cache
def get_evaluation_settings() -> EvaluationSettings:
    return EvaluationSettings()
