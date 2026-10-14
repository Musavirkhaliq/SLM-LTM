# 03 - API Specification

Machine-readable specs (validated as OpenAPI 3.1 in CI, see [08-cicd.md](08-cicd.md)):

- [`openapi/inference.yaml`](openapi/inference.yaml): `/v1/*`, OpenAI-compatible, API-key auth.
- [`openapi/control-plane.yaml`](openapi/control-plane.yaml): `/api/v1/*`, JWT auth, CONTRACT.md section 5.

This page is the human summary; the YAML wins on details. Both are served by the gateway on port 8000 (single public entrypoint).

## 1. Two API surfaces

| | Inference API | Control-plane API |
|---|---|---|
| Prefix | `/v1/*` | `/api/v1/*` |
| Caller | Applications, OpenAI SDKs | Web UI, CI scripts, operators |
| Credential | API key `Authorization: Bearer slm_<8>_<40>` | JWT access token (15 min) from `/api/v1/auth/login`; refresh token (opaque, rotated) |
| Tenant resolution | From the key (`org_id`, optional `project_id`) | JWT `org` claim; switch with `POST /api/v1/orgs/switch` |
| Error format | OpenAI envelope `{"error": {"message","type","param","code"}}` | RFC 7807 `application/problem+json` with extension members `code`, `request_id`, `param` and, for 422, `errors[]`; `type` is `https://slm-studio.dev/problems/<code>` |
| Pagination | not applicable (`/v1/models` returns the full list) | `?limit=50&cursor=...` returning `{"items":[...],"next_cursor":...}` |
| Served by | gateway -> vLLM | gateway -> owning service by prefix |

Every response carries `X-Request-ID` (CONTRACT.md: generated at the gateway and propagated). **Proposed:** accept a
client-supplied value only if it matches `^[A-Za-z0-9._-]{8,64}$` (log-injection guard), otherwise replace it.

## 2. Inference API

### 2.1 Endpoints

| Method and path | Purpose | Notes |
|---|---|---|
| `POST /v1/chat/completions` | Chat completion | Streaming (`stream: true`, SSE) and non-streaming; tools / `response_format` forwarded to vLLM |
| `POST /v1/completions` | Legacy completion | |
| `POST /v1/embeddings` | Embeddings | Alias must point at an embedding (pooling) deployment |
| `GET /v1/models` | List aliases usable by the caller | Own enabled `endpoints` plus shared-catalogue aliases |

Not provided: `/v1/images`, `/v1/audio`, `/v1/files`, `/v1/fine_tuning` (fine-tuning is the control-plane API),
Assistants/Responses APIs. Clients that call them get 404 `invalid_request_error`.

### 2.2 Model aliases

`model` is an **endpoint alias** (`endpoints.alias`), e.g. `phi-4-mini` or `acme-support-v3`. Resolution order
(**Proposed**, contract says only that shared aliases "also resolve"): the caller's org alias first, then the shared catalogue.
An alias maps to weighted `endpoint_routes`; traffic splits for A/B and canary are therefore invisible to the client, except via
the response header `X-SLM-Route` (the route `label`, e.g. `canary`). The `model` field in the response echoes the
requested alias, not the underlying deployment name.

### 2.3 Headers

| Header | Direction | Meaning |
|---|---|---|
| `Authorization: Bearer slm_...` | request | API key |
| `X-Request-ID` | both | Correlation id; also in logs and trace attributes |
| `X-SLM-Route` | response | Route label that served the call (`endpoint_routes.label`, or `default` when it is NULL) |
| `X-RateLimit-*` | response | OpenAI-style, lower-case on the wire in the gateway: `x-ratelimit-limit-requests`, `-remaining-requests`, `-reset-requests` (e.g. `60s`), `-limit-tokens`, `-remaining-tokens`, `-reset-tokens`. Verified against `services/gateway/src/slm_gateway/limits.py` |
| `Retry-After` | response (429, 503) | Seconds |

### 2.4 Streaming

- `stream: true` returns `text/event-stream`; events are `data: {chunk}` terminated by a blank line; the stream ends with `data: [DONE]`.
- The gateway passes bytes through; it does not re-chunk. Response headers are sent before the first token, so rate-limit headers describe the state at request start.
- Usage appears in the stream only if the client sets `stream_options.include_usage = true`. The gateway always requests it upstream for metering, and strips the extra chunk when the client did not ask for it.
- An error after streaming has begun cannot change the HTTP status (already 200): the gateway emits `data: {"error": {...}}` then closes. Clients must handle both.
- Client disconnect cancels the upstream generation.
- Proxies in front of the gateway must disable response buffering (`proxy_buffering off` / `X-Accel-Buffering: no`); the ingress configuration in `deploy/` must do this.

### 2.5 Error catalogue (`/v1/*`)

