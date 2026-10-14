# 05 - Folder Structure

Top level and port/package names are fixed by [`CONTRACT.md`](../CONTRACT.md) section 1. Below that level, entries marked
*(convention)* are the intended layout and may differ in detail from what lands in the repo; the directory tree is the
authority for anything already committed.

## 1. Repository tree

```
slm-studio/
  CONTRACT.md               binding engineering contract
  README.md
  pyproject.toml            uv workspace root (members = services/*), ruff + pytest config
  uv.lock                   single lockfile for all Python services
  db/
    migrations/             0001_init.sql, 0002_*.sql ... plain SQL, applied in order
    migrate.py              applies migrations, records checksums (see 02, section 8)
  docs/                     these 15 deliverables + openapi/
    openapi/                inference.yaml, control-plane.yaml
  services/                 Python 3.11+, FastAPI, one package per service
    common/    slm_common      shared library (CONTRACT.md section 3)
    auth/      slm_auth        :8001
    registry/  slm_registry    :8002
    gateway/   slm_gateway     :8000  single public entrypoint
    finetune/  slm_finetune    :8003
    rag/       slm_rag         :8004
    evaluation/slm_evaluation  :8005
    deployment/slm_deployment  :8006
    monitoring/slm_monitoring  :8007
    cost/      slm_cost        :8008
  web/                      Next.js 15 App Router UI
  deploy/
    docker/                 Dockerfile.service / .web / .trainer / .mlflow, healthcheck, trainer entrypoint
    compose/                docker compose assets (vLLM stub; full stack in progress)
    helm/slm-studio/        umbrella Helm chart (+ helm/environments/ overlays)
    k8s/                    raw reference manifests
    grafana/                dashboards, alert rules, prometheus + otel configs, provisioning
    scripts/                smoke-test.sh, sync-dashboards.sh
  .github/workflows/        CI/CD
```

## 2. Python services *(convention)*

Each service is an independent uv workspace member with its own `pyproject.toml`, depending on `slm-common` via the
workspace source declared in the root `pyproject.toml`.

```
services/<name>/
  pyproject.toml            name = "slm-<name>", dependencies, entry points
  src/slm_<name>/
    __init__.py
    main.py                 create_app(): FastAPI app, lifespan (pool, redis), exception handlers, middleware
    config.py               Settings(BaseServiceSettings) + service-specific SLM_* variables
    api/                    routers (thin: parse, authorise with require_role, call service layer)
    schemas.py              pydantic v2 request/response models (mirrors openapi/)
    service.py              business logic; no SQL, no FastAPI types
    repo/                   repository modules: SQL strings + asyncpg calls, always inside tenant_tx
  tests/                    pytest + pytest-asyncio + httpx; DB tests use a real PostgreSQL (pgserver) so RLS is exercised
```

Rules that keep the architecture honest:

1. **No ORM, SQL lives in `repo/`.** Reviewers can see every query next to its RLS context.
2. **Every DB access goes through `slm_common.db.tenant_tx`** (or an explicit `bypass=True`, which must be justified in review; see [02](02-database-schema.md) section 3).
3. **Services never import each other.** Shared behaviour goes into `slm_common`; cross-service calls are HTTP through the documented APIs.
4. **Authorisation is declared on the route** with `require_role(Role.x)`; no inline role string comparisons.
5. **Every mutation calls `slm_common.audit.audit(...)`** in the same transaction as the change.
6. Error handling: `ApiError` raised in service code, mapped by `problem_json_handler` (control plane) or `openai_error_handler` (gateway `/v1/*`).
7. Config is environment-only (`SLM_` prefix, pydantic-settings); secrets arrive as env vars or file mounts, never defaults in code.
8. Every service exposes `/healthz`, `/readyz`, `/metrics`.

### 2.1 `services/common/` (`slm_common`)

Package `slm_common`, modules per CONTRACT.md section 3: `config`, `db`, `security`, `auth`, `rbac`, `errors`,
`logging`, `telemetry`, `audit`, `pagination`. Changing a public name here is a repo-wide change: update the contract first.

### 2.2 `services/gateway/` specifics *(convention)*

`proxy/` (SSE passthrough, upstream client pool), `ratelimit/` (Redis Lua sliding window), `routing/` (alias to routes, weighted choice, cache + invalidation subscriber), `usage/` (bounded queue and batch writer), `controlplane/` (prefix to upstream map for `/api/v1/*`).

### 2.3 Trainer (`slm-studio/trainer` image) *(Proposed, not built)*

