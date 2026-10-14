"""Turns a deployment's `vllm_args` (see CONTRACT.md section 6) into a `vllm serve` command line."""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LoraModule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    path: str = Field(min_length=1)


class SpeculativeConfig(BaseModel):
    """Speculative decoding: a draft model, or model-free n-gram prompt lookup."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["ngram", "draft_model", "eagle", "eagle3", "medusa"] | None = None
    model: str | None = Field(default=None, description="Draft model repo or path")
    num_speculative_tokens: int = Field(ge=1, le=32)
    prompt_lookup_max: int | None = Field(default=None, ge=1)
    prompt_lookup_min: int | None = Field(default=None, ge=1)
    draft_tensor_parallel_size: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _needs_a_drafter(self) -> Self:
        if self.method is None and self.model is None:
            raise ValueError("speculative_config needs a method or a draft model")
        if self.method == "ngram" and self.model is not None:
            raise ValueError("the ngram method does not use a draft model")
        if self.method in {"draft_model", "eagle", "eagle3", "medusa"} and self.model is None:
            raise ValueError(f"method '{self.method}' requires a draft model")
        if (
            self.prompt_lookup_min is not None
            and self.prompt_lookup_max is not None
            and self.prompt_lookup_min > self.prompt_lookup_max
        ):
            raise ValueError("prompt_lookup_min must be <= prompt_lookup_max")
        return self


class VllmArgs(BaseModel):
    """Typed form of `deployments.vllm_args`; unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    served_model_name: str | None = Field(default=None, min_length=1, max_length=128)
    max_model_len: int | None = Field(default=None, ge=128)
    gpu_memory_utilization: float | None = Field(default=None, gt=0, le=1)
    enable_prefix_caching: bool | None = Field(
        default=None, description="Reuse KV cache across requests sharing a prompt prefix"
    )
    speculative_config: SpeculativeConfig | None = None
    max_num_seqs: int | None = Field(default=None, ge=1)
    max_num_batched_tokens: int | None = Field(default=None, ge=1)
    quantization: Literal["awq", "gptq", "fp8", "bitsandbytes", "gguf"] | None = None
    dtype: Literal["auto", "float16", "bfloat16", "float32"] | None = None
    tensor_parallel_size: int | None = Field(default=None, ge=1)
    enable_lora: bool | None = None
    lora_modules: list[LoraModule] = Field(default_factory=list)
    max_loras: int | None = Field(default=None, ge=1)
    max_lora_rank: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _lora_consistency(self) -> Self:
        if self.lora_modules and not self.enable_lora:
            raise ValueError("lora_modules requires enable_lora=true")
        names = [m.name for m in self.lora_modules]
        if len(names) != len(set(names)):
            raise ValueError("lora module names must be unique")
        if (self.max_loras or self.max_lora_rank) and not self.enable_lora:
            raise ValueError("max_loras / max_lora_rank require enable_lora=true")
        return self


def build_vllm_command(
    model: str,
    args: VllmArgs | Mapping[str, Any] | None = None,
    *,
    gpu_count: int = 1,
    host: str = "0.0.0.0",  # noqa: S104 - bound inside the pod; reachability is a network-policy concern
    port: int = 8000,
    served_model_name: str | None = None,
) -> list[str]:
    """`vllm serve` argv for `model` (an HF repo id or a local/mounted path).

    Optional settings are only emitted when set, so vLLM defaults apply otherwise.
    `tensor_parallel_size` defaults to `gpu_count` when more than one GPU is requested.
    """
    cfg = args if isinstance(args, VllmArgs) else VllmArgs.model_validate(dict(args or {}))
    if not model.strip():
        raise ValueError("model must not be empty")
    cmd = ["vllm", "serve", model, "--host", host, "--port", str(port)]

    name = served_model_name or cfg.served_model_name
    if name:
        cmd += ["--served-model-name", name]

    tensor_parallel = cfg.tensor_parallel_size or (gpu_count if gpu_count > 1 else None)
    scalar_flags: list[tuple[str, Any]] = [
        ("--tensor-parallel-size", tensor_parallel),
        ("--max-model-len", cfg.max_model_len),
        ("--gpu-memory-utilization", cfg.gpu_memory_utilization),
        ("--max-num-seqs", cfg.max_num_seqs),
        ("--max-num-batched-tokens", cfg.max_num_batched_tokens),
        ("--quantization", cfg.quantization),
        ("--dtype", cfg.dtype),
    ]
    for flag, value in scalar_flags:
        if value is not None:
            cmd += [flag, str(value)]

    if cfg.enable_prefix_caching is True:
        cmd.append("--enable-prefix-caching")
    elif cfg.enable_prefix_caching is False:
        cmd.append("--no-enable-prefix-caching")  # vLLM V1 enables it by default

    if cfg.speculative_config is not None:
        spec = cfg.speculative_config.model_dump(exclude_none=True)
        cmd += ["--speculative-config", json.dumps(spec, sort_keys=True, separators=(",", ":"))]

    if cfg.enable_lora:
        cmd.append("--enable-lora")
        if cfg.max_loras:
            cmd += ["--max-loras", str(cfg.max_loras)]
        if cfg.max_lora_rank:
            cmd += ["--max-lora-rank", str(cfg.max_lora_rank)]
        if cfg.lora_modules:
            cmd += ["--lora-modules", *(f"{m.name}={m.path}" for m in cfg.lora_modules)]
    return cmd


def to_shell(cmd: list[str]) -> str:
    """Quoted single-line form for logs and docs (containers should exec the argv list)."""
    return shlex.join(cmd)
