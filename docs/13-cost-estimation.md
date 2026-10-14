# 13 - Cost Estimation

A framework, not a quote. Every number in the worked examples comes from the stated assumptions and the formulas next to it; change an
assumption and recompute. Throughput inputs are planning values from [01](01-architecture.md) section 9 (a bandwidth/compute model, **not measurements**); replace them with
your benchmark results (production checklist C1) before using any figure for pricing. All money is USD.

Status: the `cost` service (daily roll-up into `cost_records`, summaries, CSV) is a typed stub; the tables (`cost_rates`, `cost_records`, `usage_logs`, `quotas`, `training_jobs.est_cost_usd`, `evaluation_results.cost_per_1k_tokens_usd`) exist in `0001_init.sql`.

## 1. Model overview

```
hardware capex + opex + power  --->  cost per GPU-hour  --->  cost per busy GPU-second
                                                                 |
              throughput (prefill / decode tok/s) --------------+--->  cost per 1k/1M tokens (input, output)
              utilisation  -------------------------------------+
                                                                 +--->  fine-tune job cost
                                                                 +--->  dedicated deployment cost (GPU-hours)
usage_logs, deployment runtime, training_jobs  --->  cost_records (daily, per org/project/deployment/resource)  --->  chargeback / showback
```

## 2. Cost per GPU-hour

Per GPU, owned hardware:

```
C_amort = capex_per_gpu / (life_years * 8760)
C_opex  = capex_per_gpu * opex_rate / 8760          # support contracts, colocation/space, network share, staff share
C_power = kW_avg_per_gpu * PUE * price_per_kWh
C_gpu_hr = C_amort + C_opex + C_power               # cost of one GPU-hour whether used or idle (availability ~100%)
C_busy_hr = C_gpu_hr / utilisation                  # cost of one productive GPU-hour
```

### Assumptions (A3: placeholders, replace with your quotes and tariffs)

| Input | DGX Spark | H100 (8-GPU server) | Basis |
|---|---|---|---|
| Capex per GPU | $4,000 (one machine) | $31,250 (= $250,000 per 8-GPU server / 8) | Spark list price at launch was around $4,000 and has been reported higher since (see sensitivity); H100 server price is a market placeholder |
| Useful life | 3 years | 4 years | Straight-line to zero residual |
| Opex rate (per year, of capex) | 10% | 15% | Support, space, network, staff share. Higher for datacentre gear |
| Average power per GPU | 0.20 kW (whole system at the wall) | 0.875 kW (= 7.0 kW per 8-GPU node average / 8) | Node max ~10 kW; 70% average load assumed |
| PUE | 1.2 | 1.4 | Room cooling overhead |
| Electricity | $0.15 / kWh | $0.12 / kWh | |

| Component ($/GPU-hour) | Spark | H100 |
|---|---|---|
| Amortisation | 0.1522 | 0.8918 |
| Opex | 0.0457 | 0.5351 |
| Power | 0.0360 | 0.1470 |
| **C_gpu_hr** | **0.234** | **1.574** |
| Per month (730 h) | $170.7 | $1,149 |
| 8-GPU H100 server per month | - | $9,192 |

Not included (add as an "overhead factor" on top in your own model): control-plane and data-service nodes, storage and backup, software support
contracts, engineering staff beyond the opex rate, networking between sites. Rule of thumb for a first estimate: add 15-25% on top of the GPU line for a production multi-tenant platform (Assumption).

Cloud targets (`azure`, `aws`) replace the formula with the provider's hourly price (on-demand, reserved or spot); the rest of the model is unchanged.
**Own vs rent break-even:** if the rented GPU is billed only while needed and demand is elastic, owning wins when average utilisation of the owned GPU exceeds `C_gpu_hr / price_rent`.
For `C_gpu_hr = 1.574`: rent at $2.00/h gives 79%, at $3.00/h gives 52%, at $4.00/h gives 39% (rental prices are Assumptions: check current price lists).

## 3. Token cost

GPU time per request has a prefill part (compute-bound) and a decode part (bandwidth-bound; the aggregate decode rate already includes batching):

