from __future__ import annotations

from functools import lru_cache

from slm_common.config import BaseServiceSettings


class GatewaySettings(BaseServiceSettings):
    # Control-plane upstreams for the /api/v1/* reverse proxy.
    auth_url: str = "http://localhost:8001"
    registry_url: str = "http://localhost:8002"
    finetune_url: str = "http://localhost:8003"
    rag_url: str = "http://localhost:8004"
    evaluation_url: str = "http://localhost:8005"
    deployment_url: str = "http://localhost:8006"
    monitoring_url: str = "http://localhost:8007"
    cost_url: str = "http://localhost:8008"
    trust_forwarded_for: bool = False  # honour a client-supplied X-Forwarded-For (behind a trusted LB)

    # Inference path.
    api_key_cache_ttl_seconds: int = 60
    api_key_negative_cache_seconds: int = 5
    route_cache_ttl_seconds: int = 5
    upstream_connect_timeout: float = 5.0
    upstream_read_timeout: float = 300.0
    max_request_bytes: int = 4 * 1024 * 1024
    default_completion_estimate: int = 256  # TPM admission estimate when max_tokens is absent

    # Usage logging.
    usage_batch_size: int = 200
    usage_flush_interval_seconds: float = 1.0
    usage_queue_size: int = 50_000

    def service_map(self) -> dict[str, str]:
        """URL prefix -> upstream base URL (longest prefix wins in the proxy)."""
        return {
            "/api/v1/auth": self.auth_url,
            "/api/v1/me": self.auth_url,
            "/api/v1/orgs": self.auth_url,
            "/api/v1/projects": self.auth_url,
            "/api/v1/api-keys": self.auth_url,
            "/api/v1/audit-logs": self.auth_url,
            "/api/v1/models": self.registry_url,
            "/api/v1/datasets": self.finetune_url,
            "/api/v1/training-jobs": self.finetune_url,
            "/api/v1/rag": self.rag_url,
            "/api/v1/evaluations": self.evaluation_url,
            "/api/v1/deployments": self.deployment_url,
            "/api/v1/endpoints": self.deployment_url,
            "/api/v1/monitoring": self.monitoring_url,
            "/api/v1/cost": self.cost_url,
        }


@lru_cache
def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()