Not a uv workspace service: its dependency set (PyTorch, Unsloth, PEFT, TRL, bitsandbytes) is GPU/arch specific and is
built from `deploy/docker/` with its own pinned requirements. It lives outside `services/` to keep the workspace lockfile
CPU-resolvable. Entrypoint contract: reads `SLM_JOB_SPEC` (JSON from the training job), pulls artifacts from S3, posts
metrics to the finetune service, writes checkpoints/artifacts to S3, exits non-zero on failure.

## 3. Web UI

```
web/
  app/                      App Router: routes, layouts (see 04 for the page list)
  components/
    ui/                     shadcn/ui primitives (avatar, badge, button, card, dialog, table, tabs, ...)
  lib/
    api/                    typed API client (client.ts, resources.ts, types.ts, sse.ts, token.ts)
    mocks/                  deterministic mock backend for dev and tests (db.ts, chat.ts, metrics.ts)
    rbac.ts                 role ordering and gating helpers (mirrors slm_common.rbac)
    config.ts, utils.ts
  tests/                    Vitest + Testing Library (setup.ts)
  public/
  .env.example              NEXT_PUBLIC_* settings (API base URL, mock toggle)
```

`lib/api/types.ts` should be generated from `docs/openapi/control-plane.yaml` (`openapi-typescript`) rather than hand-maintained,
so contract drift fails the type-check. Proposed, see [08](08-cicd.md).

## 4. Deploy artifacts

Layout and how the pieces relate: [07-kubernetes-and-docker.md](07-kubernetes-and-docker.md). In short:

| Path | Contents |
|---|---|
| `deploy/docker/` | `Dockerfile.service` (all nine services via build args), `Dockerfile.web`, `Dockerfile.trainer`, `Dockerfile.mlflow`, `healthcheck.py`, trainer entrypoint `trainer/train.py` |
| `deploy/compose/` | Compose assets (currently `vllm-stub/`, an OpenAI-compatible stub server); full stack in progress |
| `deploy/helm/slm-studio/` | Chart: `Chart.yaml`, `values.yaml`, overlays `values-dgx-spark.yaml` and `values-ha.yaml`, `ci/` values, templates; `deploy/helm/environments/` holds `staging.yaml` and `production.yaml` |
| `deploy/k8s/` | Reference manifests (namespace with Pod Security labels, NetworkPolicy, vLLM Deployment, canary), not kept in lock-step with the chart |
| `deploy/grafana/` | Dashboard JSON, alert rules, Prometheus and OTel collector configs, provisioning |
| `deploy/scripts/` | `smoke-test.sh`, `sync-dashboards.sh` |

## 5. Naming conventions

| Thing | Convention | Example |
|---|---|---|
| Python package | `slm_<service>` | `slm_gateway` |
| Distribution name | `slm-<service>` | `slm-gateway` |
| Env vars | `SLM_<UPPER_SNAKE>` | `SLM_DATABASE_URL`, `SLM_MLFLOW_URI`, `SLM_WANDB_API_KEY` |
| Migrations | `NNNN_snake_case.sql` | `0002_hardening.sql` |
| API paths | kebab-case plural nouns | `/api/v1/training-jobs` |
| JSON fields | snake_case (matches columns) | `model_version_id` |
| Redis keys | `slm:{purpose}:{scope}:{id}` | `slm:rpm:org:<org_id>`, `slm:rpm:key:<key_id>`, `slm:tpm:org:<org_id>`, `slm:month_tokens:<org_id>:<YYYYMM>` (as used by the gateway limiter) |
| S3 keys | `<bucket>/<org_id>/<resource>/<id>/...` | `models/<org>/<model>/v3/` ([15](15-mlops-and-rag.md)) |
| K8s labels | `app.kubernetes.io/*` plus `slm-studio.io/scrape`, `slm-studio.io/deployment` (as used by the chart) | |
| Docker images | `slm-studio/<name>:<git-sha>` | `slm-studio/gateway`, `slm-studio/trainer` |
| Git | trunk-based, short-lived branches, Conventional Commits | `feat(gateway): add TPM limiter` |

## 6. Tooling configuration already fixed in the root `pyproject.toml`

- Python `>=3.11`; ruff line length 100, target `py311`; rule sets E, F, I, UP, B, ASYNC, S (security), RUF, with `S101`, `B008`, `RUF012`, `RUF029`, `S608` ignored; `web` and `deploy` excluded from ruff.
- pytest: `asyncio_mode = "auto"`, `testpaths = ["services"]`, `--import-mode=importlib`.
- Dev dependencies: pytest, pytest-asyncio, httpx, `pgserver` (an embedded PostgreSQL for tests, which is how RLS gets real coverage without Docker), ruff.