```
gpu_seconds(req)   = prompt_tokens / prefill_tps + completion_tokens / decode_tps
cost(req)          = gpu_seconds(req) * C_busy_hr / 3600
price_in_per_1M    = C_busy_hr / (prefill_tps * 3600) * 1e6
price_out_per_1M   = C_busy_hr / (decode_tps  * 3600) * 1e6
```

The two prices map to `cost_rates.resource = input_token_1k` and `output_token_1k` (divide by 1,000 to get per-1k).
Cached prompt tokens (`usage_logs.cached_tokens`) skip prefill, so they cost approximately zero GPU time; **Proposed:** bill them at a discount factor `d` (for example 0.1-0.5) of the input price, i.e. `billed_input = (prompt - cached) + d * cached`.

### 3.1 Unit costs (workload 800 prompt + 250 completion tokens, utilisation 60%)

| Unit | Decode tok/s | Prefill tok/s | $/1M input | $/1M output | $/request | $/1M requests |
|---|---|---|---|---|---|---|
| Spark, Phi-4-mini 3.8B FP8 | 600 | 12,000 | 0.0090 | 0.1805 | 0.0000523 | 52 |
| Spark, Llama-3.1-8B FP8 | 380 | 6,000 | 0.0181 | 0.2849 | 0.0000857 | 86 |
| H100, Phi-4-mini 3.8B FP8 | 8,000 | 80,000 | 0.0091 | 0.0911 | 0.0000301 | 30 |
| H100, Llama-3.1-8B FP8 | 4,000 | 40,000 | 0.0182 | 0.1822 | 0.0000601 | 60 |
| H100, Phi-4 14B FP8 | 2,200 | 22,000 | 0.0331 | 0.3312 | 0.0001093 | 109 |

(`C_busy_hr` = 0.234 / 0.6 = 0.390 for Spark and 1.574 / 0.6 = 2.623 for H100.)

**The 60% utilisation used above is the utilisation of a GPU while it is serving at peak, not the fleet average.** A fleet provisioned for a 4x peak-to-average ratio has an average utilisation of about 15%, and with N+1 redundancy and
node-size quantisation it is lower. Section 6 shows both views, because mixing them is the most common error in LLM cost models.

## 4. Fleet cost view (what you actually pay)

For the sizing scenarios of [01](01-architecture.md) section 9.2 (Llama-3.1-8B FP8, peak = 4x average, 60% utilisation at peak):

| Scenario | Peak GPUs needed | Fleet you buy | Monthly fleet cost (C_gpu_hr x 730 h) | Average utilisation | $/1M requests (30 d) |
|---|---|---|---|---|---|
| 1M req/day, H100, no HA | 7 | 1 node = 8 GPUs | $9,192 | 11.9% | **306** |
| 1M req/day, H100, HA | 7 | 2 nodes = 16 GPUs | $18,384 | 6.0% | 613 |
| 10M req/day, H100 | 64 | 8 nodes = 64 GPUs (no HA) | $73,534 | 14.9% | 245 |
| 1M req/day, Spark (shown for completeness) | 62 | 62 machines | $10,585 | ~15% | 353 |

Reading: the unit cost of $60 per 1M requests is a floor reachable only by keeping GPUs busy (shared multi-tenant pool, queue-tolerant batch traffic, autoscaling on cloud, time-zone smoothing).
A peak-provisioned owned fleet costs 4-10x the floor per request. The Spark row is arithmetic only: 62 non-redundant desktop-class machines is not a sensible deployment (see [06](06-infrastructure.md)).
Levers that close the gap, in order of effect: raise average utilisation (shared endpoints, batch/async API tier, autoscaling, rent for peaks), quantise (FP8/AWQ), enable prefix caching for RAG traffic, serve per-tenant fine-tunes as LoRA on a shared base, speculative decoding for latency-bound workloads.

## 5. Fine-tuning job cost estimator

```
tokens_processed = dataset_tokens * epochs                       # (training split; eval pass adds ~eval_split fraction)
time_h           = tokens_processed / train_tps / 3600 + overhead_h      # overhead_h ~ 0.25 h: model load, tokenisation, checkpoint writes, merge
cost_hw          = time_h * gpu_count * C_gpu_hr                  # hardware cost of the reserved GPU(s)
cost_loaded      = cost_hw / fleet_utilisation                    # share of idle capacity (use the same utilisation policy as token prices)
est_cost_usd     = cost_loaded                                    # stored in training_jobs.est_cost_usd; show a +/-50% range in the UI
```

