# SLM Studio — Engineering Contract

Shared conventions every service, the web app and the deploy artifacts build against.
Source of truth for data shapes is `db/migrations/0001_init.sql`.

## 1. Repo layout

```
slm-studio/
  CONTRACT.md  README.md
  db/migrations/              plain .sql files, applied in order by db/migrate.py
  docs/                       15 design deliverables (see docs/README.md)
  services/                   uv workspace (root pyproject.toml), Python 3.11+, FastAPI
    common/    -> package slm_common     shared library
    auth/      -> package slm_auth       port 8001
    registry/  -> package slm_registry   port 8002
    gateway/   -> package slm_gateway    port 8000  (single public entrypoint)
    finetune/  -> slm_finetune 8003 | rag/ -> slm_rag 8004 | evaluation/ -> slm_evaluation 8005
    deployment/-> slm_deployment 8006 | monitoring/ -> slm_monitoring 8007 | cost/ -> slm_cost 8008
  web/                        Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query
  deploy/                     docker/, compose/, helm/slm-studio/, k8s/ (raw manifests), grafana/
  .github/workflows/
```

Service dependency direction: `gateway -> {auth, registry, ...control-plane, vLLM}`; services never call
each other's DB tables; they share only Postgres (with RLS), Redis, S3-compatible object storage.

## 2. Platform standards

| Concern | Choice |
|---|---|
| Language/runtime | Python 3.11+, FastAPI, pydantic v2, asyncpg (no ORM; SQL in repository modules) |
| Packaging | uv workspace; each service has its own `pyproject.toml`; tests with pytest + pytest-asyncio + httpx |
| Auth tokens | RS256/HS256 JWT access (15 min) + opaque refresh (rotation, family revoke on reuse). Claims: `sub`, `org` (active org id), `role` (org_role or `platform_admin`), `exp`, `iat`, `jti` |
| Passwords | argon2id (`argon2-cffi`) |
| API keys | format `slm_<8 char prefix>_<40 char secret>`; only `sha256(full key)` stored; plaintext returned once on create/rotate |
| Tenancy | `org_id` on every row; RLS via `set_config('app.org_id', ..., true)` per transaction (`slm_common.db.tenant_tx`) |
| Errors | OpenAI-shaped on `/v1/*`: `{"error": {"message","type","code"}}`. Control plane (`/api/v1/*`): RFC 7807 `application/problem+json` |
| IDs | UUID everywhere; request id in `X-Request-ID` (generated at gateway, propagated) |
| Pagination | `?limit=50&cursor=<opaque>` -> `{"items": [...], "next_cursor": "..."|null}` |
| Observability | structlog JSON logs with `request_id`, `org_id`; OpenTelemetry traces/metrics via OTLP; `/healthz` (liveness), `/readyz` (deps), `/metrics` (Prometheus) on every service |
| Config | pydantic-settings, env prefix `SLM_`; secrets only from env/secret mounts, never committed |

### Roles & permissions (RBAC)

`platform_admin` (users.is_platform_admin) > `org_admin` > `ml_engineer` > `developer` > `viewer`.

| Capability | viewer | developer | ml_engineer | org_admin | platform_admin |
|---|---|---|---|---|---|
| Read models/deployments/evals/usage | ✓ | ✓ | ✓ | ✓ | ✓ |
| Call inference, use playground, create project-level keys | | ✓ | ✓ | ✓ | ✓ |
| Register models, datasets, fine-tune, evaluate, RAG indexes | | | ✓ | ✓ | ✓ |
| Create/scale/stop deployments, endpoints & routes | | | ✓ | ✓ | ✓ |
| Org-level keys, members, quotas, SSO, audit log, cost reports | | | | ✓ | ✓ |
| Global catalogue, all orgs, platform settings | | | | | ✓ |

Implemented as `slm_common.rbac.require_role(min_role)` FastAPI dependency (ordered comparison).

## 3. `slm_common` public API (other packages import exactly these names)

```python
slm_common.config:   class BaseServiceSettings(BaseSettings)   # SLM_DATABASE_URL, SLM_REDIS_URL, SLM_JWT_*, SLM_OTLP_ENDPOINT, SLM_ENV
                     def get_settings() -> BaseServiceSettings
slm_common.db:       async def create_pool(dsn) -> asyncpg.Pool
                     @asynccontextmanager tenant_tx(pool, org_id: UUID | None, *, bypass: bool=False) -> asyncpg.Connection
slm_common.security: hash_password / verify_password; create_access_token(...) / decode_token(...);
                     generate_api_key() -> (plaintext, prefix, sha256_bytes); hash_api_key(plaintext) -> bytes
slm_common.auth:     @dataclass Principal(kind: 'user'|'api_key', user_id, org_id, role, project_id, key_id)
                     async def get_principal(request) -> Principal      # FastAPI dependency (JWT bearer)
slm_common.rbac:     Role(IntEnum); def require_role(min_role) -> Depends
slm_common.errors:   class ApiError(Exception); problem_json_handler; openai_error_handler
slm_common.logging:  configure_logging(service: str); request-id middleware
slm_common.telemetry:setup_otel(app, service: str); metrics middleware
slm_common.audit:    async def audit(conn, *, org_id, actor, action, resource, resource_id=None, detail=None, request=None)
slm_common.pagination: encode_cursor / decode_cursor
```

