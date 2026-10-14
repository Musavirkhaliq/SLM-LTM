# Container images

Build context is always the **repository root**.

| Image | Dockerfile | Notes |
|---|---|---|
| `gateway auth registry finetune rag evaluation deployment monitoring cost` | `Dockerfile.service` | one file, `--build-arg SERVICE=<name> --build-arg PORT=<port>` (ports in CONTRACT.md); uv workspace, non-root uid 10001, `HEALTHCHECK` on `/healthz` |
| `web` | `Dockerfile.web` | Next.js `output: "standalone"`; needs `package-lock.json`; read-only rootfs compatible (mount emptyDir on `/app/.next/cache`) |
| `trainer` | `Dockerfile.trainer` | CUDA 12.8 base, Unsloth + PEFT + TRL; `train.py` reads the JSON job spec from `SLM_JOB_SPEC` |
| (dev only) mlflow | `Dockerfile.mlflow` | compose only |

```bash
docker build -f deploy/docker/Dockerfile.service --build-arg SERVICE=gateway --build-arg PORT=8000 -t slm-studio/gateway:dev .
docker build -f deploy/docker/Dockerfile.web     -t slm-studio/web:dev .
docker build -f deploy/docker/Dockerfile.trainer -t slm-studio/trainer:dev .
```

## rag-worker

There is no separate `rag-worker` image. Document ingestion (parse, chunk, embed, upsert to Qdrant) runs in the
**`rag` image** with a different command, so API and workers share code and versioning:

```yaml
# Helm: services.ragWorker in values.yaml (disabled by default)
image: <registry>/<prefix>/rag:<tag>
command: ["python", "-m", "slm_rag.worker"]   # assumed entrypoint; adjust to the module the rag service ships
```

Scale workers independently of the API (CPU/memory heavy, no inbound traffic, so no Service/Ingress/NetworkPolicy ingress).
If embedding runs on GPU, build a CUDA variant by reusing `Dockerfile.trainer`'s base and installing `slm-rag`.

## Base-image pinning

Tags are pinned to exact versions through `ARG`s at the top of each Dockerfile. For immutable builds append
`@sha256:<digest>`; Dependabot (`.github/dependabot.yml`, docker ecosystem) proposes tag+digest bumps.
Trivy scans every pushed image in `build-push.yml`.

## Trainer on DGX Spark

DGX Spark is aarch64 with a Blackwell GB10 GPU. The default `nvidia/cuda:12.8.1` base plus cu128 PyTorch wheels is
the best-effort path; if a wheel is unavailable for your driver/arch, rebuild on NVIDIA's NGC PyTorch container:
`--build-arg BASE_IMAGE=nvcr.io/nvidia/pytorch:25.09-py3 --build-arg USE_SYSTEM_TORCH=1`.
Neither path has been built or run from this repository's CI-less authoring environment.
