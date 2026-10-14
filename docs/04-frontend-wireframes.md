# 04 - Frontend Wireframes

Target: `web/` - Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui (Radix primitives), TanStack Query,
Recharts (charts), `@xyflow/react` (graph canvases: lineage, route topology). Dev server on port 3000.
The UI talks only to the gateway (`/api/v1/*`); during development `web/lib/mocks` can stand in for backend services.

Status: the web UI is in progress. Route names below are **Proposed** and must be reconciled with `web/app/` as it lands;
the API operations each page depends on are real or specified in [03-api-spec.md](03-api-spec.md). Operations that are
not yet in CONTRACT.md are flagged "(gap)".

## 1. Global shell

```
+----------------------------------------------------------------------------+
| [logo] SLM Studio   Org: [Acme v]  Project: [All v]      (?) [moon] [AK v]  |
+------------------+---------------------------------------------------------+
| Overview         |                                                         |
| Models           |                  page content                           |
| Playground       |                                                         |
| Deployments      |                                                         |
| Endpoints        |                                                         |
| Fine-tuning      |                                                         |
|  Datasets        |                                                         |
| RAG              |                                                         |
| Evaluations      |                                                         |
| Monitoring       |                                                         |
| Cost             |                                                         |
| ---------------- |                                                         |
| API keys         |                                                         |
| Audit log        |                                                         |
| Settings         |                                                         |
| Admin (platform) |                                                         |
+------------------+---------------------------------------------------------+
```