Envelope: `{"error": {"message", "type", "param", "code"}}` (`param` is null unless a request parameter is at fault).
`type` is derived from the HTTP status by `slm_common.errors.openai_error_handler`: 400/404/409/422 `invalid_request_error`, 401 `authentication_error`,
403 `permission_error`, 429 `rate_limit_error`, anything else >= 500 `server_error`. Codes below were read from the gateway source on 2026-10-14.

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_json` | Body is not valid JSON |
| 400 | `invalid_request_body` | Body is not a JSON object or fails validation (`param` names the field) |
| 401 | `missing_api_key` | No `Authorization: Bearer` header |
| 401 | `invalid_api_key` | Malformed or unknown key (malformed keys never reach the cache or the DB) |
| 401 | `api_key_revoked` | `revoked_at` set |
| 401 | `api_key_expired` | `expires_at` passed, or a rotated key whose `grace_until` passed |
| 404 | `model_not_found` | No enabled alias visible to the caller (`param: model`) |
| 413 | `request_too_large` | Body larger than the gateway limit (4 MiB default) |
| 429 | `rate_limit_exceeded` | RPM (per key and per org) or TPM (per org) exceeded; headers `retry-after`, `x-ratelimit-*` |
| 429 | `insufficient_quota` | Monthly token limit reached; retrying does not help |
| 502 | `upstream_error` | Connection to the model server failed, or the stream was dropped |
| 502 | `upstream_unavailable` | No model server could be reached after retries |
| 503 | `no_healthy_deployment` | The alias has no `running` deployment |
| 504 | `upstream_timeout` | Model server did not respond within the read timeout |
| 500 | `internal_error` | Unhandled gateway error |

Upstream vLLM errors that are returned as a response (for example a prompt longer than `max_model_len`, HTTP 400) are passed through with vLLM's own body.
Not implemented: a per-key alias allow-list (`403 model_not_permitted` in earlier drafts), and `monthly_budget_usd` enforcement at the gateway (only `monthly_token_limit` is enforced; USD budgets need the cost roll-up).

### 2.6 Limits and quota semantics

- **RPM:** a sliding window (60 s) checked first per API key when `api_keys.rate_limit_rpm` is set, then per org with `quotas.rpm_limit` (default 600). Requests are counted at admission.
- **TPM:** per org, `quotas.tpm_limit` (default 200,000). Admission uses an estimate (prompt estimate plus `max_tokens`, or a default completion estimate of 256 tokens when absent) and the real token count is charged after completion, so a burst can overshoot by what is in flight but not more.
- **Monthly:** `quotas.monthly_token_limit` (NULL = unlimited) from a Redis counter seeded from `usage_logs` for the current month; reached = `insufficient_quota`.
- **Request size:** 4 MiB body (`max_request_bytes`); context length bounded per deployment by `max_model_len`.
- **Timeouts:** upstream connect 5 s, read 300 s (`upstream_connect_timeout`, `upstream_read_timeout`), configurable.
- **Caches:** API key info cached 60 s (negative results 5 s); route table cached 5 s in-process. Key revocation and route changes therefore take up to 60 s / 5 s to reach a gateway replica.
- **Redis outage:** limits and counters fall back to a per-replica in-process store instead of failing requests.

### 2.7 Using the OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(base_url="https://api.slm.example.com/v1", api_key="slm_a1b2c3d4_<secret>")
stream = client.chat.completions.create(
    model="phi-4-mini",
    messages=[{"role": "user", "content": "Summarise RLS in one line."}],
    stream=True,
    stream_options={"include_usage": True},
)
for chunk in stream:
    if chunk.choices:
        print(chunk.choices[0].delta.content or "", end="")
```

Compatibility notes: sampling parameters follow vLLM semantics (`seed` is best-effort, not bit-exact across batch
compositions); `logprobs`, `tools`, `response_format` depend on the model and chat template; vLLM-specific extras
(`guided_json`, `top_k`, ...) pass through but are not part of the contract; embeddings dimension is fixed by the model.

## 3. Control-plane API

All routes: JWT bearer, RFC 7807 errors, cursor pagination, audited mutations. Minimum role in the right-hand column
(from the RBAC table in CONTRACT.md section 2). "Svc" is the service the gateway proxies to; status is the
implementation status of that service.

