# 09 - Deployment Guide: bare DGX Spark to first inference call

Goal: a single NVIDIA DGX Spark (Topology T1 in [06](06-infrastructure.md)) running SLM Studio on k3s, one organization,
one model (Phi-4-mini, MIT licence, no gated download), verified end to end with `curl` and the OpenAI Python SDK.

**Read first**

- This is a **single-node, non-HA** install. Do not promise availability on it. Backups (step 12) are mandatory.
- Status of the components at time of writing ([README](README.md)): auth, registry and gateway are implemented; the deployment
  service is a typed stub, so step 9 registers a Helm-managed vLLM server by hand instead of letting the platform create it. The chart
  (`deploy/helm/slm-studio`, overlay `values-dgx-spark.yaml`) exists but has not been run against a real Spark by the authors of this guide;
  key names below were taken from the chart files, but check them against the chart version you install.
- Commands that depend on NVIDIA/DGX OS behaviour on GB10 are marked **(verify)**: they were written from vendor documentation
  conventions and must be checked against the DGX OS release notes on your unit.
- Replace every `<placeholder>`. Never paste real secrets into shell history on a shared machine.

Time: about 2-3 hours, mostly downloads.

## 0. Prerequisites

| Item | Requirement |
|---|---|
| Hardware | DGX Spark, >= 1 TB NVMe free (model cache, Postgres, MinIO, images), wired network |
| OS | DGX OS (Ubuntu-based, aarch64), fully updated, GPU driver and NVIDIA Container Toolkit as shipped |
| Network | Outbound HTTPS to `huggingface.co`, `ghcr.io`/your registry, `helm.ngc.nvidia.com`, `nvcr.io`, `get.k3s.io`, chart repos. If air-gapped, mirror these first (see [07](07-kubernetes-and-docker.md)) |
| DNS | `api.slm.<domain>` and `app.slm.<domain>` pointing to the Spark (or use `/etc/hosts` + self-signed TLS for a lab) |
| Accounts | HF token only if using gated models (Llama, Gemma); not needed for Phi-4-mini |
| Workstation tools | `kubectl`, `helm` 3.14+, `curl`, `jq`, `uv` (for the SDK smoke test) |

## 1. Base system

```bash
uname -m                      # expect: aarch64
sudo apt update && sudo apt full-upgrade -y
sudo reboot                   # DGX OS may also prompt for a firmware/driver update (use the vendor-provided updater)
nvidia-smi                    # expect: GB10, driver and CUDA version printed. Memory fields may show N/A on unified memory (verify)
docker --version
docker run --rm --gpus all nvcr.io/nvidia/cuda:<tag-from-NGC-matching-driver> nvidia-smi
```

Do **not** install a different NVIDIA driver than the one DGX OS provides.

Host tuning (recommended):

```bash
# keep swap from masking memory pressure; unified memory is shared with the GPU
sudo sysctl -w vm.swappiness=10
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-slm.conf
# directories on NVMe
sudo mkdir -p /data/{models,pg,minio,qdrant} && sudo chown -R 1000:1000 /data
# firewall: allow 22, 443, 6443 (k3s API; restrict to your admin network)
sudo ufw allow 22/tcp && sudo ufw allow 443/tcp && sudo ufw allow from <admin-cidr> to any port 6443 proto tcp
```

Unified-memory note: Linux page cache counts against the same 128 GB. After loading large models, if `free -g` shows little available memory, `sync; echo 3 | sudo tee /proc/sys/vm/drop_caches` reclaims it (do not automate this; investigate instead if it recurs).

## 2. Kubernetes (k3s)

```bash
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server --disable traefik --write-kubeconfig-mode 640 \
  --data-dir /data/k3s" sh -
sudo cat /etc/rancher/k3s/k3s.yaml     # copy to your workstation as ~/.kube/config, replace 127.0.0.1 with the Spark address
kubectl get nodes -o wide              # Ready, arch arm64
```

