# 10 - Production Readiness Checklist

How to use: every item is a testable statement. **P0** = must be true before real tenant data or paying traffic;
**P1** = before general availability; **P2** = within the first quarter of operation. "Evidence" says what an auditor/reviewer should
be able to look at. Security-specific controls are in [11](11-security-checklist.md); this list references them rather than repeating them.

## 0. Known blockers at time of writing (2026-10-14)

These are not checklist items; they are the reasons the answer to "ready for production?" is currently **no**, so nobody is surprised.

| # | Blocker | Phase to clear (see [14](14-roadmap.md)) |
|---|---|---|
| B1 | finetune, rag, evaluation, deployment, monitoring and cost services are typed stubs: no real orchestration, ingestion, judging, roll-ups | 1-3 |
| B2 | Deployment service does not yet manage vLLM workloads, so serving requires manual manifests | 1 |
| B3 | Web UI, Helm chart, Docker images and CI are in progress, not released | 1 |
| B4 | `bypass_rls` is a settable GUC (RLS does not defend against SQL injection); cross-tenant FK references rely on application checks | 2 |
| B5 | No external audit-chain anchoring; hash computation lives in app code | 2 |
| B6 | No measured performance baseline on either hardware tier (all capacity numbers are model-based) | 1 |
| B7 | No restore drill performed | 1 |
| B8 | Org-level SSO configuration and invitations have no schema | 2 |

## 1. Availability and resilience

| ID | Item | Pri | Evidence |
|---|---|---|---|
| A1 | Gateway >= 3 replicas across failure domains; PDBs set; rolling updates `maxUnavailable: 0` | P0 | Helm values, `kubectl get pdb` |
| A2 | Every production endpoint alias has >= 2 vLLM replicas on different nodes (T3) or the single-replica risk is accepted in writing (T1/T2) | P0 | `deployments.replicas_min`, risk acceptance record |
| A3 | PostgreSQL has a synchronous replica and tested automated failover; RPO/RTO targets written down | P0 | Failover test log |
| A4 | Redis outage behaviour is documented and tested. Implemented in the gateway: `ResilientStore` falls back to an in-process store, so limits become per-replica (inference stays up, global limits weaken by the replica count). Confirm that is the accepted policy; consider a lower per-replica cap while degraded | P0 | Chaos test: kill Redis during load |
| A5 | Behaviour with Postgres down is tested: inference continues from cached keys/routes for the cache TTL; control plane returns 503 with `Retry-After`; usage rows buffered then spilled/dropped with a counter | P0 | Chaos test |
| A6 | Graceful shutdown: in-flight SSE streams drain (`terminationGracePeriodSeconds` + `preStop`); no 5xx spike on deploy | P0 | Rollout test under load |
| A7 | Gateway retry only before first byte; client disconnect cancels upstream | P0 | Test with killed client / killed vLLM pod |
| A8 | Timeouts, connection limits and circuit breaker toward each vLLM pool are configured; a slow replica cannot exhaust gateway connections | P1 | Load test with one slow upstream |
| A9 | Per-org concurrency cap prevents one tenant occupying all vLLM sequence slots | P1 | Noisy-neighbour test |
| A10 | Node drain procedure tested for GPU nodes (no outage with >= 2 replicas) | P1 | Runbook + test |
| A11 | Multi-zone/rack placement verified with `topologySpreadConstraints` | P1 | `kubectl get pods -o wide` |
| A12 | Region failover tested (T3 + DR) | P2 | Game-day report |

## 2. Data protection and recovery

