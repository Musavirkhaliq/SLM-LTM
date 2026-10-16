from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from slm_common.auth import Principal, get_principal
from slm_common.rbac import Role, require_role

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


class MetricPoint(BaseModel):
    t: int
    requests_per_min: float = 0.0
    tokens_per_min: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    gpu_util: float = 0.0
    gpu_memory_util: float = 0.0
    throughput_tps: float = 0.0
    error_rate: float = 0.0


class GpuStat(BaseModel):
    id: str = "gpu-0"
    name: str = "NVIDIA GB10"
    node: str = "spark-7b25"
    util: float = 0.0
    memory_used_gb: float = 0.0
    memory_total_gb: float = 120.0
    temperature_c: float = 38.0


class DeploymentMetric(BaseModel):
    deployment_id: str
    name: str
    status: str
    requests_per_min: float = 0.0
    p95_latency_ms: float = 0.0
    error_rate: float = 0.0
    gpu_util: float = 0.0


class ModelUsage(BaseModel):
    model: str
    requests: int = 0
    tokens: int = 0


class AlertItem(BaseModel):
    id: str
    severity: str
    title: str
    detail: str
    resource: str
    fired_at: str


class MonitoringOverview(BaseModel):
    window: str
    points: list[MetricPoint] = []
    gpus: list[GpuStat] = []
    deployments: list[DeploymentMetric] = []
    usage_by_model: list[ModelUsage] = []
    alerts: list[AlertItem] = []


@router.get("/overview", response_model=MonitoringOverview)
async def get_overview(
    window: str = Query("1h"),
    _: Principal = require_role(Role.viewer),
) -> MonitoringOverview:
    now_ms = int(time.time() * 1000)
    point = MetricPoint(
        t=now_ms,
        requests_per_min=0.0,
        tokens_per_min=0.0,
        latency_p50_ms=0.0,
        latency_p95_ms=0.0,
        gpu_util=0.0,
        gpu_memory_util=0.0,
        throughput_tps=0.0,
        error_rate=0.0,
    )
    return MonitoringOverview(
        window=window,
        points=[point],
        gpus=[GpuStat()],
        deployments=[],
        usage_by_model=[],
        alerts=[],
    )


@router.get("/deployments/{deployment_id}")
async def get_deployment_metrics(
    deployment_id: UUID,
    _: Principal = require_role(Role.viewer),
) -> dict[str, Any]:
    return {
        "deployment_id": str(deployment_id),
        "requests_per_min": 0.0,
        "p95_latency_ms": 0.0,
        "error_rate": 0.0,
        "gpu_util": 0.0,
    }


@router.websocket("/stream")
async def monitoring_stream(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_text()
            await ws.send_text(f"echo:{msg}")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
