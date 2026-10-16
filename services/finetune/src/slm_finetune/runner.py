from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from uuid import UUID

import httpx
import structlog

from slm_finetune.config import FinetuneSettings

logger = structlog.get_logger(__name__)


class DockerTrainerRunner:
    """Manages training jobs executed as Docker containers on DGX / local Docker."""

    def __init__(self, settings: FinetuneSettings) -> None:
        self.settings = settings
        self.socket_path = settings.docker_socket

    def has_docker(self) -> bool:
        return os.path.exists(self.socket_path)

    async def _docker_client(self) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(uds=self.socket_path)
        return httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=30.0)

    async def launch(
        self,
        job_id: UUID,
        org_id: UUID,
        job_name: str,
        method: str,
        base_model_uri: str,
        dataset_uri: str,
        dataset_format: str,
        hyperparams: dict[str, Any],
    ) -> str:
        """Launch a trainer container via Docker socket. Returns the container ID."""
        spec = {
            "job_id": str(job_id),
            "org_id": str(org_id),
            "name": job_name,
            "method": method,
            "base_model": {"uri": base_model_uri},
            "dataset": {
                "uri": dataset_uri,
                "format": dataset_format,
            },
            "hyperparams": hyperparams,
            "output": {
                "artifact_uri": f"s3://{self.settings.s3_models_bucket}/{org_id}/{job_id}/adapter",
                "checkpoint_uri": f"s3://{self.settings.s3_checkpoints_bucket}/{org_id}/{job_id}/",
            },
            "mlflow": {
                "tracking_uri": self.settings.mlflow_uri,
                "experiment": f"slm/{org_id}/finetune",
                "run_name": job_name,
            },
            "report": {
                "base_url": self.settings.training_api_url,
                "metrics_path": "/internal/v1/training-jobs/{job_id}/metrics",
                "status_path": "/internal/v1/training-jobs/{job_id}/status",
            },
            "s3": {
                "endpoint_url": self.settings.s3_endpoint,
                "region": self.settings.s3_region,
            },
        }

        env = [
            f"SLM_JOB_SPEC={json.dumps(spec)}",
            f"SLM_S3_ENDPOINT={self.settings.s3_endpoint}",
            f"SLM_S3_ACCESS_KEY={self.settings.s3_access_key}",
            f"SLM_S3_SECRET_KEY={self.settings.s3_secret_key}",
            f"SLM_MLFLOW_URI={self.settings.mlflow_uri}",
            f"SLM_TRAINING_API_URL={self.settings.training_api_url}",
            f"SLM_TRAINER_TOKEN={self.settings.trainer_token}",
        ]
        if self.settings.hf_token:
            env.append(f"HF_TOKEN={self.settings.hf_token}")

        env.append("HF_HOME=/work/.cache/huggingface")

        container_name = f"slm-trainer-{str(job_id)[:8]}"

        if not self.has_docker():
            logger.warning("docker_socket_missing_mocking", socket=self.socket_path)
            # In test environments or when docker socket isn't mounted, return a mock ID
            return f"mock-container-{job_id}"

        async with await self._docker_client() as client:
            # 1. Create container
            create_payload = {
                "Image": self.settings.trainer_image,
                "Env": env,
                "HostConfig": {
                    "NetworkMode": self.settings.docker_network,
                    "IpcMode": "host",
                    "Binds": ["slm-studio_hfcache:/work/.cache/huggingface:rw"],
                    "DeviceRequests": [
                        {
                            "Driver": "",
                            "Count": -1,
                            "Capabilities": [["gpu"]],
                        }
                    ],
                },
            }
            res = await client.post(f"/containers/create?name={container_name}", json=create_payload)
            if res.status_code == 409:
                # Remove existing container if any
                await client.delete(f"/containers/{container_name}?force=true")
                res = await client.post(f"/containers/create?name={container_name}", json=create_payload)

            if res.status_code >= 400:
                logger.error("docker_create_failed", status=res.status_code, body=res.text)
                raise RuntimeError(f"Failed to create trainer container: {res.text}")

            container_id = res.json().get("Id")

            # 2. Start container
            start_res = await client.post(f"/containers/{container_id}/start")
            if start_res.status_code >= 400:
                logger.error("docker_start_failed", status=start_res.status_code, body=start_res.text)
                raise RuntimeError(f"Failed to start trainer container: {start_res.text}")

            logger.info("trainer_container_started", container_id=container_id, job_id=str(job_id))
            return container_id

    async def cancel(self, container_id_or_name: str) -> None:
        """Stop and remove a running trainer container."""
        if not self.has_docker() or container_id_or_name.startswith("mock-"):
            logger.info("trainer_cancel_mock", container=container_id_or_name)
            return

        async with await self._docker_client() as client:
            try:
                await client.post(f"/containers/{container_id_or_name}/stop?t=5")
                logger.info("trainer_container_stopped", container=container_id_or_name)
            except Exception as e:
                logger.warning("trainer_stop_failed", container=container_id_or_name, error=str(e))
