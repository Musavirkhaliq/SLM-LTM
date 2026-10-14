# Plain Kubernetes reference manifests

For teams that do not use Helm. The Helm chart in `../helm/slm-studio` is the maintained, complete deployment; these
files show the essential shapes it renders and are not kept in lock-step.

| File | What it is |
|---|---|
| `00-namespace.yaml` | namespace with Pod Security Admission `restricted` (enforce/audit/warn) |
| `05-networkpolicy.yaml` | default-deny + DNS, gateway -> vLLM, Prometheus scrape, model-download egress |
| `10-vllm-deployment.yaml` | stable vLLM server: PVC, Service, Deployment (GPU limit, shm, probes on `/health`), PDB |
| `20-vllm-canary.yaml` | a second vLLM server to receive canary / green traffic |

## Canary and blue-green traffic splits

Traffic is split by the **SLM Studio gateway**, not by Kubernetes or the ingress. Clients send `"model": "<alias>"`; the
gateway resolves the alias (table `endpoints`) to weighted rows of `endpoint_routes` (weights 0-100, must sum to 100),
picks a route, and proxies to that deployment's `internal_url`. The chosen route is returned in the `X-SLM-Route`
response header and recorded in `usage_logs.route_label`, so latency/error dashboards can be split by route.

1. Register both servers as deployments (`POST /api/v1/deployments`), with `internal_url`:
   - stable `http://vllm-phi-4-mini.slm-studio.svc.cluster.local:8000`
   - canary `http://vllm-phi-4-mini-canary.slm-studio.svc.cluster.local:8000`
2. Send 5 % of traffic to the canary (`PUT /api/v1/endpoints/{endpoint_id}/routes`):

```json
{
  "routes": [
    { "deployment_id": "<stable-deployment-uuid>", "weight": 95, "label": "stable" },
    { "deployment_id": "<canary-deployment-uuid>", "weight": 5,  "label": "canary" }
  ]
}
```

3. Watch the canary: Grafana "SLM Studio - Deployment" (`deployment = phi-4-mini-canary`) for TTFT/e2e latency and error
   rate against stable; promote by raising the weight (5 -> 25 -> 50 -> 100).
4. **Blue-green** is the same mechanism with a hard flip: keep green at weight 0 while it warms up and passes smoke
   tests, then `PUT` 0 / 100 (blue/green). Rollback = `PUT` the previous weights (or
   `POST /api/v1/deployments/{id}/rollback`); the old pods are still running, so it is instantaneous.
5. After the promotion, scale the old server to zero (`POST /api/v1/deployments/{id}/stop`) or delete its manifests.

The exact request body shape of the routes endpoint is defined by the deployment service (CONTRACT.md only states
`PUT /api/v1/endpoints/{id}/routes (weights sum to 100)`); adjust the JSON above if the field names differ.
