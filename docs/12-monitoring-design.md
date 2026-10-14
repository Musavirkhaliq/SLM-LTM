# 12 - Monitoring Design

Stack: Prometheus (+ Alertmanager), Grafana, Loki, Tempo, OpenTelemetry Collector, DCGM exporter (from the GPU Operator or standalone), kube-state-metrics,
node-exporter, postgres/redis exporters. Every service exposes `/healthz`, `/readyz`, `/metrics`, emits structlog JSON logs and OTLP traces (CONTRACT.md section 2).

Status: the shared `slm_common.telemetry` / `logging` helpers exist; the `monitoring` service is a typed stub (roll-ups, WebSocket stream, partition maintenance are not built).
Metric names marked "Implemented" were verified in `slm_common.telemetry` and the gateway source (2026-10-14); everything else under "gateway" and "control plane" is the **target catalogue** and must be reconciled with the code as services are completed.
vLLM and DCGM metric names are the exporters' own and are given as they appear in current upstream releases.
**vLLM renames metrics between releases** (for example the KV-cache gauge); pin the vLLM version and check `/metrics` of the pinned build, as the dashboards in `deploy/grafana/` must match the pin.

## 1. Principles

1. **Per-deployment and per-route metrics in Prometheus; per-org analytics from `usage_logs`.** An `org_id` Prometheus label with thousands of orgs would explode cardinality (histograms multiply it by buckets). Org-level views (usage, latency, errors, cost) are computed by the monitoring service from Postgres, and tenants only ever see their own rows.
2. **Tenants never query Prometheus directly.** vLLM metrics carry no org label and shared deployments mix tenants; the monitoring service filters and aggregates to what the caller may see (own deployments, own usage; for shared deployments only the caller's slice).
3. **Alert on symptoms (SLO burn) first, causes second** (saturation, GPU health).
4. **No content in telemetry.** Prompts, completions and secrets never appear in metrics, logs or spans.
5. **Everything alerting has a runbook link** ([10](10-production-checklist.md) O6).

## 2. Metrics catalogue

### 2.1 vLLM (one scrape target per pod; label `model_name`, `engine`)

| Metric | Type | Meaning / use |
|---|---|---|
| `vllm:num_requests_running` | gauge | Sequences in the running batch; compare with `max_num_seqs` |
| `vllm:num_requests_waiting` | gauge | Queue depth. **Primary autoscaling and saturation signal** |
| `vllm:kv_cache_usage_perc` | gauge 0-1 | KV-cache utilisation (V1 engine; older releases: `vllm:gpu_cache_usage_perc`) |
| `vllm:num_preemptions_total` | counter | Sequences evicted for lack of KV space: rising = under-provisioned `max_model_len`/memory or too much concurrency |
| `vllm:prefix_cache_queries_total`, `vllm:prefix_cache_hits_total` | counters | Prefix-cache hit rate = hits/queries (older: `vllm:gpu_prefix_cache_hit_rate` gauge) |
| `vllm:time_to_first_token_seconds` | histogram | TTFT, server side (excludes network and gateway) |
| `vllm:inter_token_latency_seconds` | histogram | Per-token latency in newer releases; older: `vllm:time_per_output_token_seconds` |
| `vllm:e2e_request_latency_seconds` | histogram | Total request time in the engine |
| `vllm:request_queue_time_seconds` | histogram | Time waiting before scheduling |
| `vllm:request_prefill_time_seconds`, `vllm:request_decode_time_seconds`, `vllm:request_inference_time_seconds` | histograms | Phase breakdown |
| `vllm:prompt_tokens_total`, `vllm:generation_tokens_total` | counters | Throughput: tokens/s via `rate()` |
| `vllm:request_prompt_tokens`, `vllm:request_generation_tokens` | histograms | Length distributions (feed capacity model in [01](01-architecture.md)) |
| `vllm:request_success_total{finished_reason}` | counter | `stop`, `length`, `abort`; high `length` share means truncated answers |
| `vllm:iteration_tokens_total` | histogram | Tokens per engine step (batch composition) |
| `vllm:spec_decode_num_accepted_tokens_total`, `vllm:spec_decode_num_draft_tokens_total` | counters | Speculative decoding acceptance rate = accepted/draft |
| `vllm:lora_requests_info` | gauge (info) | Running/waiting adapters per pod |
| `vllm:cache_config_info` | info | `block_size`, `num_gpu_blocks`, `gpu_memory_utilization`: use to compute KV token capacity |

Derived (recording rules):

```
slm:vllm_ttft_p95:5m   = histogram_quantile(0.95, sum by (le, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))
slm:vllm_itl_p95:5m    = histogram_quantile(0.95, sum by (le, model_name) (rate(vllm:inter_token_latency_seconds_bucket[5m])))
slm:vllm_gen_tps:1m    = sum by (model_name) (rate(vllm:generation_tokens_total[1m]))
slm:vllm_prefix_hit:5m = sum by (model_name) (rate(vllm:prefix_cache_hits_total[5m])) / sum by (model_name) (rate(vllm:prefix_cache_queries_total[5m]))
```

### 2.2 GPU (DCGM exporter; labels `gpu`, `UUID`, `modelName`, `Hostname`, plus `pod`/`namespace` when pod mapping is enabled)

| Metric | Use |
|---|---|
| `DCGM_FI_DEV_GPU_UTIL` | Coarse utilisation (percent of time a kernel ran); not a saturation measure on its own |
| `DCGM_FI_PROF_GR_ENGINE_ACTIVE`, `DCGM_FI_PROF_SM_ACTIVE`, `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` | Profiling-based activity; more meaningful (enable the profiling counters in the exporter CSV; some add overhead) |
| `DCGM_FI_PROF_DRAM_ACTIVE` | Memory-bandwidth activity: decode is bandwidth-bound, so this approaches 1 when a replica is saturated |
| `DCGM_FI_DEV_FB_USED`, `DCGM_FI_DEV_FB_FREE` | Framebuffer memory (**may be unavailable on GB10 unified memory: verify; use node memory and vLLM KV metrics instead**) |
| `DCGM_FI_DEV_POWER_USAGE`, `DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION` | Power (W) and cumulative energy (mJ): input to the energy cost line in [13](13-cost-estimation.md) |
| `DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_MEMORY_TEMP` | Thermals (critical for a desktop-class Spark in a warm room) |
| `DCGM_FI_DEV_SM_CLOCK`, `DCGM_FI_DEV_MEM_CLOCK`, `DCGM_FI_DEV_CLOCK_THROTTLE_REASONS` | Throttling detection |
| `DCGM_FI_DEV_XID_ERRORS` | Last Xid error code (non-zero = driver-reported GPU fault) |
| `DCGM_FI_DEV_ECC_DBE_VOL_TOTAL` (where ECC is exposed) | Uncorrectable ECC errors |
| `DCGM_FI_PROF_NVLINK_TX_BYTES` / `RX_BYTES`, `DCGM_FI_PROF_PCIE_TX_BYTES` | Interconnect (H100 multi-GPU) |

Which fields exist differs by GPU and driver; the exporter silently omits unsupported ones. The GPU dashboard must tolerate missing series (show "N/A").

### 2.3 Gateway

Verified against `services/gateway` and `slm_common.telemetry` on 2026-10-14 ("Implemented"); the rest is the target catalogue ("Target").

| Metric | Type | Labels | Status | Meaning |
|---|---|---|---|---|
| `slm_gateway_inference_requests_total` | counter | `kind` (chat/completions/embeddings), `status` (HTTP status as a string) | Implemented | Inference requests by outcome; error ratio = `status=~"5.."` share |
| `slm_gateway_inference_latency_seconds` | histogram | `kind` | Implemented | End-to-end latency (buckets 0.05 s to 300 s) |
| `slm_gateway_ttft_seconds` | histogram | none | Implemented | Request received to first streamed event (client-perceived TTFT; buckets 0.05 s to 30 s) |
| `slm_gateway_tokens_total` | counter | `type` (`prompt`/`completion`) | Implemented | Metered tokens |
| `slm_gateway_usage_dropped_total` | counter | `reason` (`queue_full`/`db_error`) | Implemented | Usage rows lost: **billing-impacting** |
| `slm_http_requests_total`, `slm_http_request_duration_seconds`, `slm_http_in_flight_requests` | counter, histogram, gauge | `service`, `method`, `route` (template), `status` | Implemented (all services via `slm_common.telemetry`) | Generic per-route HTTP metrics; in-flight includes open SSE streams for the gateway |
| `slm_gateway_ratelimited_total` | counter | `limit` (`rpm`/`tpm`/`quota`) | Target | 429s by reason (until then: `status="429"` on the request counter) |
| `slm_gateway_auth_failures_total` | counter | `reason` (`missing`/`invalid`/`revoked`/`expired`) | Target | Key stuffing or leaked/revoked keys (until then: `status="401"`) |
| `slm_gateway_keycache_total` | counter | `result` (`hit`/`miss`) | Target | Key cache effectiveness (expect > 95% hits) |
| `slm_gateway_upstream_errors_total` | counter | `reason` | Target | Upstream problems by cause |
| `slm_gateway_upstream_retries_total` | counter | | Target | Pre-first-byte retries |
| `slm_gateway_usage_queue_depth` | gauge | | Target | Pending usage rows (queue bound is 50,000 by default) |
| `slm_gateway_usage_flush_seconds` | histogram | | Target | Batch insert latency |

Labels deliberately exclude `org_id`, `api_key_id`, `endpoint_alias` and `deployment_id` (cardinality). For a per-deployment view use the vLLM series and `usage_logs`.

### 2.4 Control-plane services (target names)

| Metric | Meaning |
|---|---|
| `slm_http_requests_total{service,method,route,status}`, `slm_http_request_duration_seconds{service,method,route}` | Implemented: shared metrics middleware (route template, never raw path) |
| `slm_db_tx_duration_seconds{service}`, `slm_db_pool_in_use{service}` | `tenant_tx` latency and pool saturation |
| `slm_deployments{status}` | Deployments by state (gauge from the deployment service) |
| `slm_deployment_reconcile_drift` | Count of Postgres vs Kubernetes mismatches |
| `slm_deployment_provision_seconds` | Time from `pending` to `running` |
| `slm_finetune_jobs{status}`, `slm_finetune_queue_oldest_age_seconds` | Job states and head-of-line wait |
| `slm_finetune_tokens_processed_total` | Training throughput (feeds cost) |
| `slm_rag_ingest_documents_total{status}`, `slm_rag_ingest_seconds`, `slm_rag_query_seconds{stage}` | Stage in `embed`, `retrieve`, `rerank`, `generate` |
| `slm_eval_runs{status}`, `slm_eval_judge_calls_total` | Evaluation activity |
| `slm_cost_rollup_lag_seconds` | Age of the last successful roll-up |
| `slm_usage_logs_default_rows` | Rows in the default partition (must be 0) |
| `slm_audit_chain_verify_failures_total`, `slm_audit_chain_last_verified_timestamp_seconds` | Audit integrity |

### 2.5 Infrastructure and data stores

| Source | Key series |
|---|---|
| kube-state-metrics / cAdvisor | `kube_pod_container_status_restarts_total`, `kube_pod_status_phase`, `kube_deployment_status_replicas_available`, `container_memory_working_set_bytes`, `kube_pod_container_status_last_terminated_reason` (OOMKilled) |
| node-exporter | `node_memory_MemAvailable_bytes` (the Spark capacity signal), `node_filesystem_avail_bytes`, `node_timex_offset_seconds`, `node_hwmon_temp_celsius` |
| postgres_exporter / CNPG | `pg_stat_activity_count`, `pg_replication_lag_seconds`, `pg_stat_archiver_failed_count`, `pg_database_size_bytes`, `pg_stat_database_xact_rollback` |
| redis_exporter | `redis_memory_used_bytes`, `redis_connected_clients`, `redis_rejected_connections_total`, `redis_evicted_keys_total` |
| Qdrant `/metrics` | REST/gRPC request-duration histograms, collection and point counts |
| MinIO `/minio/v2/metrics/cluster` | Drive/node online counts, capacity, API errors |
| cert-manager | `certmanager_certificate_expiration_timestamp_seconds` |
| ingress-nginx | `nginx_ingress_controller_requests`, upstream response time |

## 3. SLOs

Measured over a rolling 30 days. T3 = H100 production; T1/T2 = Spark tiers (lower targets, no HA, see [06](06-infrastructure.md)). Targets are **Assumptions** to agree with customers.

| # | SLI (good events / valid events) | T3 target | T1/T2 target |
|---|---|---|---|
| S1 | Inference availability: `/v1/*` requests not ending in 5xx (429 and 4xx excluded as client-attributable; 503 `no_healthy_deployment` counts as bad) | 99.9% | 99.0% |
| S2 | TTFT: requests with TTFT <= 1.0 s for prompts <= 2,048 tokens (gateway-measured) | 95% | 90% (<= 2.0 s) |
| S3 | Streaming speed: inter-token latency p95 <= 50 ms (>= 20 tokens/s per stream) | 95% | <= 100 ms |
| S4 | Queue wait: `vllm:request_queue_time_seconds` <= 0.5 s | 99% | 95% |
| S5 | Control-plane availability: `/api/v1/*` non-5xx | 99.9% | 99.0% |
| S6 | Control-plane latency: GET p95 <= 300 ms, mutating p95 <= 1 s | 99% | 95% |
| S7 | Metering completeness: usage rows / successful `/v1/*` requests | 99.99% | 99.9% |
| S8 | Deployment provisioning: `pending` to `running` <= 10 min | 95% | 90% |
| S9 | Job start: `queued` to `running` <= 15 min when GPUs are free | 95% | 90% |

Error budget for S1 at 99.9%: 43.2 minutes of full outage per 30 days. Budget policy: if > 50% consumed with > 2 weeks left, freeze risky releases; if exhausted, only reliability work ships.

### 3.1 Burn-rate alerts (multi-window, multi-burn)

Recording rule (gateway; `good` = non-5xx):

```
slm:gateway_error_ratio:rate5m  = sum(rate(slm_gateway_inference_requests_total{status=~"5.."}[5m]))
                                  / sum(rate(slm_gateway_inference_requests_total[5m]))
# same with [30m], [1h], [2h], [6h], [1d], [3d]
```

| Alert | Condition (for SLO 99.9%, budget = 0.001) | Severity |
|---|---|---|
| `InferenceSLOBurnFast` | `ratio1h > 14.4 * 0.001` **and** `ratio5m > 14.4 * 0.001` | page |
| `InferenceSLOBurnMedium` | `ratio6h > 6 * 0.001` and `ratio30m > 6 * 0.001` | page |
| `InferenceSLOBurnSlow` | `ratio1d > 3 * 0.001` and `ratio2h > 3 * 0.001` | ticket |
| `InferenceSLOBurnDrift` | `ratio3d > 1 * 0.001` | ticket |

Latency SLOs use the same shape with the "bad" ratio = `1 - (rate(..._bucket{le="1"}) / rate(..._count))` for TTFT.

## 4. Alert rules

Severities: **page** (human now), **ticket** (business hours), **info** (dashboard). `for:` durations avoid flapping. Runbook annotations point to `docs/runbooks/<name>` (to be written; [10](10-production-checklist.md) O6).

| Alert | Expression (abridged) | For | Sev |
|---|---|---|---|
| `NoHealthyRoute` | `sum(rate(slm_gateway_inference_requests_total{status="503"}[2m])) > 0` (a `no_healthy_deployment` code label would make this exact) | 2m | page |
| `GatewayHigh5xx` | `slm:gateway_error_ratio:rate5m > 0.05` | 5m | page |
| `GatewayDown` | `up{job="gateway"} == 0` for all replicas or `kube_deployment_status_replicas_available{deployment="gateway"} < 2` | 2m | page |
| `UsageRowsDropped` | `increase(slm_gateway_usage_dropped_total[5m]) > 0` | 0m | page |
| `UsageQueueBacklog` | `slm_gateway_usage_queue_depth > 5000` (Target metric) | 5m | ticket |
| `UsageDefaultPartitionNotEmpty` | `slm_usage_logs_default_rows > 0` | 10m | ticket |
| `AuthFailureSpike` | `sum(rate(slm_gateway_inference_requests_total{status="401"}[5m])) > 50` | 5m | ticket |
| `VllmQueueBacklog` | `max by (model_name,pod) (vllm:num_requests_waiting) > 10` | 5m | ticket |
| `VllmQueueSaturated` | `max by (model_name,pod) (vllm:num_requests_waiting) > 50` | 2m | page |
| `VllmKvCacheFull` | `max by (pod) (vllm:kv_cache_usage_perc) > 0.95` | 10m | ticket |
| `VllmPreempting` | `sum by (pod) (rate(vllm:num_preemptions_total[5m])) > 0.1` | 10m | ticket |
| `VllmTTFTHigh` | `slm:vllm_ttft_p95:5m > 2` | 10m | ticket |
| `VllmPodNotReady` | `kube_pod_status_ready{namespace="slm-serving",condition="true"} == 0` | 5m | ticket (page if last replica of a production alias) |
| `VllmDown` | `up{job="vllm"} == 0` | 3m | page |
| `GpuXidError` | `DCGM_FI_DEV_XID_ERRORS > 0` | 1m | page |
| `GpuEccUncorrectable` | `increase(DCGM_FI_DEV_ECC_DBE_VOL_TOTAL[10m]) > 0` | 0m | page |
| `GpuTemperatureHigh` | `DCGM_FI_DEV_GPU_TEMP > 85` | 5m | ticket (page > 92) |
| `GpuThrottling` | `DCGM_FI_DEV_CLOCK_THROTTLE_REASONS > 0` and `slm:vllm_gen_tps:1m` below baseline | 15m | ticket |
| `GpuUnderused` | `avg_over_time(DCGM_FI_DEV_GPU_UTIL[6h]) < 5` on a `dedicated` node | 6h | info (cost) |
| `NodeMemoryLow` (Spark) | `node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes < 0.08` | 5m | page (OOM risk on unified memory) |
| `PostgresDown` | `pg_up == 0` or CNPG cluster not ready | 1m | page |
| `PostgresReplicationLag` | `pg_replication_lag_seconds > 30` | 5m | ticket |
| `PostgresConnectionsHigh` | `sum(pg_stat_activity_count) / pg_settings_max_connections > 0.8` | 5m | ticket |
| `WalArchiveFailing` | `increase(pg_stat_archiver_failed_count[15m]) > 0` | 0m | page |
| `PostgresDiskFilling` | `predict_linear(node_filesystem_avail_bytes{mountpoint=~"/var/lib/.*pg.*"}[6h], 24*3600) < 0` | 30m | page |
| `RedisDown` | `redis_up == 0` | 1m | page |
| `RedisMemoryHigh` | `redis_memory_used_bytes / redis_memory_max_bytes > 0.85` | 10m | ticket |
| `CertificateExpiring` | `certmanager_certificate_expiration_timestamp_seconds - time() < 14*86400` | 1h | ticket |
| `PodCrashLooping` | `increase(kube_pod_container_status_restarts_total[30m]) > 3` | 0m | ticket |
| `PodOOMKilled` | `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1` | 0m | ticket |
| `ClockSkew` | `abs(node_timex_offset_seconds) > 0.05` | 5m | ticket |
| `AuditChainBroken` | `increase(slm_audit_chain_verify_failures_total[1h]) > 0` | 0m | page |
| `AuditChainNotVerified` | `time() - slm_audit_chain_last_verified_timestamp_seconds > 2*86400` | 0m | ticket |
| `DeploymentDrift` | `slm_deployment_reconcile_drift > 0` | 15m | ticket |
| `DeploymentProvisionSlow` | `slm_deployments{status="provisioning"} > 0` with age > 15 min | 0m | ticket |
| `TrainingQueueStuck` | `slm_finetune_queue_oldest_age_seconds > 1800` while a GPU is free | 10m | ticket |
| `CostRollupLag` | `slm_cost_rollup_lag_seconds > 2*86400` | 0m | ticket |
| `BudgetThreshold` (tenant-facing) | Evaluated from `cost_records` vs `quotas.monthly_budget_usd` at 50/80/100% | - | notification to `org_admin` (needs notification-channel schema, gap in [02](02-database-schema.md)) |

Routing: pages to on-call rota; tickets to the platform board; tenant-facing notifications (budget, quota) go through the application, not Alertmanager.

## 5. Dashboard layouts

Provisioned from `deploy/grafana/` (JSON) and mirrored by the web **Monitoring** page, whose data comes from the monitoring service API, not from Grafana. Grafana is for operators.

### 5.1 Platform overview (operators)

```
+--------------------------------------------------------------------------------+
| SLO strip: S1 avail 99.97% (budget 71% left) | S2 TTFT 96.1% | S7 metering 100% |
+----------------------+----------------------+----------------------------------+
| Requests/s by kind   | 5xx / 429 ratio      | In-flight (streams)              |
| Tokens/s in vs out   | Gateway latency p50/p95/p99 | TTFT p50/p95/p99 (gateway)   |
+----------------------+----------------------+----------------------------------+
| Healthy replicas per alias class | GPU util + power per node | Active alerts   |
+--------------------------------------------------------------------------------+
```

### 5.2 Inference (per deployment / model)

```
+--------------------------------------------------------------------------------+
| Variables: deployment [v]  pod [All v]  window                                  |
+----------------------+----------------------+----------------------------------+
| Running vs max_num_seqs | Waiting (queue)    | KV-cache usage % + preemptions  |
| TTFT p50/95/99          | Inter-token p95    | E2E latency p95                 |
| Prompt / generation tokens/s | Prefix-cache hit % | Spec-decode acceptance     |
| Request length histograms (prompt, generation) | finished_reason mix           |
| Queue time vs prefill time vs decode time (stacked)                             |
+--------------------------------------------------------------------------------+
```

### 5.3 GPU fleet

Per GPU/node: utilisation and tensor-pipe activity, DRAM active, power and energy, temperature, clocks and throttle reasons, Xid/ECC, framebuffer (N/A on GB10), plus node `MemAvailable` (Spark). Rows per node pool; heat-map of utilisation across the fleet.

### 5.4 Tenants and usage (Grafana Postgres datasource on the read replica, platform_admin only)

Top-10 orgs by tokens and by 429s, per-org p95 latency from `usage_logs` (`latency_ms`, `ttft_ms`), error rate by org and route label, cache-hit tokens (`cached_tokens`), A/B and canary comparison by `route_label` (latency, error rate, tokens).

### 5.5 Other dashboards

| Dashboard | Panels |
|---|---|
| Fine-tuning | Jobs by state, queue age, GPU util during jobs, tokens/s per job, failures by reason, cost per job |
| RAG | Ingest rate and failures, stage latencies (embed/retrieve/rerank/generate), vector DB latency, index sizes |
| Data stores | Postgres (connections, TPS, lag, WAL, bloat, partition sizes), Redis, Qdrant, MinIO capacity |
| Capacity and cost | Utilisation vs the 60% target, GPU-hours by org (from `cost_records`), idle dedicated GPUs, forecast |
| Security | Auth failures, 401/403 rates, key rotations/revocations, platform-admin actions, audit-chain status |

### 5.6 What tenants see (web Monitoring page)

Overview and per-deployment time series for resources they own, built from `usage_logs` (their rows only) and, for their private/dedicated deployments, vLLM series filtered by deployment.
Shared endpoints show only the tenant's own traffic, never other tenants' queue depth. Keys: RPS, TTFT p50/p95/p99, ITL, tokens/s, queue depth (own deployments), KV usage (own), error rate, 429 rate.

## 6. Tracing

OpenTelemetry via OTLP to the Collector (`SLM_OTLP_ENDPOINT`), stored in Tempo. W3C `traceparent` propagated gateway to services and to vLLM (vLLM supports OTLP trace export via its `--otlp-traces-endpoint` flag; verify per version).

| Span | Kind | Key attributes (no content) |
|---|---|---|
| `gateway.request` | server | `http.method`, `http.route`, `http.status_code`, `slm.request_id`, `slm.org_id`, `slm.project_id`, `slm.endpoint_alias`, `slm.route_label`, `slm.stream`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` (OTel GenAI semantic conventions; experimental, names may change) |
| `gateway.auth` | internal | `slm.keycache.hit`, `slm.key.scope`, outcome |
| `gateway.ratelimit` | internal | `slm.limit.rpm_remaining`, `slm.limit.tpm_remaining`, outcome |
| `gateway.route` | internal | `slm.endpoint_id`, chosen `slm.deployment_id`, candidates count |
| `gateway.upstream` | client | `server.address`, retry count; event `first_token` (gives TTFT), event `usage` |
| `gateway.usage_enqueue` | internal | queue depth |
| vLLM `llm_request` (upstream-defined) | server | engine timings if enabled |
| `<svc>.db.tx` | client | `slm.org_id`, statement kind (not text with values), duration |
| `rag.query` -> `rag.embed`, `rag.retrieve`, `rag.rerank`, `rag.generate` | internal/client | `slm.index_id`, `top_k`, candidate counts, scores summary |
| `finetune.job.create` -> `k8s.job.create` | internal/client | `slm.job_id`, `gpu_count` |
| `deployment.reconcile` | internal | drift count, actions |

Sampling: head sampling 5% in production; tail sampling in the Collector keeps 100% of errors, requests slower than p99 or 10 s, and all 429/503; exemplars link latency histograms to trace ids.
`slm.org_id` as a span attribute is acceptable (traces are not metrics); apply an attribute allow-list processor to guarantee that no content attributes can ever be exported.

## 7. Log schema

One JSON object per line (structlog), shipped by the node agent (Promtail/Alloy/Fluent Bit) to Loki. Loki labels (low cardinality only): `service`, `env`, `namespace`, `level`. Everything else is a JSON field, not a label.

| Field | Type | Notes |
|---|---|---|
| `timestamp` | string | RFC 3339 UTC with milliseconds |
| `level` | string | `debug`/`info`/`warning`/`error`/`critical` |
| `service` | string | e.g. `gateway` |
| `version` | string | Git SHA of the image |
| `event` | string | Short stable message key (structlog event name) |
| `request_id` | string | From `X-Request-ID` |
| `trace_id`, `span_id` | string | For log-trace correlation |
| `org_id` | string (uuid) | When a tenant context exists |
| `user_id` / `api_key_id` / `actor_type` | string | Identifiers only; never key material or email |
| `project_id` | string (uuid) | Optional |
| `http.method`, `http.route` (template), `http.status` | | Never raw query strings containing tokens |
| `duration_ms` | number | |
| `deployment_id`, `endpoint_alias`, `route_label` | string | Gateway/deployment logs |
| `prompt_tokens`, `completion_tokens`, `cached_tokens`, `ttft_ms`, `stream`, `upstream_status`, `retries` | | Gateway access log |
| `error.type`, `error.message` | string | Message scrubbed of values; no stack traces in `warning` |
| `exception` | string | Stack trace on `error` level only |

Example gateway access line:

```json
{"timestamp":"2026-10-14T10:42:03.114Z","level":"info","service":"gateway","version":"3f2a9c1","event":"request_completed",
 "request_id":"9f3c2a1e7b","trace_id":"4bf92f3577b34da6a3ce929d0e0e4736","org_id":"6b1f...","api_key_id":"c2d4...","project_id":"a91e...",
 "http.method":"POST","http.route":"/v1/chat/completions","http.status":200,"duration_ms":1840,"endpoint_alias":"support",
 "route_label":"canary","deployment_id":"e7a0...","stream":true,"prompt_tokens":812,"completion_tokens":243,"cached_tokens":512,"ttft_ms":211,"retries":0}
```

Rules enforced by a unit test and a CI log-scrubbing job: never log `Authorization`, API keys, refresh tokens, passwords, JWTs, request or response bodies, prompts, completions, dataset rows, document text, `mfa` material.
Health-check and `/metrics` requests are not access-logged. Retention: 14-30 days hot in Loki, 90-365 days in object storage if required by policy; **audit trail is the `audit_logs` table, not application logs.**

## 8. Retention and cardinality budget

| Data | Retention | Notes |
|---|---|---|
| Prometheus TSDB | 15 days local; optional remote-write (Mimir/Thanos) 13 months at 5 m downsampling | Budget: < 1 M active series per Prometheus; histogram bucket sets standardised |
| Loki | 14-30 days (+ cold storage per policy) | |
| Tempo | 7 days | Tail-sampled |
| `usage_logs` | 90 days hot; export to Parquet | See [02](02-database-schema.md) s6 |
| Alerts history | 1 year (Alertmanager + ticket system) | |

Series-count estimate for a 4-GPU-node, 40-deployment cluster: vLLM ~ 60 series per pod (with histograms ~15 buckets x 6 histograms) x 80 pods = ~5k; DCGM ~ 40 x 32 GPUs = ~1.3k; gateway ~ 400; control plane ~ 3k; infra ~ 20k. Total below 30k series: comfortable. The failure mode to avoid is adding `org_id`/`api_key_id` labels.

## 9. Monitoring service responsibilities (target)

1. Maintain `usage_logs` partitions (`pg_partman` or SQL) and the `slm_usage_logs_default_rows` gauge.
2. Serve `GET /api/v1/monitoring/overview` and `/deployments/{id}`: SQL aggregates over `usage_logs` (`date_bin` buckets, `percentile_cont` for latency) filtered by the caller's org, joined with Prometheus range queries for deployments the org owns.
3. Push 1 Hz snapshots on `/api/v1/monitoring/stream` (WebSocket) per subscribed deployment; cap subscriptions per user; fall back to polling.
4. Run the audit-chain verifier and expose its metrics.
5. Expose `slm_*` service metrics for its own health.
Heavy aggregations run against a read replica or pre-aggregated materialised rollups (5-minute buckets per org/deployment) once `usage_logs` exceeds ~100 M rows; live queries scan only the last hour.