| Area | Operations | Min role | Svc (status) |
|---|---|---|---|
| Auth | `POST auth/register`, `auth/login`, `auth/refresh`, `auth/logout`; `GET auth/oauth/{provider}/start` and `/callback` (azure_ad, google, github); `GET me` | public / any | auth (implemented) |
| Organization | `POST orgs/switch`; `GET/POST orgs/members`; `PATCH/DELETE orgs/members/{user_id}` | any / org_admin | auth (implemented) |
| Projects | `GET/POST projects`; `GET/PATCH/DELETE projects/{id}` | viewer read, developer write, org_admin delete | auth (implemented) |
| API keys | `GET/POST api-keys`; `POST api-keys/{id}/rotate`; `DELETE api-keys/{id}`; `GET api-keys/{id}/usage` | developer (project keys), org_admin (org keys) | auth (implemented) |
| Registry | `GET/POST models`; `GET/PATCH models/{id}`; `POST models/{id}/archive`; `GET/POST models/{id}/versions`; `POST .../versions/{v}/rollback`; `PATCH .../versions/{v}` | viewer read, ml_engineer write | registry (implemented) |
| Deployments | `GET/POST deployments`; `GET/PATCH/DELETE deployments/{id}`; `POST deployments/{id}/{start,stop,scale,rollback}`; `GET/POST endpoints`; `PUT endpoints/{id}/routes` | viewer read, ml_engineer write | deployment (typed stub) |
| Fine-tuning | `GET/POST datasets` (multipart); `GET/POST training-jobs`; `GET training-jobs/{id}/metrics`; `POST training-jobs/{id}/cancel` | viewer read, ml_engineer write | finetune (typed stub) |
| RAG | `GET/POST rag/indexes`; `POST rag/indexes/{id}/documents`; `POST rag/indexes/{id}/query` | viewer read, ml_engineer write, developer query | rag (typed stub) |
| Evaluation | `GET/POST evaluations`; `GET evaluations/{id}/results`; `GET evaluations/compare?ids=a,b` | viewer read, ml_engineer write | evaluation (typed stub) |
| Monitoring | `GET monitoring/overview?window=1h`; `GET monitoring/deployments/{id}`; WebSocket `monitoring/stream` | viewer | monitoring (typed stub) |
| Cost | `GET cost/summary?group_by=project\|deployment\|model\|org&from&to`; `GET cost/report.csv` | org_admin (`group_by=org`: platform_admin) | cost (typed stub) |
| Audit | `GET audit-logs` | org_admin | auth (Proposed owner, see [01](01-architecture.md)) |

### 3.1 Conventions

- **IDs** are UUIDs; **times** are RFC 3339 UTC.
- **Cross-tenant ids return 404**, not 403, to avoid confirming existence. 403 means "your role is too low".
- **Async operations** (`POST deployments`, `POST training-jobs`, `POST rag/indexes`, `POST evaluations`, `deployments/{id}/start|stop|scale|rollback`) return `202 Accepted` with the resource in its initial state; poll `GET` or subscribe to the monitoring stream. State machines: deployment `pending -> provisioning -> running -> stopped | failed | rolling_back`; job `queued -> running -> succeeded | failed | cancelled`.
- **Concurrency**: `PATCH` bodies are partial. No `If-Match`/ETag yet (**gap**, Phase 2); last write wins.
- **Idempotency**: no `Idempotency-Key` support yet. `POST` creating a uniquely-named resource is naturally idempotent via 409 on the unique constraint (`UNIQUE (org_id, name)` etc.). Proposed: accept `Idempotency-Key` on `POST api-keys` and `POST training-jobs` (both are costly to duplicate).
- **Secrets**: API key plaintext appears only in the create/rotate response. It is never returned by `GET`, never logged, never in audit `detail`.
- **Validation**: `vllm_args`, `hyperparams` are closed schemas (`additionalProperties: false`); unknown keys are 422 because they would otherwise reach a container command line.
- **Uploads**: `multipart/form-data`; the gateway streams to the owning service, which streams to S3 (no full buffering). Size limits: the chart's API ingress allows 512 MB request bodies (`proxy-body-size`), which is the effective ceiling today; larger datasets need a presigned multipart upload straight to S3 (**Proposed**). Per-file application limits (**Assumption**): datasets 2 GiB via presigned upload, RAG documents 100 MiB each.

### 3.2 WebSocket `monitoring/stream`

Browsers cannot set an `Authorization` header on WebSocket. **Proposed:** the client opens
`wss://host/api/v1/monitoring/stream`, then sends `{"type":"auth","token":"<jwt>"}` as its first frame within 5 s;
the server closes with code 4401 otherwise. Server pushes one JSON message per second per subscription
(`MonitoringStreamMessage` in the OpenAPI file). The token expires after 15 min; the client reconnects with a fresh token
(the server closes with 4401 at expiry).

### 3.3 Operations the UI needs that are not in CONTRACT.md section 5

Marked `x-proposed: true` in `control-plane.yaml`; they should be added to the contract before the web UI depends on them.

| Operation | Needed by |
|---|---|
| `GET /api/v1/training-jobs/{id}` | Job detail page |
| `GET /api/v1/evaluations/{id}` | Evaluation detail page |
| `GET /api/v1/rag/indexes/{id}`, `GET /api/v1/rag/indexes/{id}/documents` | RAG index page, ingestion status |

Also missing and **not** specified anywhere yet: organization settings and quota read/write (`GET/PUT /api/v1/orgs/quotas`),
SSO configuration, usage listing (the UI "Usage" page can use `monitoring/overview` and `cost/summary` until then),
webhooks, password reset / email verification, MFA enrolment. These are tracked in [14-roadmap.md](14-roadmap.md).

## 4. Versioning and deprecation

- `/v1` and `/api/v1` are separate version spaces. Within a version only additive changes (new optional fields, new endpoints, new enum values on *response* fields clients are told to tolerate).
- Breaking changes require a new prefix (`/api/v2`), 6 months overlap (**Assumption**), `Deprecation` and `Sunset` response headers, and an audit-log counter of callers still on the old version.
- The OpenAPI files carry the version; CI fails if a path or required field is removed without a version bump (oasdiff-style check, proposed in [08-cicd.md](08-cicd.md)).