Throughput assumptions (QLoRA, sequence packing; **Assumptions scaled from the 8B point by 1/params; benchmark**):

| Model | Spark tok/s | H100 tok/s |
|---|---|---|
| Phi-4-mini 3.8B | ~2,500 | ~12,600 |
| Llama-3.1-8B | ~1,200 | ~6,000 |
| Phi-4 14B | ~690 | ~3,400 |

Derivation of the 8B points: `6 x 8e9 = 48 GFLOP/token`; Spark ~125 TFLOPS dense BF16 x ~30% efficiency x ~1.5 (Unsloth kernels) / 48 GFLOP ~ 1,200 tok/s; H100 ~989 TFLOPS dense BF16 x ~35% x ~1.0 / 48 GFLOP = 7.2k, derated for 4-bit dequantisation to 6,000.

Memory fit (why QLoRA is the default `method`): weights + optimiser + activations, order of magnitude.

| Method | Memory per parameter (approx.) | 8B on 128 GB Spark | 8B on 80 GB H100 |
|---|---|---|---|
| Full fine-tune (BF16 + AdamW fp32 states + master weights) | ~16 bytes (2 weights + 2 grads + 8 Adam + 4 master) = ~128 GB | Does not fit with activations | Does not fit; needs sharding or 8-bit optimiser |
| LoRA (BF16 base frozen) | ~2 bytes base + small adapters + activations | Fits (~16 GB base + activations) | Fits |
| QLoRA (4-bit base) | ~0.5-0.6 bytes base + adapters + activations | Fits easily (~5 GB base) | Fits easily |

Worked estimate: 20 M-token dataset x 3 epochs = 60 M tokens, Llama-3.1-8B QLoRA, overhead 0.25 h, fleet utilisation 60%.

| Tier | Time | Hardware cost | Loaded cost | Effective price per 1M trained tokens (loaded) |
|---|---|---|---|---|
| Spark | 14.1 h | $3.31 | $5.51 | $0.092 |
| 1x H100 | 3.0 h | $4.77 | $7.94 | $0.132 |

Same job for Phi-4-mini: Spark 6.9 h / $2.70 loaded; H100 1.6 h / $4.13 loaded. Phi-4 14B: Spark 24.4 h / $9.51; H100 5.1 h / $13.51.
The Spark is cheaper per trained token and 4-5x slower: the choice is a latency/throughput decision, not a cost one. On a Spark that also serves inference the **opportunity cost** (serving unavailable for the whole job, section 9.4 of [01](01-architecture.md)) usually dominates.

Pricing a customer: `cost_rates` row `train_token_1k` = effective price per 1M / 1,000 (for example 0.000132 for H100 loaded).
**Precision warning:** `cost_rates.usd` is `numeric(12,6)`; per-1k token prices of this size ($0.000018 for input, $0.000132 for training) carry only 2-3 significant digits, which creates up to ~3% rounding error at the smallest prices.
Either store per-1M rates (and rename the unit) or widen the column in `0002`.

Estimator accuracy: dominated by the throughput assumption (a 2x error moves the cost 2x). Calibrate with actual runs: after each job record `tokens_processed / (finished_at - started_at)` and keep a rolling median per `(model size, method, hardware)`; the estimator uses that when it has >= 3 samples.

## 6. Sensitivity

### 6.1 Output-token cost vs utilisation and decode throughput ($ per 1M output tokens, H100 Llama-3.1-8B FP8, C_gpu_hr = 1.574)

| Average utilisation | decode 2,000 tok/s | 4,000 (base) | 6,000 |
|---|---|---|---|
| 12% (peak-provisioned fleet) | 1.822 | 0.911 | 0.607 |
| 30% | 0.729 | 0.364 | 0.243 |
| 50% | 0.437 | 0.219 | 0.146 |
| 60% (base) | 0.364 | **0.182** | 0.121 |
| 90% | 0.243 | 0.121 | 0.081 |

Same for Spark Llama-3.1-8B FP8 (C_gpu_hr = 0.234):