`--disable traefik` because ingress-nginx is used (buffering control for SSE). k3s embeds containerd; if the NVIDIA container runtime is installed on the host
when k3s starts, k3s registers a `nvidia` runtime in containerd. Check and create the RuntimeClass:

```bash
sudo grep -n nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml    # runtime entry should exist
kubectl apply -f - <<'EOF'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: { name: nvidia }
handler: nvidia
EOF
```

If the runtime entry is missing, restart k3s after confirming `nvidia-container-runtime` is on the PATH (`sudo systemctl restart k3s`).

## 3. GPU exposure to Kubernetes

**Option A (recommended on a single Spark): device plugin + DCGM exporter using the host driver and toolkit.**

```bash
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin && helm repo update
helm upgrade --install nvdp nvdp/nvidia-device-plugin -n gpu-operator --create-namespace \
  --set runtimeClassName=nvidia
```

**Option B: NVIDIA GPU Operator** (use this if you will add nodes or want operator-managed DCGM/feature discovery):

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia && helm repo update
helm upgrade --install gpu-operator nvidia/gpu-operator -n gpu-operator --create-namespace \
  --set driver.enabled=false \
  --set toolkit.enabled=false \
  --set-string toolkit.env[0].name=CONTAINERD_CONFIG \
  --set-string toolkit.env[0].value=/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl \
  --set-string toolkit.env[1].name=CONTAINERD_SOCKET \
  --set-string toolkit.env[1].value=/run/k3s/containerd/containerd.sock
```

The chart's vLLM pods select nodes with `nvidia.com/gpu.present: "true"` (set by GPU Feature Discovery, which Option B installs). **With Option A that label does not exist: add it yourself** (`kubectl label node <node> nvidia.com/gpu.present=true`) or the pod stays `Pending`.

`driver.enabled=false` and `toolkit.enabled=false` assume DGX OS provides both (**verify** your DGX OS release and the GPU Operator release notes for GB10/aarch64 support; if operator pods crash-loop, use Option A).
Do not install both options.

Verify:

```bash
kubectl get nodes -o json | jq '.items[].status.allocatable["nvidia.com/gpu"]'     # "1"
kubectl run gpu-test --rm -it --restart=Never --image=nvcr.io/nvidia/cuda:<tag> \
  --overrides='{"spec":{"runtimeClassName":"nvidia","containers":[{"name":"gpu-test","image":"nvcr.io/nvidia/cuda:<tag>","command":["nvidia-smi"],"resources":{"limits":{"nvidia.com/gpu":1}}}]}}'
```

## 4. Platform dependencies

Install order matters. The chart installs everything it owns into one release namespace (`slm-studio` below) and expects its dependencies elsewhere:
`storage` (S3), `mlops` (MLflow), `vectordb` (Qdrant), `monitoring` (Prometheus); the names are only defaults in `values.yaml` and can be changed.
`deploy/k8s/00-namespace.yaml` shows a namespace with Pod Security `restricted` labels for reference.

```bash
kubectl create namespace slm-studio
kubectl create namespace storage; kubectl create namespace mlops; kubectl create namespace vectordb; kubectl create namespace monitoring

# TLS and ingress
helm repo add jetstack https://charts.jetstack.io
helm upgrade --install cert-manager jetstack/cert-manager -n cert-manager --create-namespace --set crds.enabled=true
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx --create-namespace \
  --set controller.hostPort.enabled=true --set controller.service.type=ClusterIP \
  --set-string controller.config.proxy-buffering=off \
  --set-string controller.config.proxy-read-timeout=600

# PostgreSQL operator
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm upgrade --install cnpg cnpg/cloudnative-pg -n cnpg-system --create-namespace
```

PostgreSQL cluster (single instance on Spark; use 3 instances in T3):

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: { name: slm-pg, namespace: slm-studio }
spec:
  instances: 1
  enableSuperuserAccess: true                            # the migration step below needs an admin DSN (secret slm-pg-superuser)
  imageName: ghcr.io/cloudnative-pg/postgresql:16        # multi-arch image; PostgreSQL >= 15 is required by the schema
  storage: { size: 100Gi, storageClass: local-path }     # k3s default; points at --data-dir
  resources: { requests: { cpu: "2", memory: 4Gi }, limits: { memory: 8Gi } }   # cap: it shares RAM with the GPU
  postgresql:
    parameters: { max_connections: "200", shared_buffers: "2GB" }
```

