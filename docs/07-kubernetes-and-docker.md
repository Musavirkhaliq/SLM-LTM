# 07 - Kubernetes and Docker

This document explains **how the artifacts under `deploy/` are organised and why**. It deliberately does not reproduce them: the files are the source of truth, and
where this page and a file disagree the file wins. Status: the Dockerfiles, Helm chart, raw manifests, Grafana assets and smoke script exist in the repository
(reviewed 2026-10-14) but have **not been run against a real cluster or GPU by the authors of this documentation**; the chart's own README text says the same about
the trainer image on DGX Spark. The compose stack is still being assembled (only `deploy/compose/vllm-stub/` is present).

## 1. Three kinds of Kubernetes workloads

| | Platform services | Platform-managed vLLM model servers | Runtime workloads created by services |
|---|---|---|---|
| What | gateway, auth, registry, finetune, rag, evaluation, deployment, monitoring, cost, web; OTel collector; migration hook Job | One vLLM `StatefulSet` + `Service` (+ KEDA `ScaledObject`) per entry in `vllm.models` of the chart (e.g. `phi-4-mini`) | Tenant vLLM servers created by the `deployment` service (`deployments` rows); trainer `Job`s created by the `finetune` service (`training_jobs` rows) |
| Created by | `helm upgrade` | `helm upgrade` | API calls at runtime |
| Source of truth | Git (values files) | Git (values files) | PostgreSQL rows, reconciled into the cluster. **Both creating services are typed stubs today, so this column is the target design** |
| Discovery labels | `app.kubernetes.io/*` | `slm-studio.io/scrape: vllm`, `slm-studio.io/deployment: <name>`, `app.kubernetes.io/part-of: slm-studio` | same labels, so Prometheus scrapes them and dashboards can filter by deployment |
| Cluster rights | one ServiceAccount per service, `automountServiceAccountToken: false` unless needed | ServiceAccount per server | `deploymentController.rbac.enabled` creates a namespaced Role letting the deployment service manage vLLM StatefulSets/Services/PVCs; `rbac.yaml` creates a `trainer-launcher` Role (jobs: create/get/list/watch/delete/patch; pods and events read; pod logs get) for `finetune` |

Consequences:

- The chart does **not** render tenant deployments. It grants the `deployment` and `finetune` ServiceAccounts namespaced Roles, and those services create workloads. `helm uninstall` does not remove them; decommissioning needs a label-selector cleanup (`-l app.kubernetes.io/part-of=slm-studio` plus the deployment label).
- **Namespace layout.** The chart installs into the release namespace (the raw manifests use `slm-studio`) and `trainer.namespace` can move training Jobs elsewhere. The multi-namespace layout in [06](06-infrastructure.md) (`slm-control`, `slm-data`, `slm-serving`, `slm-train`) is the **recommended hardened topology**, not the chart default; adopt it by installing dependencies in their own namespaces (the chart's defaults already assume external `storage`, `mlops`, `vectordb`, `monitoring` namespaces) and by setting `trainer.namespace`. A single namespace is acceptable for T1.
- Postgres being the desired-state record, with a reconcile loop that repairs drift, is a property the `deployment` service has to implement; it is not in the chart.

## 2. `deploy/` layout and purpose

| Path | Purpose |
|---|---|
| `deploy/docker/` | `Dockerfile.service` (one file for the nine Python services, `--build-arg SERVICE=<name> --build-arg PORT=<port>`), `Dockerfile.web` (Next.js standalone), `Dockerfile.trainer` (CUDA + Unsloth/PEFT/TRL, `train.py` reads `SLM_JOB_SPEC`), `Dockerfile.mlflow` (dev/compose only), `healthcheck.py`, `.hadolint.yaml`, `README.md` (build commands, base-image pinning, Spark notes) |
| `deploy/compose/` | Compose assets; currently `vllm-stub/server.py`, an OpenAI-compatible stub so the gateway can be exercised without a GPU. Full stack: in progress |
| `deploy/helm/slm-studio/` | Umbrella chart: `values.yaml` (defaults), overlays `values-dgx-spark.yaml` and `values-ha.yaml`, `ci/values-dev.yaml` and `ci/values-full.yaml` (used by CI lint/template), templates (below), `dashboards/` (Grafana JSON copies), `files/postgres-init.sh` |
| `deploy/helm/environments/` | `staging.yaml`, `production.yaml` environment overlays used by the deploy workflow |
| `deploy/k8s/` | Plain reference manifests for teams not using Helm (`00-namespace.yaml` with Pod Security `restricted`, `05-networkpolicy.yaml`, `10-vllm-deployment.yaml`, `20-vllm-canary.yaml`, README with the canary/blue-green procedure). Documented as **not kept in lock-step with the chart** |
| `deploy/grafana/` | `dashboards/` (`slm-platform-overview.json`, `slm-deployment.json`), `alert-rules.yml`, `prometheus.yml`, `otel-collector.yaml`, `provisioning/` |
| `deploy/scripts/` | `smoke-test.sh` (`/readyz`, `/v1/models`, optional chat call, with retries), `sync-dashboards.sh` (keeps `deploy/helm/.../dashboards/` equal to `deploy/grafana/dashboards/`) |

## 3. Images

| Image | Dockerfile | Notes |
|---|---|---|
| `gateway auth registry finetune rag evaluation deployment monitoring cost` | `Dockerfile.service` | uv workspace, non-root UID 10001, `HEALTHCHECK` on `/healthz`; every image also ships `db/migrate.py` (the migration hook runs it from the `auth` image) |
| `web` and `web-staging` | `Dockerfile.web` | `NEXT_PUBLIC_*` values are baked at **build** time (`NEXT_PUBLIC_API_BASE`, `NEXT_PUBLIC_USE_MOCKS=false`), which is why staging and production have separate web images |
| `trainer` | `Dockerfile.trainer` | Default base is a CUDA 12.8 image with cu128 PyTorch wheels (best effort for GB10); fallback for DGX Spark is NVIDIA's NGC PyTorch container via build args (`BASE_IMAGE=nvcr.io/nvidia/pytorch:25.09-py3`, `USE_SYSTEM_TORCH=1`) |
| vLLM | not built here | `vllm/vllm-openai` pinned in `values.yaml` for x86; `values-dgx-spark.yaml` uses NVIDIA's NGC vLLM container for aarch64/GB10. Verify the tags you pull |
| `rag-worker` | none | Ingestion runs in the `rag` image with a different command (`services.ragWorker`, disabled by default) |

Build and tagging: `docker buildx` multi-arch (`linux/amd64,linux/arm64`), images pushed to GHCR as `ghcr.io/<owner>/slm-studio/<name>:<tag>`; environments deploy by tag `sha-<40 hex>` or semver, with cosign signature verification before deploy ([08](08-cicd.md)).
Base images are pinned through `ARG`s at the top of each Dockerfile, with Dependabot proposing bumps; `@sha256` digest pinning is optional per the README.
Arm64 builds currently run under QEMU emulation in CI (workflow comment: move to a native arm64 runner if the trainer exceeds its timeout); the trainer's arm64 variant has never been validated on real GB10 hardware.

## 4. Helm chart organisation

Templates (all under `deploy/helm/slm-studio/templates/`): `control-plane.yaml` (Deployments/Services for the nine services plus web, rendered from the `services` map), `vllm.yaml` (StatefulSets, Services, KEDA ScaledObjects), `ingress.yaml`, `configmap.yaml`, `secrets.yaml`, `rbac.yaml`, `networkpolicy.yaml`, `hpa.yaml`, `pdb.yaml`, `migration-job.yaml`, `servicemonitor.yaml`, `prometheusrule.yaml`, `grafana-dashboards.yaml`, `otel-collector.yaml`, `bundled-dev.yaml` (dev-only Postgres/Redis), `_helpers.tpl`, `NOTES.txt`.

Design decisions and what they imply:

| Decision | Detail | Note |
|---|---|---|
| External dependencies by default | Postgres, Redis, S3 (MinIO/AWS), MLflow and Qdrant are external; `postgresql.bundled` and `redis.bundled` exist for dev/demo only | Matches the recommendation not to bundle operators in the app chart ([09](09-deployment-guide.md) installs them separately) |
| Config vs secrets | Non-secret `SLM_*` in a ConfigMap (`envFrom`): `SLM_ENV`, `SLM_LOG_LEVEL`, `SLM_JWT_ALGORITHM` (RS256), `SLM_S3_ENDPOINT`, `SLM_S3_REGION`, `SLM_MLFLOW_URI`, `SLM_QDRANT_URL`, `SLM_OTLP_ENDPOINT`; computed by the chart: `SLM_<SERVICE>_URL`, `SLM_PUBLIC_BASE_URL`, `SLM_TRAINER_*`, `SLM_VLLM_*` | `SLM_ENV: prod` enables the insecure-default guards in `slm_common` |
| One Secret, keys = env var names | `SLM_DATABASE_URL` (role `slm_app`, **not** the owner), `SLM_REDIS_URL`, `SLM_JWT_PRIVATE_KEY`, `SLM_JWT_PUBLIC_KEY`, `SLM_S3_ACCESS_KEY`, `SLM_S3_SECRET_KEY`, `SLM_TRAINER_TOKEN`; optional `SLM_HF_TOKEN`, `SLM_WANDB_API_KEY`, `SLM_OAUTH__{AZURE_AD,GOOGLE,GITHUB}__CLIENT_SECRET`. Source precedence: `existingSecret`, then `externalSecret` (External Secrets Operator, default), then values (dev only) | Each service mounts only the keys it lists (`secretEnv`), e.g. the gateway gets `SLM_DATABASE_URL`, `SLM_REDIS_URL`, `SLM_JWT_PUBLIC_KEY` only |
| Migration hook | `migration-job.yaml`: pre-install/pre-upgrade Job running `python /app/db/migrate.py` with `SLM_MIGRATE_DATABASE_URL` (owner/admin DSN) and optional `SLM_APP_PASSWORD`, from which `migrate.py` creates or refreshes the `slm_app` role and re-applies grants. A failed migration fails the release | Migrations must be expand/contract-compatible because old pods keep serving during the Job. Note a naming inconsistency inside the chart: `values.yaml` and `migration-job.yaml` use the key `SLM_MIGRATE_DATABASE_URL`, while the comment block of `values-dgx-spark.yaml` shows `SLM_DATABASE_URL` for the migrate secret: use the former |
| Service defaults | 2 replicas, `maxUnavailable: 0`, `preStopSleepSeconds: 10`, `terminationGracePeriodSeconds: 60` (gateway 120 to drain SSE), PDB `maxUnavailable: 1`, read-only root filesystem with `/tmp` emptyDir, probes: startup/liveness `/healthz`, readiness `/readyz`; gateway 3 replicas with HPA 3-20 on CPU (optional RPS metric via prometheus-adapter) | Pod Security `restricted`-compatible security contexts |
| Gateway behind ingress | `SLM_TRUST_FORWARDED_FOR: "true"` so the client IP is taken from ingress-nginx's `X-Forwarded-For`; NetworkPolicy blocks direct access | See the audit-helper caveat in [02](02-database-schema.md) section 5 |
| Ingress | ingress-nginx + cert-manager `ClusterIssuer`; API host with `proxy-buffering: off`, `proxy-request-buffering: off`, read/send timeouts 3600 s, body size 512 MB; app host body size 16 MB | Matches the SSE/WebSocket requirements in [06](06-infrastructure.md) |
| NetworkPolicy | Default deny with allows; `allowIntraControlPlane: true`; `externalEgress` rules for Postgres (5432), Redis (6379), HTTPS/S3 (443, 9000), MLflow/Qdrant | **With `to: []` each rule allows the port to any destination**: tighten with `ipBlock`/`namespaceSelector` in production values (the chart says so) |
| vLLM | `vllm.models` map; `modelDefaults.args` use camelCase keys (`maxModelLen`, `gpuMemoryUtilization` default 0.90, `enablePrefixCaching`, `maxNumSeqs`, `maxNumBatchedTokens`, `tensorParallelSize`, `speculativeConfig`, `quantization`, `enableLora`, `loraModules`, `extraArgs`) rendered into the vLLM flags in `vllm.yaml`; `/dev/shm` sized by `shmSize`; per-replica PVC cache (or one RWX claim); S3 init container for `s3://` models; startup probe up to ~30 min on `/health`; optional KEDA scaling on queue depth | vLLM pinned to `v0.10.2` for x86. Metric names differ across vLLM versions: the chart's alert rules use `vllm:gpu_cache_usage_perc or vllm:kv_cache_usage_perc` for that reason ([12](12-monitoring-design.md)) |
| Trainer | `trainer.*`: GPU node selector and tolerations, `ttlSecondsAfterFinished: 86400`, `backoffLimit: 2`, `activeDeadlineSeconds: 172800` (48 h hard stop) | `backoffLimit: 2` means the Job controller retries a failed training run twice; decide whether automatic retries of a costly GPU job are wanted ([14](14-roadmap.md) OQ list) |
| Observability | ServiceMonitor (15 s), PrometheusRule with thresholds (error ratio 0.05, gateway p95 30 s, TTFT p95 2 s, queue 20, KV cache 0.95, GPU util/memory 95%), Grafana dashboards via sidecar label, OTel collector (2 replicas) | The collector's default pipelines export **traces to a `debug` exporter only**: configure an exporter (Tempo) or traces are not stored |

Overlays:

| File | Purpose |
|---|---|
| `values-dgx-spark.yaml` | One replica per service, no anti-affinity or spread, `existingSecret` expected, NGC vLLM image on arm64, `local-path` cache, `gpuMemoryUtilization: 0.35` per model so two models can share the unified memory through device-plugin time-slicing (keep the sum of utilisations below ~0.85), k3s/Traefik note (install ingress-nginx or set `className: traefik`) |
| `values-ha.yaml` | Zone `topologySpreadConstraints` with `DoNotSchedule`, 3 replicas default and gateway 4-30 with HPA and the RPS metric; requires prometheus-adapter, KEDA, External Secrets Operator and external HA Postgres/Redis |
| `environments/staging.yaml`, `environments/production.yaml` | Image registry/tag, hosts, issuer, secret references per environment |

## 5. How the chart maps to the API's `vllm_args`

The API field `deployments.vllm_args` (snake_case, [CONTRACT.md](../CONTRACT.md) section 6) and the chart's `vllm.models.<m>.args` (camelCase) describe the same flags:

| `vllm_args` key | Chart key | vLLM flag | Notes |
|---|---|---|---|
| `max_model_len` | `maxModelLen` | `--max-model-len` | Also bounds prompt plus completion |
| `gpu_memory_utilization` | `gpuMemoryUtilization` | `--gpu-memory-utilization` | Spark overlay 0.35 per model; <= 0.90 on dedicated H100 |
| `enable_prefix_caching` | `enablePrefixCaching` | `--enable-prefix-caching` | Set explicitly: defaults changed across vLLM versions |
| `speculative_config` | `speculativeConfig` | `--speculative-config '<json>'` | Older versions used separate flags: validate against the pinned version |
| `max_num_seqs` | `maxNumSeqs` | `--max-num-seqs` | Batch size bound |
| `max_num_batched_tokens` | `maxNumBatchedTokens` | `--max-num-batched-tokens` | Prefill chunk budget |
| `quantization` | `quantization` | `--quantization` | Must match the artifact |
| `enable_lora`, `lora_modules` | `enableLora`, `loraModules` | `--enable-lora`, `--lora-modules name=path` | Chart also sets `--max-loras`, `--max-lora-rank` |

The deployment service must whitelist keys and build argument lists, never interpolate user strings into a command; the chart's template does the same for platform-managed models. Flag names evolve between vLLM releases, so the pinned image version, the flag table and a container-start smoke test belong together.

## 6. Raw manifests and traffic splitting

`deploy/k8s/README.md` documents the canary/blue-green procedure: **traffic is split by the SLM Studio gateway (weighted `endpoint_routes`), not by Kubernetes or the ingress**; two vLLM servers are registered as deployments with their `internal_url`, `PUT /api/v1/endpoints/{id}/routes` sets weights (sum 100), `X-SLM-Route` and `usage_logs.route_label` expose the split, and rollback is a weight change or `POST /api/v1/deployments/{id}/rollback`. That matches [01](01-architecture.md) section 6.4. The request body shape for the routes endpoint is defined in [`openapi/control-plane.yaml`](openapi/control-plane.yaml).

## 7. Compose

`deploy/compose/vllm-stub/server.py` provides an OpenAI-compatible stub (no GPU) so the gateway, auth, registry and UI can be exercised end to end; CI has a `docker compose config` job ready for the compose file. Compose is not a production orchestrator: no PDBs, no rolling updates, no network policy, and the `deployment` service's Kubernetes backend does not run under it.

## 8. Environments

| Environment | Cluster | Values | Data |
|---|---|---|---|
| dev | compose or kind/k3d, `ci/values-dev.yaml` | bundled Postgres/Redis | Synthetic |
| staging | Same shape as production, smaller | `values.yaml` + `environments/staging.yaml` | Synthetic or scrubbed; separate IdP app registrations |
| production | T1 (`values-dgx-spark.yaml`), T3 (`values-ha.yaml`) | `environments/production.yaml` | Real |

Promotion between environments is by image tag/digest and a values commit, see [08-cicd.md](08-cicd.md).