| Average utilisation | decode 250 tok/s | 380 (base) | 550 |
|---|---|---|---|
| 12% | 2.165 | 1.425 | 0.984 |
| 30% | 0.866 | 0.570 | 0.394 |
| 50% | 0.520 | 0.342 | 0.236 |
| 60% (base) | 0.433 | **0.285** | 0.197 |
| 90% | 0.289 | 0.190 | 0.131 |

Utilisation moves the answer 7.5x between 12% and 90%; throughput 3x across the range shown. Hardware price moves it far less.

### 6.2 One-at-a-time sensitivity of C_gpu_hr

| Change | H100: $/GPU-hr | vs base 1.574 | Spark: $/GPU-hr | vs base 0.234 |
|---|---|---|---|---|
| Capex -30% / +30% | 1.146 / 2.002 | -27% / +27% | - | - |
| Capex $4,699 (Spark) | - | - | 0.268 | +15% |
| Life 3 y / 5 y (H100); 2 y / 4 y (Spark) | 1.871 / 1.396 | +19% / -11% | 0.310 / 0.196 | +33% / -16% |
| Opex rate 10% / 25% (H100); 20% (Spark) | 1.396 / 1.931 | -11% / +23% | 0.280 | +20% |
| Electricity $0.06 / $0.25 per kWh (H100); $0.30 (Spark) | 1.500 / 1.733 | -5% / +10% | 0.270 | +15% |
| PUE 1.2 / 1.8 (H100) | 1.553 / 1.616 | -1% / +3% | - | - |

Takeaway: for H100-class hardware, amortisation and opex (support, space, staff) dominate; electricity is a rounding error in most tariff regions. For the Spark, capex dominates too; power is about 15% of the hourly cost.
Report a range in customer-facing estimates (the UI does so for fine-tuning).

## 7. Chargeback and showback by org and project

### 7.1 Data flow and attribution rules

```
gateway --> usage_logs (per request: org_id, project_id, api_key_id, deployment_id, model_version_id, tokens, cached_tokens, latency)
deployment_events / deployments.status --> running intervals per deployment (replicas x gpu_count x hours)
training_jobs --> tokens_processed, started_at, finished_at, gpu_count
object storage usage --> GB per org prefix (daily snapshot)
        |  daily job in the cost service, rates from cost_rates (latest effective_from <= day)
        v
cost_records (day, org_id, project_id, deployment_id, resource, quantity, usd)   -- upsert on cost_records_uq
```

| `cost_records.resource` | Source of `quantity` | Applies to | `usd` |
|---|---|---|---|
| `input_token_1k` | sum(`prompt_tokens` - discounted `cached_tokens`) / 1000, grouped by org, project, deployment | `shared` endpoints (and shown as informational for private) | quantity x input rate |
| `output_token_1k` | sum(`completion_tokens`) / 1000 | same | quantity x output rate |
| `gpu_hour` | running hours x replicas x `gpu_count` per deployment-day | `private` and `dedicated` deployments (reserved, includes idle), plus training jobs' GPU time for cost-of-goods | quantity x rate for the `gpu_type` |
| `train_token_1k` | `tokens_processed` / 1000 on the job's finish day | Fine-tuning (bill by tokens; record GPU time as quantity-only to avoid double counting) | quantity x rate |
| `storage_gb_month` | GB-days / days-in-month per org (datasets, models, checkpoints, RAG docs, vector DB) | All orgs | quantity x rate |

Rules and caveats:

1. **Project attribution comes from the API key.** Project-scope keys carry `project_id`; organization-scope keys do not, so their usage lands in the org's "unassigned" bucket (NULL project). Recommend project keys wherever cost splits matter.
2. **Idle and unallocated cost.** Shared-pool GPU cost minus the cost attributed to tenants' tokens is platform overhead. Either show it as a separate "unallocated" line or allocate pro rata to token usage (showback). Do not hide it inside token prices.
3. **Dedicated vs shared.** A dedicated deployment is charged for its reservation regardless of traffic. It is an isolation and latency-guarantee product, not a discount: it beats shared pricing only if the tenant keeps its GPU busier than the utilisation assumed in the shared price (60% in the base case). Below that, shared is cheaper by the ratio.
4. **Cost vs price.** `cost_rates` has a single `usd` column and no org dimension: it holds cost-basis (showback) rates. Customer pricing with margin, discounts or negotiated rates needs a markup/plan table (gap #8 in [02](02-database-schema.md#schema-gaps-and-follow-up-migrations)).
5. **Key limitation of `cost_records_uq`:** the unique key omits `model_version_id`, so per-model-version attribution within a deployment-day is not preserved (the contract's `group_by=model` must derive the model via the deployment's version at that time, or the index must be extended).
6. **Enforcement latency.** Budgets (`quotas.monthly_budget_usd`) cannot be enforced from daily `cost_records`. The gateway enforces near-real-time from Redis counters (tokens x current rates, GPU-hours estimated), and the daily roll-up is the book of record; they reconcile at month end.
7. **Reconciliation.** Monthly: platform cost (sum of `C_gpu_hr` x GPU-hours provided + overhead) vs sum of `cost_records.usd` (cost-basis). A persistent gap means utilisation is lower than the price assumed: re-price or optimise.
8. **Currency and tax** are out of scope (USD, pre-tax).

### 7.2 Worked chargeback: org "Acme", 30-day month, H100 tier, base-case rates

Rates (cost basis, from sections 2-3, H100 Llama-3.1-8B at 60% utilisation): input $0.0182/1M, output $0.182/1M; GPU hour $1.574; training $0.132 per 1M trained tokens (loaded); storage $0.02/GB-month (Assumption).

| Project | Resource | Quantity | Cost |
|---|---|---|---|
| support | `gpu_hour` (dedicated, 1 GPU, 24 x 30 = 720 h) | 720 GPU-h | $1,133.3 |
| support | `train_token_1k` (one 60 M-token job) | 60,000 (x1k tokens) | $7.9 |
| search | `input_token_1k` (shared) | 150 M tokens | $2.7 |
| search | `output_token_1k` (shared) | 40 M tokens | $7.3 |
| both | `storage_gb_month` | 400 GB | $8.0 |
| | **Total** | | **$1,159.3** |

(720 h x 1.574 = $1,133.3; shared tokens: 150 x 0.0182 + 40 x 0.182 = $10.02; the table rounds each line.)
Without the dedicated GPU the month would cost about $26. This is why the serving class (`shared` / `private` / `dedicated`, [01](01-architecture.md) section 8) is a pricing decision and why the UI shows the cost class at deployment creation.
Platform-side check: if the shared pool served 10 tenants like `search`, the pool's GPU cost over the month is `pool_gpu_hours x 1.574`; the sum of their token-based charges equals that cost only at 60% pool utilisation. Anything lower shows up as "unallocated".

## 8. Where the inputs come from at runtime

| Input | Source | Used for |
|---|---|---|
| Tokens, cached tokens, latency | `usage_logs` | Token cost, per-request cost, chargeback |
| GPU-hours | `deployments` status intervals (`deployment_events`), `training_jobs` | GPU cost |
| Real throughput per replica | vLLM `vllm:generation_tokens_total`, `vllm:prompt_tokens_total`; `slm:vllm_gen_tps:1m` ([12](12-monitoring-design.md)) | Replace planning throughput |
| Power | `DCGM_FI_DEV_POWER_USAGE`, `DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION` | Replace the `kW_avg` assumption with measured kWh |
| Utilisation | `DCGM_FI_PROF_*`, vLLM running/waiting gauges | Fleet utilisation in `C_busy_hr` |
| Rates | `cost_rates` (versioned by `effective_from`) | Pricing; change history is the audit trail of price changes |
| Evaluation cost | `evaluation_results.cost_per_1k_tokens_usd` | Quality-vs-cost comparison between model versions |

A "cost per 1M tokens (measured)" panel (measured GPU-hours x `C_gpu_hr` / measured tokens) should sit beside the planning figure so drift is visible; the cost service should publish it as a metric.

## 9. Limitations

- Throughput numbers are modelled, not measured; real values vary with context length mix, sampling parameters, quantisation kernels, vLLM version and, on GB10, kernel maturity.
- Straight-line amortisation ignores resale value, financing and tax treatment.
- No network egress, backup storage, licence or staff-beyond-opex costs unless added through the overhead factor.
- Cloud spot interruption, reservation discounts and committed-use agreements are not modelled.
- LoRA multiplexing savings (one base model, many adapters) are not in the unit costs; they show up as lower GPU counts, not lower per-token prices.