Remaining services (take exact chart values from `deploy/` where provided; otherwise any equivalent works because the platform only needs the protocol):

| Component | Suggested install | Notes |
|---|---|---|
| PgBouncer | CNPG `Pooler` resource, `poolMode: transaction` | Compatible with `set_config(..., true)` (transaction-local). Disable asyncpg prepared-statement cache or use PgBouncer >= 1.21 with `max_prepared_statements` |
| Redis | Official `redis` image as a 1-replica StatefulSet (in chart or `deploy/k8s`), no persistence required | Avoid depending on a chart whose image distribution policy may change without notice |
| S3 store | MinIO (single node, `/data/minio`) or any S3-compatible store (SeaweedFS, Garage, Ceph RGW) | MinIO is AGPL-licensed and its community-edition distribution/maintenance status has changed recently: **verify before standardising**. The services use only the S3 API. Single-drive = no redundancy: step 12 |
| Buckets | `models`, `datasets`, `rag-documents`, `checkpoints`, `reports` | Enable versioning on `models`, `datasets` |
| Qdrant | `helm repo add qdrant https://qdrant.github.io/qdrant-helm`; 1 replica, PVC on `/data/qdrant` | Only needed for RAG |
| MLflow | Deployment with PostgreSQL backend (separate DB `mlflow`) and S3 artifact root | Only needed for fine-tuning |
| Observability | `kube-prometheus-stack` (Prometheus, Alertmanager, Grafana); Loki/Tempo optional on Spark | Metrics are enough for a pilot |

## 5. Secrets

The chart reads **one Secret whose keys are the environment-variable names** (`secrets.existingSecret`), and a second Secret for the migration Job (`migration.existingSecret`).
On a single box without External Secrets Operator, create them yourself (the Spark overlay sets `externalSecret.enabled: false` and expects `slm-studio-secrets` and `slm-studio-migrate`).

```bash
# JWT signing key (RS256, the chart's default SLM_JWT_ALGORITHM). Only `auth` needs the private key; others verify with the public key
openssl genrsa -out jwt_private.pem 3072 && openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem

kubectl -n slm-studio create secret generic slm-studio-secrets \
  --from-literal=SLM_DATABASE_URL='postgresql://slm_app:<app-pw>@slm-pg-rw.slm-studio:5432/slm' \
  --from-literal=SLM_REDIS_URL='redis://redis.slm-studio:6379/0' \
  --from-file=SLM_JWT_PRIVATE_KEY=jwt_private.pem --from-file=SLM_JWT_PUBLIC_KEY=jwt_public.pem \
  --from-literal=SLM_S3_ACCESS_KEY='<key>' --from-literal=SLM_S3_SECRET_KEY='<secret>' \
  --from-literal=SLM_TRAINER_TOKEN="$(openssl rand -hex 32)"
# Migration Job: admin/owner DSN and the password to give the runtime role slm_app (must equal <app-pw> above)
kubectl -n slm-studio create secret generic slm-studio-migrate \
  --from-literal=SLM_MIGRATE_DATABASE_URL='postgresql://postgres:<superuser-pw>@slm-pg-rw.slm-studio:5432/slm' \
  --from-literal=SLM_APP_PASSWORD='<app-pw>'
```

Optional keys: `SLM_HF_TOKEN` (gated models), `SLM_WANDB_API_KEY`, `SLM_OAUTH__{AZURE_AD,GOOGLE,GITHUB}__CLIENT_SECRET`. Keep `jwt_private.pem` and the passwords in your secret manager and delete local copies.
Note: the comment block in `values-dgx-spark.yaml` mentions `SLM_DATABASE_URL` for the migrate secret, but `values.yaml` and `migration-job.yaml` read `SLM_MIGRATE_DATABASE_URL`: use the latter. The Redis host above assumes a Redis you installed in the same namespace (step 4).

