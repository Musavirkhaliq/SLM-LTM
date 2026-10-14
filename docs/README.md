# SLM Studio - Design Documentation

SLM Studio is a multi-tenant SLM-as-a-service platform: open-source small language models served by vLLM
on NVIDIA DGX Spark and Kubernetes behind an OpenAI-compatible API, with fine-tuning (Unsloth / QLoRA / PEFT),
RAG, evaluation, monitoring and cost analytics.

Binding sources of truth (the docs must not contradict them; if they do, the docs are wrong):

- [`../CONTRACT.md`](../CONTRACT.md) - service names, ports, API paths, roles, shared library API.
- [`../db/migrations/0001_init.sql`](../db/migrations/0001_init.sql) - table and column names, RLS policies.

Document date: 2026-10-14. Conventions used throughout: **Assumption** marks a value or decision that was
not given in the brief and must be confirmed; **Proposed** marks a design that the contract does not yet
pin down; **Status** lines state what exists in the repo today.

## Document index

| # | Document | Read it for |
|---|---|---|
| 01 | [Architecture](01-architecture.md) | Context/container diagrams, service responsibilities, request flows, multi-tenancy, capacity math, hardware tiers |
| 02 | [Database schema](02-database-schema.md) | ER diagram, table notes, RLS, partitioning/retention, indexing, migration policy, schema gaps |
| 03 | [API specification](03-api-spec.md) | OpenAI-compatible surface, control-plane summary, errors, limits |
| - | [`openapi/inference.yaml`](openapi/inference.yaml) | OpenAPI 3.1 for `/v1/*` (streaming, error schemas) |
| - | [`openapi/control-plane.yaml`](openapi/control-plane.yaml) | OpenAPI 3.1 for `/api/v1/*` (CONTRACT.md section 5) |
| 04 | [Frontend wireframes](04-frontend-wireframes.md) | Every page: wireframe, components, states, role gating |
| 05 | [Folder structure](05-folder-structure.md) | Repo layout and per-package conventions |
| 06 | [Infrastructure](06-infrastructure.md) | Infra diagram, DGX Spark and H100 topologies, HA, DR/backup |
| 07 | [Kubernetes and Docker](07-kubernetes-and-docker.md) | How `deploy/` is organised (references, not copies) |
| 08 | [CI/CD](08-cicd.md) | Pipelines, gates, promotion, supply chain |
| 09 | [Deployment guide](09-deployment-guide.md) | Bare DGX Spark to first inference call |
| 10 | [Production checklist](10-production-checklist.md) | Go-live gates |
| 11 | [Security checklist](11-security-checklist.md) | RBAC, crypto, secrets, audit chain, HIPAA/GDPR control mapping |
| 12 | [Monitoring design](12-monitoring-design.md) | Metrics catalogue, SLOs, alerts, dashboards, tracing, log schema |
| 13 | [Cost estimation](13-cost-estimation.md) | GPU-hour and token cost model, fine-tune estimator, chargeback, worked example |
| 14 | [Roadmap](14-roadmap.md) | Phases, exit criteria, status, risks, licence caveats |
| 15 | [MLOps and RAG](15-mlops-and-rag.md) | MLflow/W&B/HF Hub, lineage, RAG pipeline, vector DB choice, evaluation metric definitions |

## Implementation status (as of 2026-10-14)

"Implemented" means feature-complete for the MVP scope in the contract with tests; it does not mean
production-hardened (see [10-production-checklist.md](10-production-checklist.md)).

| Component | Path | Port | Status |
|---|---|---|---|
| Database schema | `db/migrations/0001_init.sql` | - | Implemented (single migration; gaps listed in [02](02-database-schema.md#schema-gaps-and-follow-up-migrations)) |
| Shared library `slm_common` | `services/common/` | - | Implemented (API per CONTRACT.md section 3) |
| Auth service | `services/auth/` (`slm_auth`) | 8001 | Implemented |
| Registry service | `services/registry/` (`slm_registry`) | 8002 | Implemented |
| Gateway | `services/gateway/` (`slm_gateway`) | 8000 | Implemented |
| Fine-tuning service | `services/finetune/` (`slm_finetune`) | 8003 | Typed stub plus a few real utilities |
| RAG service | `services/rag/` (`slm_rag`) | 8004 | Typed stub plus a few real utilities |
| Evaluation service | `services/evaluation/` (`slm_evaluation`) | 8005 | Typed stub plus a few real utilities |
| Deployment service | `services/deployment/` (`slm_deployment`) | 8006 | Partial: CRUD + state machine (start/stop/scale/rollback, route repointing) over Postgres; Kubernetes apply/health-poll not wired (no cluster client) |
| Monitoring service | `services/monitoring/` (`slm_monitoring`) | 8007 | Typed stub plus a few real utilities |
| Cost service | `services/cost/` (`slm_cost`) | 8008 | Typed stub plus a few real utilities |
| Web UI | `web/` | 3000 (`web/package.json`) | In progress |
| Docker images, Compose, Helm chart, raw manifests, Grafana dashboards, smoke script | `deploy/` | - | Present in the repo, not yet run against a real cluster/GPU; compose stack incomplete |
| CI/CD workflows (`ci.yml`, `build-push.yml`, `deploy.yml`) | `.github/workflows/` | - | Present; gaps listed in [08](08-cicd.md) section 3.1 |
| Trainer image (`slm-studio/trainer`), vector DB adapters, MLflow wiring | - | - | Designed here, not built |

"Typed stub" means request/response models and route signatures exist and the endpoints respond, but the
business logic behind them (Kubernetes orchestration, training launch, ingestion, judge-based evaluation,
metric roll-ups, cost roll-ups) is not complete. Documents describe the **target design** for these;
each such section carries a status line.

## Open assumptions that affect many documents

| # | Assumption | Where it matters | Owner to confirm |
|---|---|---|---|
| A1 | Two hardware tiers: DGX Spark (GB10, single node, dev/edge) and multi-GPU H100-class (production). The original brief said "multi-GPU H100 class" while also naming DGX Spark; they are different products | 01, 06, 13 | Platform owner |
| A2 | Throughput figures for both tiers are planning values derived from a memory-bandwidth model, not measurements | 01, 13 | Perf engineering (benchmark before sizing a purchase) |
| A3 | Prices (hardware, power, colocation) are placeholders in USD | 13 | Finance |
| A4 | `endpoint_kind` semantics (`private` / `shared` / `dedicated`) as defined in 01 | 01, 13 | Product |
| A5 | Which services may use `app.bypass_rls` (gateway key lookup needs it or a SECURITY DEFINER function) | 02, 11 | Security |
| A6 | HIPAA and GDPR are targeted as "ready" (controls available), not certified | 11 | Compliance |

## How to keep these docs honest

1. A change to `CONTRACT.md` or a migration must update the affected docs in the same pull request
   (CI check proposed in [08-cicd.md](08-cicd.md)).
2. `openapi/*.yaml` are validated in CI; `03-api-spec.md` tables are hand-written summaries and defer to the YAML.
3. Numbers in 01 and 13 are generated from the formulas stated next to them; change an assumption, recompute the table.