| ID | Item | Pri | Evidence |
|---|---|---|---|
| D1 | Continuous WAL archiving + daily base backups to off-site, encrypted storage | P0 | Backup status, restore log |
| D2 | **Restore drill performed** within the last quarter: restore to scratch, run RLS tests, verify audit chain | P0 | Dated drill report |
| D3 | Object storage versioning on `models`, `datasets`; Object Lock on audit anchors/reports; cross-site replication (T3) | P0 | Bucket config |
| D4 | Qdrant snapshots scheduled; re-ingestion procedure tested for one index | P1 | Drill report |
| D5 | `usage_logs` partitions exist for the next >= 2 months; alert on `usage_logs_default` rows > 0; retention job exports then drops | P0 | Partition list, alert |
| D6 | Retention and erasure jobs implemented and tested (org delete, user erasure across Postgres, S3, vector DB, MLflow) | P1 | Erasure test report |
| D7 | Migrations run via pre-upgrade hook with the migration role; expand/contract compatibility test in CI | P0 | CI run |
| D8 | Database roles: `slm_app` is not owner/superuser/`BYPASSRLS`; migration role separate; credentials rotated independently | P0 | `\du` output |
| D9 | PgBouncer in transaction mode; connection budget computed (`max_connections` >= pools x replicas) | P1 | Config |
| D10 | Disk-full protections: PVC usage alerts at 70/85%, WAL archive failure alert | P0 | Alert rules |

## 3. Tenancy and isolation (functional proof)

| ID | Item | Pri | Evidence |
|---|---|---|---|
| T1 | Automated test proves cross-tenant SELECT/INSERT/UPDATE/DELETE are denied on every tenant table as `slm_app` | P0 | CI job ([08](08-cicd.md)) |
| T2 | Every repository call path uses `tenant_tx`; `bypass=True` call sites are enumerated and reviewed | P0 | Code audit list |
| T3 | Cross-org id access returns 404 in API tests for every resource type | P0 | API test suite |
| T4 | Object-storage prefixes enforced by scoped credentials/policies, not by app logic only | P1 | Policy files |
| T5 | Vector DB queries always include server-added `org_id` + `index_id` filter; test with two tenants' near-identical documents | P0 (when RAG ships) | Test |
| T6 | NetworkPolicies default-deny verified (vLLM unreachable from other namespaces; trainer cannot reach Postgres) | P0 | `kubectl exec` curl tests |
| T7 | Quotas (RPM, TPM, monthly tokens, budget, GPU-hours) enforced and tested; `insufficient_quota` vs `rate_limit_exceeded` distinct | P0 | Tests |
| T8 | Shared-endpoint side-channel risk (prefix cache) reviewed per tier; per-org cache salt or `private` class for sensitive tenants | P1 | Decision record |
| T9 | Chart NetworkPolicy `externalEgress` rules tightened with `ipBlock`/`namespaceSelector` (defaults allow Postgres, Redis, HTTPS/S3, MLflow/Qdrant ports to any destination) | P0 | Rendered policies, connectivity tests |

## 4. Performance and capacity

| ID | Item | Pri | Evidence |
|---|---|---|---|
| C1 | **Benchmark on target hardware** with your real prompt/completion length distribution: TTFT p50/p95, inter-token latency, tokens/s per replica vs concurrency; replace the planning values in [01](01-architecture.md) section 9 | P0 | Benchmark report in `reports/` |
| C2 | Gateway load test: sustained target rps with long-lived streams (concurrency = rps x mean duration) and no growth in memory/FDs; soak 24 h | P0 | Test report |
| C3 | Autoscaling (HPA/KEDA) tested: scale-up latency vs model load time, scale-down stabilisation | P1 | Test report |
| C4 | Capacity headroom: peak utilisation target <= 60% (see [01](01-architecture.md)); capacity plan reviewed monthly | P1 | Dashboard, plan |
| C5 | Fit check in deployment service prevents unschedulable/OOM deployments (weights + KV vs node memory; Spark unified memory budget) | P0 | Tests |
| C6 | Training/serving co-location policy enforced on Spark (T1/T2) | P0 | Policy test |
| C7 | Large-payload and long-context abuse tests (max body size, `max_model_len`, `max_tokens` clamp) | P1 | Tests |
| C8 | Postgres query plan review for top 10 list queries at 100x data volume; missing indexes from [02](02-database-schema.md) section 7 added | P1 | EXPLAIN reports |

## 5. Observability and operations