## 6. Database: migrations, roles, partitions

`db/migrate.py` applies `db/migrations/*.sql` in order (advisory lock, checksum recorded in `schema_migrations`) **and creates or refreshes the runtime role `slm_app`** with its grants when you pass `--app-password`.
It needs an admin DSN: a superuser or a role with `CREATEROLE`, extension rights and `BYPASSRLS` (the catalogue seed in `0001` inserts rows into a table with forced RLS; see [02](02-database-schema.md) section 3.5).
The Helm chart runs the same script as a pre-install hook; running it by hand the first time shows errors clearly.

```bash
# create the database (CNPG creates `app` by default; here we use a dedicated one)
kubectl cnpg psql slm-pg -n slm-studio -- -c 'CREATE DATABASE slm;'
kubectl -n slm-studio port-forward svc/slm-pg-rw 5432:5432 &
export PGPASSWORD=<superuser-pw>
uv run python db/migrate.py --dsn postgresql://postgres:$PGPASSWORD@localhost:5432/slm --app-password '<app-pw>'
```

(`uv run python db/migrate.py --help` lists all options; the script is the same one CI uses.) After this succeeds the chart's migration hook is a no-op.

Create the usage partitions **before any traffic**, otherwise rows land in `usage_logs_default`, which later blocks creating that month's partition:

```sql
CREATE TABLE usage_logs_2026_10 PARTITION OF usage_logs FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE usage_logs_2026_11 PARTITION OF usage_logs FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE usage_logs_2026_12 PARTITION OF usage_logs FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
```

(The monitoring service is meant to maintain partitions; it is a typed stub today, so put a calendar reminder or a cron job on this until it is implemented. Partitions are owned by the admin role; `slm_app` reaches them through the parent table.)

Sanity check that RLS is effective for the application role:

```sql
-- psql "postgresql://slm_app:<app-pw>@localhost:5432/slm"
SELECT count(*) FROM projects;                        -- 0: no app.org_id set, fails closed
SELECT count(*) FROM models;                          -- 6: global catalogue rows are readable by everyone
SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;   -- f | f
```

## 7. Install SLM Studio

TLS: the Spark overlay sets `ingress.clusterIssuer: ""`, so create the TLS secrets `slm-api-tls` and `slm-app-tls` in `slm-studio` (self-signed is fine for a lab) or configure a cert-manager issuer.

```bash
helm upgrade --install slm-studio deploy/helm/slm-studio -n slm-studio \
  -f deploy/helm/slm-studio/values-dgx-spark.yaml \
  --set global.imageRepositoryPrefix=<owner>/slm-studio --set global.imageTag=<git-sha-or-release> \
  --set ingress.api.host=api.slm.<domain> --set ingress.app.host=app.slm.<domain> \
  --set config.SLM_S3_ENDPOINT=http://<minio-service>.storage.svc.cluster.local:9000
kubectl -n slm-studio rollout status deploy/slm-studio-gateway    # resource names follow the chart's fullname helper: list with `kubectl -n slm-studio get deploy`
kubectl -n slm-studio get pods                                     # gateway, auth, registry, ... and a vllm-phi-4-mini StatefulSet
```

The overlay also deploys the platform-managed vLLM server `phi-4-mini` (`vllm.models` in `values.yaml`, StatefulSet with a PVC cache, `gpuMemoryUtilization: 0.35` per model in the Spark overlay, image from NVIDIA NGC; **verify** that tag supports your GB10 firmware).
The first start downloads `microsoft/Phi-4-mini-instruct` from Hugging Face into the cache volume; the startup probe tolerates up to ~30 minutes.
To give the model more memory: `--set vllm.models.phi-4-mini.args.gpuMemoryUtilization=0.6`.

Health checks (port-forward if the ingress is not up yet):

```bash
kubectl -n slm-studio port-forward svc/<gateway-service> 8000:8000 &
curl -s localhost:8000/healthz ; curl -s localhost:8000/readyz | jq .
```

