from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from slm_deployment.vllm import VllmArgs

DeployTarget = Literal["dgx_spark", "kubernetes", "azure", "aws", "on_prem"]
EndpointKind = Literal["private", "shared", "dedicated"]
DeployStrategy = Literal["direct", "blue_green", "canary"]
DeployStatus = Literal["pending", "provisioning", "running", "stopped", "failed", "rolling_back"]


class DeploymentCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    model_version_id: UUID
    project_id: UUID | None = None
    kind: EndpointKind = "private"
    target: DeployTarget = "dgx_spark"
    strategy: DeployStrategy = "direct"
    replicas_min: int = Field(default=1, ge=0, le=64)
    replicas_max: int = Field(default=1, ge=1, le=64)
    gpu_count: int = Field(default=1, ge=1, le=16)
    vllm_args: VllmArgs = Field(default_factory=VllmArgs)
    internal_url: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Non-Kubernetes targets (dgx_spark/on_prem/azure/aws) have no built-in orchestrator "
            "yet: point this at a vLLM server you started yourself (e.g. the compose "
            "`vllm`/`vllm-stub` service) so 'start' has something to mark running. Kubernetes "
            "targets compute this."
        ),
    )

    @model_validator(mode="after")
    def _replica_bounds(self) -> Self:
        if self.replicas_min > self.replicas_max:
            raise ValueError("replicas_min must be <= replicas_max")
        if self.target == "kubernetes" and self.internal_url is not None:
            raise ValueError("internal_url is computed by the Kubernetes orchestrator, not settable")  # noqa: E501
        return self


class DeploymentPatch(BaseModel):
    vllm_args: VllmArgs | None = None
    strategy: DeployStrategy | None = None
    internal_url: str | None = Field(default=None, max_length=512)


class DeploymentScale(BaseModel):
    replicas_min: int = Field(ge=0, le=64)
    replicas_max: int = Field(ge=1, le=64)

    @model_validator(mode="after")
    def _replica_bounds(self) -> Self:
        if self.replicas_min > self.replicas_max:
            raise ValueError("replicas_min must be <= replicas_max")
        return self


class DeploymentRollback(BaseModel):
    target_deployment_id: UUID | None = Field(
        default=None, description="Defaults to the deployment's previous_deployment_id"
    )


class DeploymentOut(BaseModel):
    id: UUID
    org_id: UUID
    project_id: UUID | None
    name: str
    model_version_id: UUID
    kind: EndpointKind
    target: DeployTarget
    strategy: DeployStrategy
    status: DeployStatus
    replicas_min: int
    replicas_max: int
    gpu_count: int
    vllm_args: dict
    internal_url: str | None
    previous_deployment_id: UUID | None
    created_at: datetime
    updated_at: datetime


class EndpointCreate(BaseModel):
    alias: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    kind: EndpointKind = "private"
    enabled: bool = True


class RouteIn(BaseModel):
    deployment_id: UUID
    weight: int = Field(ge=0, le=100)
    label: str | None = Field(default=None, max_length=64)


class RoutesPut(BaseModel):
    routes: list[RouteIn] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def _weights(self) -> Self:
        if sum(r.weight for r in self.routes) != 100:
            raise ValueError("route weights must sum to 100")
        ids = [r.deployment_id for r in self.routes]
        if len(ids) != len(set(ids)):
            raise ValueError("each deployment may appear only once per endpoint")
        return self


class EndpointOut(BaseModel):
    id: UUID
    org_id: UUID
    alias: str
    kind: EndpointKind
    enabled: bool
    routes: list[RouteIn] = Field(default_factory=list)
    created_at: datetime