## 4. Public/inference API (served by `gateway`, port 8000) — OpenAI-compatible

Auth: `Authorization: Bearer slm_...` (API key). `model` in the request body is an **endpoint alias** (table `endpoints`);
the gateway resolves it to weighted `endpoint_routes` (A/B, canary), picks a running deployment and proxies to its vLLM
`internal_url`, streaming SSE through untouched. Global catalogue shared aliases also resolve (kind `shared`).

```
POST /v1/chat/completions   POST /v1/completions   POST /v1/embeddings   GET /v1/models
```

Gateway responsibilities per request: key lookup (hash, Redis-cached 60 s) → revoked/expired/grace check → org quota +
sliding-window rate limit (RPM & TPM, Redis) → route selection → proxy → async insert into `usage_logs`
(tokens from upstream `usage`, latency, ttft) → response headers `X-Request-ID`, `X-RateLimit-*`, `X-SLM-Route`.

## 5. Control-plane REST API — `/api/v1/*` (gateway reverse-proxies by prefix; JWT bearer from auth)

All list endpoints paginate per §2. All mutations write `audit_logs`. Org context comes from the JWT `org` claim.

```
Auth (auth svc)
  POST /api/v1/auth/register           {email,password,display_name,org_name?}
  POST /api/v1/auth/login              {email,password} -> {access_token,refresh_token,expires_in}
  POST /api/v1/auth/refresh            {refresh_token}
  POST /api/v1/auth/logout
  GET  /api/v1/auth/oauth/{provider}/start     provider ∈ azure_ad|google|github
  GET  /api/v1/auth/oauth/{provider}/callback
  GET  /api/v1/me                      -> user + memberships
  POST /api/v1/orgs/switch             {org_id} -> new tokens
  GET/POST /api/v1/orgs/members        (org_admin)      PATCH/DELETE /api/v1/orgs/members/{user_id}
  GET/POST /api/v1/projects            GET/PATCH/DELETE /api/v1/projects/{id}
  GET/POST /api/v1/api-keys            POST /api/v1/api-keys/{id}/rotate   DELETE /api/v1/api-keys/{id}
  GET  /api/v1/api-keys/{id}/usage

Registry
  GET/POST /api/v1/models              GET/PATCH /api/v1/models/{id}        POST /api/v1/models/{id}/archive
  GET/POST /api/v1/models/{id}/versions   POST /api/v1/models/{id}/versions/{v}/rollback  (re-activate older version)
  PATCH /api/v1/models/{id}/versions/{v}  (benchmark_scores, status)

Deployment            GET/POST /api/v1/deployments   GET/PATCH/DELETE /api/v1/deployments/{id}
                      POST /api/v1/deployments/{id}/{start|stop|scale|rollback}
                      GET/POST /api/v1/endpoints  PUT /api/v1/endpoints/{id}/routes  (weights sum to 100)
Fine-tuning           GET/POST /api/v1/datasets (multipart upload)  GET/POST /api/v1/training-jobs
                      GET /api/v1/training-jobs/{id}/metrics (loss curve)  POST .../{id}/cancel
RAG                   GET/POST /api/v1/rag/indexes  POST /api/v1/rag/indexes/{id}/documents  POST /api/v1/rag/indexes/{id}/query
Evaluation            GET/POST /api/v1/evaluations  GET /api/v1/evaluations/{id}/results  GET /api/v1/evaluations/compare?ids=a,b
Monitoring            GET /api/v1/monitoring/overview?window=1h   GET /api/v1/monitoring/deployments/{id}
                      WS  /api/v1/monitoring/stream   (real-time metrics push)
Cost                  GET /api/v1/cost/summary?group_by=project|deployment|model|org&from&to   GET /api/v1/cost/report.csv
Audit                 GET /api/v1/audit-logs  (org_admin)
```

## 6. Tool/MLOps integration points

- vLLM flags exposed through `deployments.vllm_args`: `max_model_len`, `gpu_memory_utilization`, `enable_prefix_caching`
  (KV cache reuse), `speculative_config` (speculative decoding), `max_num_seqs`/`max_num_batched_tokens` (batching),
  `quantization`, `enable_lora` + `lora_modules` (serve fine-tuned adapters on a shared base).
- MLflow tracking server (`SLM_MLFLOW_URI`) for training/eval runs; W&B optional (`SLM_WANDB_*`); HF Hub for model pulls.
- Object storage (S3 API; MinIO on-prem) buckets: `models`, `datasets`, `rag-documents`, `checkpoints`, `reports`.
- Training runs as a Kubernetes `Job` (image `slm-studio/trainer`, Unsloth + PEFT + TRL) requesting `nvidia.com/gpu`.
