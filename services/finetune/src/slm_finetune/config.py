from __future__ import annotations

from functools import lru_cache

from slm_common.config import BaseServiceSettings


class FinetuneSettings(BaseServiceSettings):
    """Settings specific to the finetune service."""

    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "slmadmin"
    s3_secret_key: str = "slmadmin"
    s3_datasets_bucket: str = "datasets"
    s3_models_bucket: str = "models"
    s3_checkpoints_bucket: str = "checkpoints"

    mlflow_uri: str = "http://mlflow:5000"
    training_api_url: str = "http://finetune:8003"
    trainer_token: str = "dev-trainer-token"
    hf_token: str | None = None

    trainer_image: str = "slm-studio/trainer:dev"
    docker_socket: str = "/var/run/docker.sock"
    docker_network: str = "slm-studio_default"


@lru_cache
def get_finetune_settings() -> FinetuneSettings:
    return FinetuneSettings()