## 8. First user and organization

```bash
export BASE=http://localhost:8000            # or https://api.slm.<domain>
curl -s $BASE/api/v1/auth/register -H 'content-type: application/json' \
  -d '{"email":"admin@example.com","password":"<12+ chars>","display_name":"Admin","org_name":"Acme"}' | tee /tmp/tok.json
export TOKEN=$(jq -r .access_token /tmp/tok.json)
curl -s $BASE/api/v1/me -H "authorization: Bearer $TOKEN" | jq .
```

Registering with `org_name` creates the organization and makes the user its `org_admin` (per the design in [03](03-api-spec.md)). Promote the first
**platform admin** by SQL (no API exists for this on purpose; it is an operator action; run as `slm_migrator`):

```sql
UPDATE users SET is_platform_admin = true WHERE email = 'admin@example.com';
```

Log in again to get a token that carries the `platform_admin` role. Create a project (optional): `POST /api/v1/projects {"slug":"demo","name":"Demo"}`.

## 9. First model and deployment

### 9.1 Register the model and a version

```bash
H=(-H "authorization: Bearer $TOKEN" -H 'content-type: application/json')
MODEL_ID=$(curl -s $BASE/api/v1/models "${H[@]}" \
  -d '{"name":"phi-4-mini-demo","family":"phi","license":"MIT","source":"huggingface","hf_repo":"microsoft/Phi-4-mini-instruct"}' | jq -r .id)
VER_ID=$(curl -s $BASE/api/v1/models/$MODEL_ID/versions "${H[@]}" \
  -d '{"format":"hf","artifact_uri":"hf://microsoft/Phi-4-mini-instruct","context_length":131072,"param_count":3800000000}' | jq -r .id)
```