| ID | Item | Pri | Evidence |
|---|---|---|---|
| O1 | Dashboards and alerts from [12](12-monitoring-design.md) deployed; every P1 alert has a runbook link | P0 | Grafana, Alertmanager config |
| O2 | SLOs defined and burn-rate alerts live (availability, TTFT, error budget) | P0 | Alert rules |
| O3 | Logs structured JSON with `request_id`, `org_id`; **no prompts/completions/secrets in logs** (verified by test) | P0 | Log sample audit |
| O4 | Traces propagate gateway -> vLLM/services with `X-Request-ID`; sampling configured | P1 | Tempo query |
| O5 | On-call rotation, escalation, status page and incident template exist | P0 | Docs |
| O6 | Runbooks: Postgres failover/restore, Redis loss, GPU node loss, vLLM crash loop, key compromise, tenant abuse, partition failure, certificate expiry | P0 | Runbook set |
| O7 | Alert on GPU health: DCGM Xid errors, ECC, thermal throttling, power; on GB10 note which fields are unavailable | P1 | Alert rules |
| O8 | Cost dashboards (GPU-hours, tokens by org) reconcile with `usage_logs` within 1% | P1 | Reconciliation query |
| O9 | Synthetic probe: a canary key calling each production alias every minute from outside the cluster | P1 | Probe config |
| O10 | Time sync (chrony/NTP) on all nodes; clock skew alert (JWT, audit timestamps, hash chain ordering depend on it) | P0 | Alert |
| O11 | OTel collector has a real trace exporter configured (the chart's default sends traces to a `debug` exporter only, so no traces are stored) and a metrics/logs path to Prometheus/Loki | P1 | Collector config, Tempo query |

## 6. Release engineering

| ID | Item | Pri | Evidence |
|---|---|---|---|
| R1 | CI gates from [08](08-cicd.md) are required checks on `main`; CODEOWNERS on migrations, `slm_common`, deploy, OpenAPI | P0 | Branch protection export |
| R2 | Images signed, SBOM attached, pinned by digest in prod; admission policy verifies signatures | P1 | cosign verify |
| R3 | Rollback tested for the platform release (Helm/GitOps) | P0 | Test log |
| R4 | vLLM image version pinned; upgrade procedure and smoke suite exist; tenant deployments not restarted by platform releases | P0 | Pin, procedure |
| R5 | Config drift detection: Postgres `deployments` vs actual Kubernetes workloads (reconcile loop alerts on mismatch) | P1 | Alert |
| R6 | Dependency update cadence (Renovate/Dependabot) and CVE SLA (critical 7 d, high 30 d: **Assumption**) | P1 | Policy |
| R7 | Multi-arch image set verified on the actual Spark for each release | P0 (T1/T2) | Nightly GPU job |

## 7. Compliance and governance

| ID | Item | Pri | Evidence |
|---|---|---|---|
| G1 | Model licences reviewed for every catalogue model; usage restrictions (Llama, Gemma acceptable-use terms, attribution, MAU thresholds) surfaced in UI and customer terms | P0 | Licence register ([14](14-roadmap.md#5-model-licence-caveats)) |
| G2 | Data processing terms, sub-processor list, retention schedule published | P0 | Documents |
| G3 | HIPAA/GDPR control mapping ([11](11-security-checklist.md)) reviewed; operator actions (BAA, DPIA, pen test) tracked with owners and dates | P0 (regulated tenants) | Tracker |
| G4 | Audit log chain verification job running; external anchor in Object Lock storage | P1 | Job logs |
| G5 | Access reviews for platform admins and DB/superuser credentials quarterly | P1 | Review record |
| G6 | Break-glass procedure for `platform_admin`/DB access, audited and alerting | P1 | Procedure |

## 8. Customer onboarding gates

| ID | Item | Pri |
|---|---|---|
| U1 | Default quotas conservative (RPM/TPM) and budget alert emails to `org_admin` at 50/80/100% | P0 |
| U2 | Org creation flow sets region (`data_region`), plan, default quota row (a missing `quotas` row must not mean unlimited) | P0 |
| U3 | Customer docs: base URL, SDK examples, rate-limit and error semantics ([03](03-api-spec.md)), data-handling statement | P0 |
| U4 | Abuse handling: key revocation latency (<= 60 s cache TTL or push invalidation), org suspension switch | P0 |
| U5 | Status page and deprecation policy published | P1 |

## 9. Go-live sign-off

All P0 rows are "done" with evidence links, the blockers table in section 0 is empty, and the following sign off in writing:
platform engineering (A, D, R), security (T, section 11), SRE/on-call (O), product/legal (G, U), finance (cost model in [13](13-cost-estimation.md) matches contracts).