- **Org switcher** calls `POST /api/v1/orgs/switch`, replaces tokens, clears the TanStack Query cache (never show another org's cached data).
- **Project filter** is a client-side query parameter on list calls; "All" = no filter.
- **Nav items are hidden, not disabled,** when the role cannot even read the page (viewer sees no Admin); items the user can read but not mutate stay visible and the action buttons are disabled with a tooltip naming the required role.
- Role is read from `GET /api/v1/me` + the active membership; the UI gating is a convenience only, the API enforces RBAC.
- Token handling: access token in memory, refresh token in an `HttpOnly; Secure; SameSite=Strict` cookie via a Next.js route handler (**Proposed**, avoids XSS-readable refresh tokens); 401 triggers one silent refresh then redirect to login.
- Theme: light/dark via `next-themes`; all charts use CSS variables.

### 1.1 Shared components

| Component | Built from | Used by |
|---|---|---|
| `DataTable` | shadcn Table + TanStack Table, cursor pagination ("Load more") | every list page |
| `StatusBadge` | Badge; maps `deploy_status` / `job_status` / `index_status` to color + icon (not color alone) | all |
| `RoleGate` | wrapper: `<RoleGate min="ml_engineer" fallback="disable">` | all mutations |
| `ConfirmDialog` | Dialog; destructive actions require typing the resource name | delete, stop, rotate, revoke |
| `SecretOnce` | Dialog + copy button; shows the API key once, warns it cannot be recovered | api-keys |
| `MetricCard`, `TimeSeriesChart`, `Sparkline` | Card + Recharts | dashboard, monitoring, cost |
| `JsonEditor` | textarea with schema validation for `vllm_args` / `hyperparams` | deployments, fine-tune |
| `ProblemAlert` | Alert rendering RFC 7807 `title`, `detail`, field `errors`, and `request_id` with copy button | all forms |
| `EmptyState` | icon + one sentence + primary action (hidden if role lacks it) | all lists |
| `LiveBadge` | WebSocket connection state (live / reconnecting / polling fallback) | monitoring |

### 1.2 Standard states (every page)

| State | Behaviour |
|---|---|
| Loading | Skeleton matching final layout (no spinners over empty pages) |
| Empty | `EmptyState` with the action that creates the first item |
| Error | `ProblemAlert` with retry; request id visible |
| Forbidden (403) | Page-level "Requires <role>" panel, no data requested |
| Not found (404) | Same panel for cross-tenant ids (the API returns 404 for those) |
| Stale / refetching | Subtle top progress bar; data stays visible |
| Long-running | Status badge + auto-refresh (5 s while `provisioning`/`running` jobs) |

## 2. Role-gating matrix

R = can view, W = can create/modify, D = can delete/stop, - = hidden. Mirrors CONTRACT.md section 2.

| Page | viewer | developer | ml_engineer | org_admin | platform_admin |
|---|---|---|---|---|---|
| Overview | R | R | R | R | R |
| Models (catalogue) | R | R | R, W (register, version, archive) | R, W | R, W (+global catalogue) |
| Playground | R (view only, send disabled) | W | W | W | W |
| Deployments, Endpoints | R | R | W, D | W, D | W, D |
| Datasets, Fine-tuning | R | R | W, D | W, D | W, D |
| RAG | R | R, query | W, D | W, D | W, D |
| Evaluations | R | R | W | W | W |
| Monitoring | R | R | R | R | R |
| Cost | - | - | - | R (export) | R (+ all orgs) |
| API keys | - | W project keys | W project keys | W (org and project keys) | W |
| Audit log | - | - | - | R | R (all orgs) |
| Settings: members, quotas, SSO | - | - | - | W | W |
| Admin (all orgs, platform settings) | - | - | - | - | W |

Note: the contract lets `developer` "create project-level keys" and puts "org-level keys, members, quotas, SSO, audit log, cost reports" with `org_admin`.
The viewer has no key access at all, so API keys are hidden for viewer.

## 3. Pages

### 3.1 Login / Register  (`/login`, `/register`)

```
+--------------------------------------------+
|              SLM Studio                    |
|  Email    [______________________]         |
|  Password [______________________]         |
|  (MFA code field appears if required)      |
|  [ Sign in ]                               |
|  ---------------- or ----------------      |
|  [ Microsoft ]  [ Google ]  [ GitHub ]     |
|  No account? Register                      |
+--------------------------------------------+
```
Components: Card, Input, Button, OAuth buttons (redirect to `GET /api/v1/auth/oauth/{provider}/start`). API: `POST auth/login`, `auth/register`.
States: invalid credentials (generic message, no account enumeration), rate-limited (429, show retry time), SSO error. Register has optional "Organization name" (creates org and makes the user `org_admin`).

### 3.2 Overview  (`/`)

```
+----------------------------------------------------------------------------+
| Last [1h v]                                                                |
| +-------------+ +-------------+ +-------------+ +-------------+            |
| | Requests    | | p95 latency | | Error rate  | | Tokens      |            |
| | 12.4k  ^8%  | | 1.8 s       | | 0.3 %       | | 9.2M in 2.1M|            |
| +-------------+ +-------------+ +-------------+ +-------------+            |
| Requests / min                       | Deployments                         |
| ||||||||||||||||||||                 | phi-4-mini     running   2/2 GPU    |
| ||||||||||||||||||||                 | support-v3     running   1/1 GPU    |
|                                      | llama-eval     failed    -   [view] |
| Recent activity (audit)              | Quota                               |
|  deploy support-v3 scaled   2m ago   | RPM 41% [####------]  TPM 18% [##--]|
|  key slm_a1b2 rotated      1h ago    | Budget $212 / $500 [####------]     |
+----------------------------------------------------------------------------+
```
API: `GET monitoring/overview?window=`, `GET deployments`, `GET audit-logs` (org_admin only; panel hidden otherwise), quota read (gap: no quota GET in the contract). Empty state for a new org: a 3-step checklist (create project, create API key, deploy a model).

### 3.3 Models  (`/models`, `/models/[id]`)

```
+----------------------------------------------------------------------------+
| Models                [Search______] [Family v] [Source v] [+ Register model]|
| +------------------------------------------------------------------------+ |
| | Name          Family  Params  License      Latest ver  Source   Scope   | |
| | phi-4-mini    phi     3.8B    MIT          v1 active   HF       Global  | |
| | llama-3.1-8b  llama   8B      Llama 3.1 !  v1 active   HF       Global  | |
| | support-v3    phi     3.8B    MIT          v3 active   finetune Org     | |
| +------------------------------------------------------------------------+ |
| (!) = licence is not OSI open source; click for terms                      |
+----------------------------------------------------------------------------+

Detail:
+----------------------------------------------------------------------------+
| support-v3                          [Archive] [Deploy] [Fine-tune from this]|
| Tabs: Versions | Lineage | Benchmarks | Deployments | Settings              |
| Versions                                                                   |
|  v3 active    qlora  base: phi-4-mini v1  dataset: tickets-q3  acc 0.81    |
|  v2 archived  ...                                       [Rollback to v2]   |
| Lineage (graph): dataset -> job -> v3 -> deployment -> endpoint            |
+----------------------------------------------------------------------------+
```
Components: DataTable, Badge (licence warning icon for `Llama*`/`Gemma*` licences, see [14](14-roadmap.md#5-model-licence-caveats)), Dialog (register: source = HF repo / upload; HF gated-model notice), `@xyflow/react` lineage graph, benchmark Table.
API: `GET/POST models`, `GET models/{id}/versions`, `POST .../rollback`, `PATCH .../versions/{v}`. Role: register/archive/rollback need `ml_engineer`; global catalogue edits need `platform_admin`.
States: HF pull in progress (version `registered`, progress via metadata), checksum mismatch error.

### 3.4 Playground  (`/playground`)

```
+----------------------------------------------------------------------------+
| Model [phi-4-mini v]  Key [Playground (temp) v]   [Compare +]   [Clear]    |
| +--------------------------------------+ +-------------------------------+ |
| | System: You are a concise assistant  | | Temperature  [----o------] 0.7| |
| | -------------------------------------| | Top-p        [---------o-] 0.95| |
| | You: Explain KV caching              | | Max tokens   [ 512 ]          | |
| | AI : KV caching stores attention...  | | Stop         [____________]   | |
| |      (streaming...)                  | | Seed         [____]           | |
| |                                      | | [ ] JSON mode                 | |
| | [ type a message...         ] [Send] | | [ ] Attach RAG index [v]      | |
| +--------------------------------------+ +-------------------------------+ |
| TTFT 212 ms | 38 tok/s | 142 in / 96 out | route: stable | req 9f3c...     |
+----------------------------------------------------------------------------+
```
Components: chat transcript, SSE reader (`lib/api/sse.ts`), Slider, Switch, Select; "Compare" splits into two panes sending the same prompt to two aliases (also used for A/B sanity checks); "View as code" tab generates curl and OpenAI-SDK snippets with the selected parameters.
Open question OQ-4 ([14](14-roadmap.md)): the playground needs a credential accepted by `/v1/*`, which accepts API keys; options are a short-lived project key minted per session (recommended, audited, scoped to the user's project) or user-supplied key. Until decided the page uses a key picker.
States: streaming, stopped by user (aborts the fetch, which cancels upstream), 429 (shows Retry-After), `no_healthy_deployment` (link to the deployment). Viewer: transcript visible, Send disabled.

### 3.5 Deployments  (`/deployments`, `/deployments/[id]`, `/deployments/new`)

```
+----------------------------------------------------------------------------+
| Deployments                               [Status v] [+ New deployment]    |
| Name         Model@ver        Target     Kind       Status    Repl  GPUs   |
| phi-4-mini   phi-4-mini@1     dgx_spark  shared     running   1/1   1      |
| support-v3   support-v3@3     kubernetes private    running   2/4   2      |
| exp-gemma    gemma-3-4b@1     dgx_spark  private    failed    0/1   1 [!]  |
+----------------------------------------------------------------------------+

New deployment (stepper):  1 Model  2 Target  3 Runtime  4 Traffic  5 Review
+----------------------------------------------------------------------------+
| 3 Runtime                                                                  |
|  Replicas min [1] max [2]     GPUs per replica [1]                         |
|  max_model_len [8192]  gpu_memory_utilization [0.80]  max_num_seqs [64]    |
|  [x] prefix caching   [ ] speculative decoding   [x] LoRA adapters         |
|  Fit check: weights 7.6 GB + KV 38 GB <= 0.80 x 128 GB    OK               |
|  Strategy ( ) direct  (o) canary 5%  ( ) blue/green                        |
+----------------------------------------------------------------------------+

Detail tabs: Overview | Metrics | Events | Config | Routes
  [Start] [Stop] [Scale] [Rollback]    status timeline from deployment_events
```
Components: Stepper, JsonEditor (advanced vLLM args, closed schema), fit estimator (client-side formula from [01](01-architecture.md) section 9, server re-validates), Table of `deployment_events`.
API: `GET/POST deployments`, `PATCH`, `POST .../{start|stop|scale|rollback}`, `DELETE`. Role: read all; mutate `ml_engineer+`. `kind = dedicated` is hidden unless the org plan allows it (**Proposed**).
States: `provisioning` (progress + log tail of events), `failed` (error banner, "Rollback" if `previous_deployment_id` set), `rolling_back`. Destructive Stop/Delete use `ConfirmDialog` with the traffic share shown ("serves 100% of `support`").
Note: `GET deployments/{id}/events` is not in the contract (gap); the detail view needs it or embeds events in `GET deployments/{id}`.

### 3.6 Endpoints and routing  (`/endpoints`)

```
+----------------------------------------------------------------------------+
| Endpoints                                         [+ New alias]            |
| support            private  enabled   [Edit routes]                        |
|    ( 95%) stable  --> support-v2 (running)                                 |
|    (  5%) canary  --> support-v3 (running)                                 |
| Edit routes:                                                               |
|   support-v2  [=========o--] 95    label [stable ]                         |
|   support-v3  [o----------]  5     label [canary ]      sum = 100  OK      |
|   [Save]   Save is disabled until the sum is exactly 100                   |
+----------------------------------------------------------------------------+
```
API: `GET/POST endpoints`, `PUT endpoints/{id}/routes`. Role: `ml_engineer+`. A small `@xyflow/react` diagram shows alias -> weighted edges -> deployments. Shows an example `curl` for the alias. State: route to a non-`running` deployment shows a warning.

### 3.7 Datasets  (`/datasets`)

```
+----------------------------------------------------------------------------+
| Datasets                                 [Purpose v]  [+ Upload dataset]   |
| Name          Purpose   Format  Rows    Tokens   Size    Project   Created |
| tickets-q3    finetune  jsonl   18,402  9.1M     41 MB   support   Oct 2   |
| qa-gold       eval      jsonl   500     0.3M     1.2 MB  support   Oct 9   |
| Upload: [ drop file here ]  Name [____] Purpose (o) finetune ( ) eval      |
|   Preview (first 5 rows) + detected schema: {messages} OK / errors list    |
+----------------------------------------------------------------------------+
```
API: `GET/POST datasets` (multipart). Role: upload `ml_engineer+`. Validation shown before commit: format, required fields (`messages` or `prompt`/`completion`), token count estimate, PII warning heuristic (**Proposed**, non-blocking). Progress bar for upload; 413 -> size limit message.

### 3.8 Fine-tuning  (`/fine-tuning`, `/fine-tuning/new`, `/fine-tuning/[id]`)

```
New job
+----------------------------------------------------------------------------+
| Base model [phi-4-mini@1 v]   Dataset [tickets-q3 v]   Method (o)QLoRA ( )LoRA ( )Full |
| Hyperparameters:  lr [2e-4]  epochs [3]  batch [4]  grad accum [8]         |
|                   max_seq_len [4096]  lora_r [16]  lora_alpha [32]         |
| GPUs [1]                                                                   |
| Estimate: 27.3M tokens, ~6.6 h on dgx_spark / ~1.5 h on H100, ~$1.5-$2.4 (!)|
| (!) Estimates use planning throughputs; see docs/13                        |
| [Start training]                                                           |
+----------------------------------------------------------------------------+
Job detail
+----------------------------------------------------------------------------+
| support-v3-run1   running   step 1,240 / 3,400   ETA 2h 10m   [Cancel]     |
| Loss                                  | GPU util / tokens-per-second        |
|  \_                                   | ########## 94%      1,180 tok/s     |
|    \___ train   ..... eval            | MLflow run [open]  W&B [open]       |
| Checkpoints: step 1000 (412 MB)  step 500 ...   Logs (tail)                |
| On success: [Register as model version] [Evaluate] [Deploy]                |
+----------------------------------------------------------------------------+
```
API: `POST training-jobs`, `GET training-jobs`, `GET training-jobs/{id}/metrics` (polling every 5 s, or the stream when available), `POST .../cancel`; job detail needs `GET training-jobs/{id}` (gap). Role: `ml_engineer+`.
The estimate calls the same cost estimator as `est_cost_usd` ([13](13-cost-estimation.md)); the UI shows a range, not a point.
States: `queued` (waiting for GPU, shows queue position if available), `failed` (error text + last checkpoint), `cancelled`, OOM suggestion (reduce `max_seq_len`/batch).

### 3.9 RAG  (`/rag`, `/rag/[id]`)

```
+----------------------------------------------------------------------------+
| RAG indexes                                         [+ New index]          |
| kb-support   qdrant  bge-m3 (1024d)  hybrid  rerank  ready  1,204 docs 88k chunks |
| Detail tabs: Documents | Query test | Settings                              |
| Documents:  [ drop files ]   name        type  status     chunks           |
|                              faq.pdf     pdf   succeeded  34               |
|                              policy.docx docx  running...  -               |
| Query test: [ How do I reset my password?           ] top_k[5] [x]rerank [ ]answer |
|   1. (0.82) faq.pdf p3  "To reset your password..."                        |
|   2. (0.77) policy.docx  "Passwords must..."                               |
+----------------------------------------------------------------------------+
```
API: `GET/POST rag/indexes`, `POST rag/indexes/{id}/documents`, `POST .../query`; document list and index detail are gaps. Role: manage `ml_engineer+`, query `developer+`.
Index create form fields: name, embedding model alias, chunk size/overlap, hybrid on/off, reranker; embedding model and dimension become read-only after creation (changing them = new index). States: ingestion per-document statuses with error reason; index `creating`/`failed`.

### 3.10 Evaluations  (`/evaluations`, `/evaluations/new`, `/evaluations/[id]`, `/evaluations/compare`)

```
+----------------------------------------------------------------------------+
| Compare: eval-oct-10 (A) vs eval-oct-12 (B)          [Export CSV]          |
| Metric           A: phi-4-mini@1   B: support-v3@3   Delta                 |
| accuracy         0.62              0.81              +0.19  ^              |
| f1               0.58              0.77              +0.19  ^              |
| faithfulness     0.84              0.88              +0.04  ^   (judge: llama-3.1-8b) |
| hallucination    0.09              0.06              -0.03  ^ (lower better)|
| json_validity    0.91              0.99              +0.08  ^              |
| p95 latency      1.9 s             2.0 s             +0.1 s                |
| $/1k tokens      0.00021           0.00022           +0.00001              |
| [ Radar chart ]   [ Per-metric bars ]   Samples: 500 each                  |
+----------------------------------------------------------------------------+
```
API: `POST evaluations` (dataset, model versions 1-5, metrics multi-select, judge model required for judge-based metrics), `GET evaluations/{id}/results`, `GET evaluations/compare?ids=a,b`. Role: create `ml_engineer+`.
Components: Recharts radar and bar, delta chips with direction-aware coloring ("lower is better" metrics: hallucination, toxicity, latency, cost), significance hint (bootstrap CI overlap, **Proposed**, see [15](15-mlops-and-rag.md)). Warning banner when the judge model equals a candidate (self-preference bias). Per-sample drill-down needs sample storage (schema gap #7).

### 3.11 Monitoring  (`/monitoring`, `/monitoring/[deploymentId]`)

```
+----------------------------------------------------------------------------+
| Monitoring   Window [1h v]   Deployment [All v]               (o) live     |
| RPS | TTFT p50/p95/p99 | Inter-token latency | Tokens/s in/out               |
| Queue depth (waiting) | Running seqs | KV-cache usage % | Prefix-cache hit % |
| GPU util % | GPU mem | Power W | Temp C        (node/DCGM; N/A on unified mem)|
| Error rate by status | 429 rate | Active alerts [2]                       |
+----------------------------------------------------------------------------+
```
API: `GET monitoring/overview`, `GET monitoring/deployments/{id}`, WebSocket `monitoring/stream` (falls back to 10 s polling when the socket cannot connect; `LiveBadge` shows which). Role: all. Full panel list in [12-monitoring-design.md](12-monitoring-design.md#5-dashboard-layouts). GPU panels may be hidden or show N/A on DGX Spark where framebuffer metrics are unavailable.

### 3.12 Cost  (`/cost`)

```
+----------------------------------------------------------------------------+
| Cost   Group by [Project v]  [2026-10-01] -> [2026-10-14]  [Export CSV]    |
| Total $1,284.10        Forecast month-end $2,430 (linear)  Budget $3,000 [####---] |
| Stacked bar by day: gpu_hour | input tokens | output tokens | training | storage |
| Project     GPU-h   In tok   Out tok   Train tok   Storage   Total         |
| support     412     80M      21M       60M         200 GB    $903.40       |
| search      98      40M      4M        -           12 GB     $380.70       |
| Unallocated platform overhead (shared pool idle)            $0.00          |
+----------------------------------------------------------------------------+
```
API: `GET cost/summary?group_by=&from=&to=`, `GET cost/report.csv`. Role: `org_admin+` (platform admin sees `group_by=org`). Forecast is client-side linear until the cost service offers one. Methodology and caveats in [13](13-cost-estimation.md).

### 3.13 API keys  (`/api-keys`)

```
+----------------------------------------------------------------------------+
| API keys                                              [+ Create key]       |
| Name        Scope     Project   Prefix      Last used   Expires   Status   |
| ci-bot      project   support   slm_a1b2    2m ago      Dec 31    active   |
| legacy      org       -         slm_c3d4    3d ago      -         grace until Oct 15 |
|                                     [Usage] [Rotate] [Revoke]              |
+----------------------------------------------------------------------------+
Create: name, scope (org requires org_admin), project, RPM override, expiry
 -> SecretOnce dialog: "slm_a1b2c3d4_********...  [Copy]  Shown once."
```
API: `GET/POST api-keys`, `POST .../rotate` (grace period selector), `DELETE`, `GET .../usage` (drawer with request/token chart). Role: `developer+` for project keys; org-scope needs `org_admin`.
State: revoked keys stay listed (struck through) for audit; rotation shows both keys with the old key's `grace_until` countdown.

### 3.14 Projects  (`/settings/projects`)
Table + create dialog (slug, name, description); delete is `org_admin` only and warns that resources lose their project association (FKs `SET NULL`). API: `GET/POST projects`, `PATCH/DELETE projects/{id}`.

### 3.15 Settings: members, org, quotas, SSO  (`/settings/members`, `/settings/org`)

```
+----------------------------------------------------------------------------+
| Members                                               [+ Add member]       |
| Name        Email              Role           Added                         |
| A. Khan     a.khan@acme.io     [org_admin v]  Jan 3                         |
| R. Patel    r.patel@acme.io    [ml_engineer v] Feb 1             [Remove]   |
| Quotas (org_admin): RPM [600] TPM [200000] Monthly tokens [   ] Budget $[   ] GPU-h [  ] |
| SSO: Azure AD / Google / GitHub / OIDC: configuration (gap: no table, see 02) |
+----------------------------------------------------------------------------+
```
API: `GET/POST orgs/members`, `PATCH/DELETE orgs/members/{user_id}`. Quotas and SSO have **no API in the contract and no SSO table** (gaps in [02](02-database-schema.md#schema-gaps-and-follow-up-migrations) and [03](03-api-spec.md)). Guard: the last `org_admin` cannot be demoted/removed (API returns 409).

### 3.16 Audit log  (`/audit-logs`)

```
+----------------------------------------------------------------------------+
| Audit log   [Action v] [Actor v] [From] [To]      [Verify chain] [Export]  |
| Time          Actor        Action              Resource        IP           |
| 10:42:03      a.khan       api_key.rotate      api_key/9f..    10.0.4.0/24  |
| 10:40:51      system       deployment.scale    deployment/c1.. -            |
| Row detail: request_id, JSON detail, prev_hash, row_hash (hex)             |
+----------------------------------------------------------------------------+
```
API: `GET audit-logs`. Role: `org_admin+`. "Verify chain" is **Proposed** (client verifies displayed contiguous rows' hashes, or the monitoring service exposes a verification result). Read-only; no edit/delete controls exist.

### 3.17 Platform admin  (`/admin`)
Visible to `platform_admin` only: organizations table (plan, region, status, usage), global model catalogue management, cost rates (`cost_rates`), platform settings, cell/region status. All operations cross-org and audited; entering an org context is explicit ("act as" banner, audited) rather than silent. API: `GET cost/summary?group_by=org`, registry global writes; org list API is a gap.

## 4. Accessibility and quality bar

- WCAG 2.1 AA: keyboard reachable Radix components, focus rings, status never conveyed by color alone, charts have a data-table toggle.
- Forms: client validation mirrors server schemas; server `errors[]` mapped onto fields.
- No secrets in the DOM after dismissal (`SecretOnce` clears state); no tokens in `localStorage`.
- Test strategy: Vitest + Testing Library for components (already configured in `web/vitest.config.ts`), Playwright for the happy paths (login, create key, deploy, playground stream) against the mock layer, then against the compose stack in CI ([08-cicd.md](08-cicd.md)).