(The global catalogue already has `phi-4-mini` rows seeded by migration `0001`; using your own org-level copy avoids needing platform-admin writes.)
Check the licence of any model you add ([14](14-roadmap.md#5-model-licence-caveats)); Llama and Gemma are gated and not OSI open source: put `HF_TOKEN` in the model-pull secret first.

### 9.2 Register the running vLLM server as a deployment

On this install the model server already runs (Helm-managed `phi-4-mini`). The platform needs a `deployments` row pointing at it. The chart annotates the Service with the exact value to store:

```bash
kubectl -n slm-studio get pods -l slm-studio.io/deployment=phi-4-mini            # wait until 1/1 Ready
INTERNAL_URL=$(kubectl -n slm-studio get svc -l slm-studio.io/deployment=phi-4-mini \
  -o jsonpath='{.items[0].metadata.annotations.slm-studio\.io/internal-url}')
echo "$INTERNAL_URL"      # http://<release>-vllm-phi-4-mini.slm-studio.svc.cluster.local:8000

DEP_ID=$(curl -s $BASE/api/v1/deployments "${H[@]}" -d "{
  \"name\":\"phi4mini-spark\",\"model_version_id\":\"$VER_ID\",\"kind\":\"private\",\"target\":\"dgx_spark\",
  \"strategy\":\"direct\",\"gpu_count\":1,\"replicas_min\":1,\"replicas_max\":1,
  \"vllm_args\":{\"max_model_len\":16384,\"gpu_memory_utilization\":0.35,\"enable_prefix_caching\":true}
}" | jq -r .id)
```

`vllm_args` here only **records** what the Helm values already configured (`max_model_len` 16384 for `phi-4-mini` in the chart, `gpuMemoryUtilization` 0.35 in the Spark overlay); the running server is governed by the Helm values until the deployment service takes over.

Because the `deployment` service is a typed stub, the row will stay `pending` and has no `internal_url`. Set both directly as the admin role (a bootstrap step that disappears once the service is implemented):

```sql
-- psql as the admin/owner role
UPDATE deployments SET status = 'running', internal_url = '<INTERNAL_URL>' WHERE id = '<DEP_ID>';
```

For another model, add an entry under `vllm.models` in your values (see the commented examples in `values.yaml` for `speculativeConfig`, `quantization`, `enableLora`), upgrade the release, and repeat this step. Do not exceed a total `gpuMemoryUtilization` of about 0.85 across models on one Spark; running two models needs device-plugin time-slicing so both pods can request `nvidia.com/gpu`.

### 9.3 Endpoint alias, route, API key

```bash
EP_ID=$(curl -s $BASE/api/v1/endpoints "${H[@]}" -d '{"alias":"phi-4-mini","kind":"private"}' | jq -r .id)
curl -s -X PUT $BASE/api/v1/endpoints/$EP_ID/routes "${H[@]}" \
  -d "{\"routes\":[{\"deployment_id\":\"$DEP_ID\",\"weight\":100,\"label\":\"stable\"}]}" | jq .
export KEY=$(curl -s $BASE/api/v1/api-keys "${H[@]}" -d '{"name":"smoke","scope":"organization"}' | jq -r .key)
echo "$KEY"     # slm_<prefix>_<secret>: shown once; store it
```

## 10. Smoke test

The repository ships `deploy/scripts/smoke-test.sh <api-base-url> <api-key>` (env `SMOKE_MODEL=phi-4-mini` adds a chat call; retries while the rollout settles); it is what the deploy workflow runs after `helm upgrade`. The manual checks below show what it verifies and more.

### 10.1 curl

```bash
curl -s $BASE/v1/models -H "authorization: Bearer $KEY" | jq '.data[].id'

# non-streaming; -i shows X-Request-ID, X-RateLimit-*, X-SLM-Route
curl -si $BASE/v1/chat/completions -H "authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"model":"phi-4-mini","messages":[{"role":"user","content":"Say hello in five words."}],"max_tokens":32}'

# streaming (-N disables curl buffering); expect data: lines and a final data: [DONE]
curl -sN $BASE/v1/chat/completions -H "authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"model":"phi-4-mini","stream":true,"stream_options":{"include_usage":true},
       "messages":[{"role":"user","content":"Count to five."}]}'

# error shape: expect 401 with {"error":{"message":...,"type":"authentication_error","code":"invalid_api_key"}}
curl -si $BASE/v1/models -H "authorization: Bearer slm_bad_key" | head -20
```

### 10.2 OpenAI Python SDK (only `base_url` and key change)

```python
# smoke.py   run with:  uv run --with openai python smoke.py
import os
from openai import OpenAI

client = OpenAI(base_url=os.environ["BASE"] + "/v1", api_key=os.environ["KEY"])
print([m.id for m in client.models.list().data])

r = client.chat.completions.create(
    model="phi-4-mini",
    messages=[{"role": "user", "content": "What is 17 * 23? Answer with just the number."}],
    max_tokens=16, temperature=0,
)
print(r.choices[0].message.content, r.usage)

for chunk in client.chat.completions.create(
    model="phi-4-mini", stream=True, stream_options={"include_usage": True},
    messages=[{"role": "user", "content": "Name three GPUs."}],
):
    if chunk.choices:
        print(chunk.choices[0].delta.content or "", end="", flush=True)
    elif chunk.usage:
        print("\nusage:", chunk.usage)
```

Expected: models list contains `phi-4-mini`; arithmetic answer `391`; stream prints tokens then usage.

### 10.3 Verify metering, limits and observability

```sql
-- as slm_migrator (bypasses RLS) - usage rows exist with tokens and latency
SELECT request_id, status_code, prompt_tokens, completion_tokens, latency_ms, ttft_ms, route_label
FROM usage_logs ORDER BY created_at DESC LIMIT 5;
-- audit trail exists for the key create / deployment create
SELECT action, resource, created_at FROM audit_logs ORDER BY id DESC LIMIT 10;
```

Rate limiting: lower the limit and confirm a 429 with `Retry-After`:

```sql
UPDATE quotas SET rpm_limit = 5 WHERE org_id = (SELECT id FROM organizations WHERE slug = 'acme');
```
then fire 10 quick requests (`for i in $(seq 10); do curl -s -o /dev/null -w '%{http_code}\n' ...; done`) and expect `200` x5 then `429`.
(The key/quota cache is 60 s; wait up to a minute after the update.) Restore the default (`600`) afterwards.

Metrics: `curl -s localhost:8000/metrics | head`; vLLM: `kubectl -n slm-studio port-forward statefulset/<vllm-statefulset> 8001:8000 & curl -s localhost:8001/metrics | grep -E '^vllm:(num_requests_running|kv_cache_usage_perc|gpu_cache_usage_perc)'`
(the KV-cache metric name depends on the vLLM version, see [12](12-monitoring-design.md)).

Cross-tenant check (security smoke): register a second user/org, create a key there, and confirm `GET /v1/models` does not list `phi-4-mini`
(private alias) and that using the first org's deployment id in an API call returns 404.

## 11. Troubleshooting

| Symptom | Likely cause | Check / fix |
|---|---|---|
| Pod `Pending`, event `Insufficient nvidia.com/gpu` | Device plugin not running or GPU already allocated | `kubectl -n gpu-operator get pods`; only one GPU exists on Spark: stop other GPU pods |
| vLLM pod `Pending`, events mention unmatched node selector | Node lacks `nvidia.com/gpu.present=true` (Option A) or `kubernetes.io/arch=arm64` | `kubectl label node <node> nvidia.com/gpu.present=true`; check `kubectl describe pod` |
| vLLM `CrashLoopBackOff`, CUDA or kernel error at start | Image lacks Blackwell/GB10 (`sm_121`) support, or arm64 mismatch | Use the NGC/validated arm64 vLLM image; check `kubectl logs`; try a different quantisation |
| vLLM OOM / node freezes | `gpu_memory_utilization` too high for shared memory | Lower to 0.6-0.7, stop other pods, drop page cache, set pod memory limits on neighbours |
| `no_healthy_deployment` (503) | Route points to a deployment not `running`, or no `internal_url` | Check `deployments.status`, `endpoint_routes`; the gateway's in-process route cache expires after 5 s |
| `invalid_api_key` right after rotating/revoking | Expected up to 60 s due to key cache | Wait or flush `slm:keycache:*` |
| Streaming appears all at once | Proxy buffering | ingress `proxy-buffering: off` / `X-Accel-Buffering: no`; check any extra LB in front |
| `usage_logs` has no rows | Gateway usage queue dropping, or DB grants missing | Gateway logs, `slm_gateway_usage_dropped_total`, `GRANT` on `usage_logs` to `slm_app` |
| Migration fails on seed insert | Migration role is not superuser/`BYPASSRLS` | See [02](02-database-schema.md) section 3.5 |
| 429 for everything | `quotas` row missing/low | `SELECT * FROM quotas WHERE org_id = ...` |
| Login loops (401) | Clock skew between nodes or wrong JWT key mount | `date` on nodes; secrets; `SLM_JWT_*` |

## 12. Backups (do not skip on Spark)

Minimum viable protection for T1: nightly Postgres logical backup plus bucket copy to a different machine.

```bash
kubectl -n slm-studio exec slm-pg-1 -- pg_dump -Fc -U postgres slm > /backup/slm-$(date +%F).dump    # nightly via cron/CronJob
rclone sync minio:models  nas:slm-backup/models  --checksum         # or mc mirror
```

Better: CNPG `Cluster` with `backup.barmanObjectStore` pointing at off-site S3 (continuous WAL = RPO of minutes). Restore drill procedure and targets: [06](06-infrastructure.md) section 7. Then continue with [10-production-checklist.md](10-production-checklist.md).

## 13. Uninstall

```bash
helm -n slm-studio uninstall slm-studio
# workloads created at runtime by the deployment/finetune services (Jobs, tenant vLLM servers) are not removed by Helm:
kubectl -n slm-studio get statefulset,job,svc,pvc -l app.kubernetes.io/part-of=slm-studio   # review, then delete what remains
# data is on /data/*: delete deliberately, it contains tenant data (GDPR erasure: see 11)
```
