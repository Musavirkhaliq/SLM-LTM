import { estimateTraining } from "@/lib/finetune";
import { hasRole } from "@/lib/rbac";
import { decodeJwt } from "@/lib/api/token";
import type {
  ApiKey,
  ApiKeyCreateInput,
  ApiKeyCreated,
  ChatCompletionRequest,
  CostGroupBy,
  CostSummary,
  Dataset,
  Deployment,
  Evaluation,
  Model,
  ModelVersion,
  RagDocument,
  RagIndex,
  RagMatch,
  Role,
  TokenPair,
  TrainingJob,
  TrainingMetric,
} from "@/lib/api/types";
import { buildMockReply, createMockChatStream, estimateTokens } from "./chat";
import {
  ALERTS,
  API_KEYS,
  DATASETS,
  DEPLOYMENTS,
  EVALUATIONS,
  EVAL_RESULTS,
  EVAL_SIM,
  JOBS,
  JOB_SIM,
  KIND,
  MODELS,
  ORGS,
  PROJECTS,
  PROVISIONING_DONE,
  RAG_DOCS,
  RAG_INDEXES,
  RAG_PASSAGES,
  T0,
  USERS,
  VERSIONS,
  activeVersion,
  synthResult,
  type MockUser,
} from "./db";
import { gpuStats, seriesFor } from "./metrics";
import { hash01, liveId, mkId, randomString, sleep } from "./rand";

/* ---------------------------------------------------------------- helpers --- */
const JSON_HEADERS = { "Content-Type": "application/json" };

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
}
function page<T>(items: T[]) {
  return json({ items, next_cursor: null });
}
function problem(status: number, title: string, detail?: string): Response {
  return new Response(JSON.stringify({ type: "about:blank", title, status, detail: detail ?? title }), {
    status,
    headers: { "Content-Type": "application/problem+json" },
  });
}
const noContent = () => new Response(null, { status: 204 });

class HttpError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    readonly detail?: string,
  ) {
    super(title);
  }
}

function b64url(obj: unknown): string {
  const bytes = new TextEncoder().encode(JSON.stringify(obj));
  let bin = "";
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const ACCESS_TTL_S = 900;

function roleFor(user: MockUser, orgId: string): Role {
  if (user.is_platform_admin) return "platform_admin";
  return user.memberships.find((m) => m.org_id === orgId)?.role ?? "viewer";
}

function issueTokens(user: MockUser, orgId: string): TokenPair {
  const now = Math.floor(Date.now() / 1000);
  const access = `${b64url({ alg: "none", typ: "JWT" })}.${b64url({
    sub: user.id,
    org: orgId,
    role: roleFor(user, orgId),
    iat: now,
    exp: now + ACCESS_TTL_S,
    jti: randomString(12),
  })}.mock`;
  const refresh = `rt_${b64url({ u: user.id, o: orgId, e: now + 30 * 86400, n: randomString(8) })}`;
  return { access_token: access, refresh_token: refresh, expires_in: ACCESS_TTL_S };
}

interface Claims extends Record<string, unknown> {
  sub: string;
  org: string;
  role: Role;
  exp: number;
}

interface Ctx {
  params: Record<string, string>;
  query: URLSearchParams;
  body: unknown;
  form: FormData | null;
  claims: Claims | null;
  signal?: AbortSignal | null;
  now: number;
}

function requireRole(ctx: Ctx, min: Role) {
  if (!ctx.claims) throw new HttpError(401, "Unauthorized", "Missing or expired access token");
  if (!hasRole(ctx.claims.role, min)) {
    throw new HttpError(403, "Forbidden", `Your role (${ctx.claims.role}) cannot perform this action; ${min} or higher is required.`);
  }
}
function requireAuth(ctx: Ctx) {
  if (!ctx.claims) throw new HttpError(401, "Unauthorized", "Missing or expired access token");
}

const orgOf = (ctx: Ctx) => ORGS.find((o) => o.id === ctx.claims?.org) ?? ORGS[0]!;
const iso = (ms: number) => new Date(ms).toISOString();
const asObj = (b: unknown): Record<string, unknown> => (b && typeof b === "object" ? (b as Record<string, unknown>) : {});
const str = (v: unknown, fallback = "") => (typeof v === "string" ? v : fallback);
const num = (v: unknown, fallback: number) => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

/* ------------------------------------------------------------ state syncing --- */
function syncDeployments(now: number) {
  for (const d of DEPLOYMENTS) {
    const done = PROVISIONING_DONE[d.id];
    if ((d.status === "provisioning" || d.status === "rolling_back") && done && now >= done) {
      d.status = "running";
      d.replicas_current = d.replicas_min;
      d.updated_at = iso(now);
      delete PROVISIONING_DONE[d.id];
    }
    if (d.status === "running") {
      const noise = (hash01(Math.floor(now / 4000), d.id.charCodeAt(d.id.length - 1)) - 0.5) * 0.08;
      const baseP95 = (d.vllm_args as { _p95?: number })._p95 ?? 260;
      d.p95_latency_ms = Math.round(baseP95 * (1 + noise));
    } else if (d.status !== "provisioning") {
      d.p95_latency_ms = null;
    }
  }
}
// remember each seed deployment's baseline latency
DEPLOYMENTS.forEach((d) => {
  if (d.p95_latency_ms) (d.vllm_args as Record<string, unknown>)._p95 = d.p95_latency_ms;
});

function stepOf(job: TrainingJob, now: number): number {
  const sim = JOB_SIM[job.id]!;
  if (job.status === "succeeded") return sim.totalSteps;
  if (job.status === "queued") return 0;
  if (job.status === "running" && sim.startedMs) {
    return Math.min(sim.totalSteps, Math.max(0, Math.floor((now - sim.startedMs) / 1000 / sim.stepSec)));
  }
  return job.current_step ?? sim.failAtStep ?? 0;
}

function finalizeJob(job: TrainingJob) {
  const sim = JOB_SIM[job.id]!;
  if (sim.finalized) return;
  sim.finalized = true;
  const baseV = VERSIONS.find((v) => v.id === job.base_version_id);
  if (!baseV) return;
  const name = job.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const model: Model = {
    id: liveId(KIND.model),
    org_id: ORGS[0]!.id,
    name,
    family: MODELS.find((m) => m.id === baseV.model_id)?.family ?? null,
    description: `Fine-tuned from ${job.base_model_name} with ${job.method.toUpperCase()} on ${job.dataset_name}.`,
    license: MODELS.find((m) => m.id === baseV.model_id)?.license ?? null,
    source: "finetune",
    hf_repo: null,
    created_at: iso(Date.now()),
    archived_at: null,
  };
  MODELS.push(model);
  const lift: Record<string, number> = {};
  for (const [k, v] of Object.entries(baseV.benchmark_scores)) lift[k] = +Math.min(0.99, v + 0.012 + hash01(k.length, 3) * 0.02).toFixed(3);
  const version: ModelVersion = {
    ...baseV,
    id: liveId(KIND.version),
    model_id: model.id,
    version: 1,
    status: "active",
    base_version_id: baseV.id,
    training_job_id: job.id,
    benchmark_scores: lift,
    artifact_uri: `s3://models/${name}/v1`,
    created_at: iso(Date.now()),
  };
  VERSIONS.push(version);
  job.output_version_id = version.id;
}

function syncJobs(now: number) {
  for (const job of JOBS) {
    const sim = JOB_SIM[job.id]!;
    if (job.status === "queued" && sim.queuedUntil && now >= sim.queuedUntil) {
      job.status = "running";
      sim.startedMs = sim.queuedUntil;
      job.started_at = iso(sim.queuedUntil);
    }
    if (job.status === "running" && sim.startedMs) {
      const step = stepOf(job, now);
      if (sim.failAtStep && step >= sim.failAtStep) {
        job.status = "failed";
        job.error = "CUDA out of memory. Reduce batch size or sequence length.";
        job.finished_at = iso(now);
      } else if (step >= sim.totalSteps) {
        job.status = "succeeded";
        job.finished_at = iso(sim.startedMs + sim.totalSteps * sim.stepSec * 1000);
        finalizeJob(job);
      }
    }
    const step = stepOf(job, now);
    job.current_step = step;
    job.total_steps = sim.totalSteps;
    job.total_tokens = sim.totalTokens;
    job.tokens_processed = Math.round((step / sim.totalSteps) * sim.totalTokens);
    job.cost_so_far_usd = +((job.est_cost_usd ?? 0) * (sim.totalSteps ? step / sim.totalSteps : 0)).toFixed(2);
  }
}

function syncRag(now: number) {
  for (const idx of RAG_INDEXES) {
    if (idx.status === "creating" && now - Date.parse(idx.created_at) > 12_000) idx.status = "ready";
  }
  for (const doc of RAG_DOCS) {
    const age = now - Date.parse(doc.created_at);
    if (doc.status === "queued" && age > 2500) doc.status = "running";
    if (doc.status === "running" && age > 6500) {
      doc.status = "succeeded";
      doc.chunk_count = doc.chunk_count || 40 + Math.floor(hash01(doc.filename.length, 9) * 120);
      const idx = RAG_INDEXES.find((i) => i.id === doc.index_id);
      if (idx) {
        idx.doc_count += 1;
        idx.chunk_count += doc.chunk_count;
      }
    }
  }
}

function syncEvaluations(now: number) {
  for (const ev of EVALUATIONS) {
    const sim = EVAL_SIM[ev.id];
    if (!sim) continue;
    if (ev.status === "queued" && now > sim.readyAt - 4500) ev.status = "running";
    if ((ev.status === "running" || ev.status === "queued") && now >= sim.readyAt) {
      ev.status = "succeeded";
      ev.finished_at = iso(now);
      for (const vid of ev.model_version_ids) {
        const v = VERSIONS.find((x) => x.id === vid);
        if (v && !EVAL_RESULTS.some((r) => r.evaluation_id === ev.id && r.model_version_id === vid)) {
          EVAL_RESULTS.push(synthResult(ev.id, v, ev.id.length));
        }
      }
      delete EVAL_SIM[ev.id];
    }
  }
}

function syncAll(now: number) {
  syncDeployments(now);
  syncJobs(now);
  syncRag(now);
  syncEvaluations(now);
}
syncAll(T0);

/* ----------------------------------------------------------------- routes --- */
type Handler = (ctx: Ctx) => Response | Promise<Response>;
interface Route {
  method: string;
  re: RegExp;
  keys: string[];
  handler: Handler;
}
const routes: Route[] = [];
function route(method: string, pattern: string, handler: Handler) {
  const keys: string[] = [];
  const re = new RegExp(
    "^" +
      pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/:([a-z_]+)/g, (_m, k: string) => {
        keys.push(k);
        return "([^/]+)";
      }) +
      "/?$",
  );
  routes.push({ method, re, keys, handler });
}

const P = "/api/v1";

/* auth */
route("POST", `${P}/auth/login`, (ctx) => {
  const b = asObj(ctx.body);
  const email = str(b.email).trim().toLowerCase();
  const user = USERS.find((u) => u.email === email);
  if (!user || !str(b.password)) throw new HttpError(401, "Invalid credentials", "Incorrect email or password.");
  const orgId = user.memberships[0]!.org_id;
  return json(issueTokens(user, orgId));
});
route("POST", `${P}/auth/register`, (ctx) => {
  const b = asObj(ctx.body);
  const email = str(b.email).trim().toLowerCase();
  if (USERS.some((u) => u.email === email)) throw new HttpError(409, "Conflict", "An account with that email already exists.");
  const org = { id: liveId(KIND.org), slug: str(b.org_name, "my-org").toLowerCase().replace(/\s+/g, "-"), name: str(b.org_name, "My Organization"), load: 0.2 };
  ORGS.push(org);
  const user: MockUser = {
    id: liveId(KIND.user),
    email,
    display_name: str(b.display_name, email),
    is_platform_admin: false,
    memberships: [{ org_id: org.id, org_name: org.name, org_slug: org.slug, role: "org_admin" }],
  };
  USERS.push(user);
  return json(issueTokens(user, org.id), 201);
});
route("POST", `${P}/auth/refresh`, (ctx) => {
  const rt = str(asObj(ctx.body).refresh_token);
  const payload = rt.startsWith("rt_") ? decodeJwt<{ u: string; o: string; e: number }>(`x.${rt.slice(3)}.y`) : null;
  const user = payload && USERS.find((u) => u.id === payload.u);
  if (!payload || !user || payload.e < Date.now() / 1000) throw new HttpError(401, "Invalid refresh token", "The refresh token is invalid or expired.");
  return json(issueTokens(user, payload.o));
});
route("POST", `${P}/auth/logout`, () => noContent());
route("GET", `${P}/me`, (ctx) => {
  requireAuth(ctx);
  const user = USERS.find((u) => u.id === ctx.claims!.sub);
  if (!user) throw new HttpError(401, "Unauthorized", "Unknown user");
  return json({
    user: { id: user.id, email: user.email, display_name: user.display_name, is_platform_admin: user.is_platform_admin },
    memberships: user.memberships,
    active_org_id: ctx.claims!.org,
  });
});
route("POST", `${P}/orgs/switch`, (ctx) => {
  requireAuth(ctx);
  const user = USERS.find((u) => u.id === ctx.claims!.sub)!;
  const orgId = str(asObj(ctx.body).org_id);
  if (!user.is_platform_admin && !user.memberships.some((m) => m.org_id === orgId)) throw new HttpError(403, "Forbidden", "You are not a member of that organization.");
  return json(issueTokens(user, orgId));
});

/* projects */
route("GET", `${P}/projects`, (ctx) => {
  requireAuth(ctx);
  return page(PROJECTS);
});

/* api keys */
route("GET", `${P}/api-keys`, (ctx) => {
  requireAuth(ctx);
  return page([...API_KEYS].sort((a, b) => b.created_at.localeCompare(a.created_at)));
});
function newSecret(): { prefix: string; key: string } {
  const prefix = `slm_${randomString(5)}`;
  return { prefix, key: `slm_${randomString(8)}_${randomString(40)}` };
}
route("POST", `${P}/api-keys`, (ctx) => {
  const b = asObj(ctx.body) as unknown as ApiKeyCreateInput;
  if (b.scope === "organization") requireRole(ctx, "org_admin");
  else requireRole(ctx, "developer");
  if (!b.name?.trim()) throw new HttpError(422, "Validation error", "name is required");
  if (b.scope === "project" && !b.project_id) throw new HttpError(422, "Validation error", "project_id is required for project-scoped keys");
  const secret = newSecret();
  const key: ApiKey = {
    id: liveId(KIND.apiKey),
    org_id: ctx.claims!.org,
    project_id: b.scope === "project" ? (b.project_id ?? null) : null,
    scope: b.scope,
    name: b.name.trim(),
    prefix: secret.key.slice(0, 9),
    rate_limit_rpm: b.rate_limit_rpm ?? null,
    expires_at: b.expires_at ?? null,
    rotated_from: null,
    grace_until: null,
    last_used_at: null,
    revoked_at: null,
    created_at: iso(Date.now()),
  };
  API_KEYS.unshift(key);
  return json({ ...key, key: secret.key } satisfies ApiKeyCreated, 201);
});
route("POST", `${P}/api-keys/:id/rotate`, (ctx) => {
  const old = API_KEYS.find((k) => k.id === ctx.params.id);
  if (!old) throw new HttpError(404, "Not found", "API key not found");
  requireRole(ctx, old.scope === "organization" ? "org_admin" : "developer");
  if (old.revoked_at) throw new HttpError(409, "Conflict", "Revoked keys cannot be rotated.");
  const secret = newSecret();
  old.grace_until = iso(Date.now() + 24 * 3_600_000);
  const key: ApiKey = { ...old, id: liveId(KIND.apiKey), prefix: secret.key.slice(0, 9), rotated_from: old.id, grace_until: null, last_used_at: null, created_at: iso(Date.now()) };
  API_KEYS.unshift(key);
  return json({ ...key, key: secret.key } satisfies ApiKeyCreated, 201);
});
route("DELETE", `${P}/api-keys/:id`, (ctx) => {
  const k = API_KEYS.find((x) => x.id === ctx.params.id);
  if (!k) throw new HttpError(404, "Not found", "API key not found");
  requireRole(ctx, k.scope === "organization" ? "org_admin" : "developer");
  k.revoked_at = iso(Date.now());
  return noContent();
});
route("GET", `${P}/api-keys/:id/usage`, (ctx) => {
  requireAuth(ctx);
  const k = API_KEYS.find((x) => x.id === ctx.params.id);
  if (!k) throw new HttpError(404, "Not found", "API key not found");
  const seed = k.id.charCodeAt(k.id.length - 1) + k.name.length;
  const points = Array.from({ length: 24 }, (_, i) => {
    const t = ctx.now - (23 - i) * 3_600_000;
    const active = k.revoked_at ? 0 : 1;
    const requests = Math.round(active * (120 + seed * 6) * (0.55 + 0.45 * Math.sin(i / 3 + seed)) * (0.8 + hash01(i, seed) * 0.4));
    return { t: iso(t), requests: Math.max(0, requests), tokens: Math.max(0, requests * (380 + Math.round(hash01(i, seed + 1) * 90))) };
  });
  return json({ api_key_id: k.id, total_requests: points.reduce((s, p) => s + p.requests, 0), total_tokens: points.reduce((s, p) => s + p.tokens, 0), points });
});

/* models */
const embedModel = (m: Model): Model => {
  const versions = VERSIONS.filter((v) => v.model_id === m.id);
  return { ...m, latest_version: activeVersion(m.id) ?? null, version_count: versions.length };
};
route("GET", `${P}/models`, (ctx) => {
  requireAuth(ctx);
  return page(MODELS.filter((m) => !m.archived_at).map(embedModel));
});
route("POST", `${P}/models`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const b = asObj(ctx.body);
  const name = str(b.name).trim();
  if (!name) throw new HttpError(422, "Validation error", "name is required");
  if (MODELS.some((m) => m.name === name)) throw new HttpError(409, "Conflict", `A model named "${name}" already exists.`);
  const model: Model = {
    id: liveId(KIND.model),
    org_id: ctx.claims!.org,
    name,
    family: (b.family as string | null) ?? null,
    description: (b.description as string | null) ?? null,
    license: (b.license as string | null) ?? null,
    source: (b.source as Model["source"]) ?? "huggingface",
    hf_repo: (b.hf_repo as string | null) ?? null,
    created_at: iso(Date.now()),
    archived_at: null,
  };
  MODELS.push(model);
  VERSIONS.push({
    id: liveId(KIND.version),
    model_id: model.id,
    version: 1,
    status: "registered",
    format: "safetensors",
    param_count: null,
    quantization: "none",
    context_length: 8192,
    artifact_uri: model.hf_repo ? `hf://${model.hf_repo}` : `s3://models/${name}/v1`,
    base_version_id: null,
    training_job_id: null,
    benchmark_scores: {},
    created_at: iso(Date.now()),
  });
  return json(embedModel(model), 201);
});
route("POST", `${P}/models/:id/archive`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const m = MODELS.find((x) => x.id === ctx.params.id);
  if (!m) throw new HttpError(404, "Not found", "Model not found");
  m.archived_at = iso(Date.now());
  return json(m);
});
route("GET", `${P}/models/:id/versions`, (ctx) => {
  requireAuth(ctx);
  return page(VERSIONS.filter((v) => v.model_id === ctx.params.id).sort((a, b) => b.version - a.version));
});
route("POST", `${P}/models/:id/versions/:v/rollback`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const target = VERSIONS.find((x) => x.model_id === ctx.params.id && x.version === Number(ctx.params.v));
  if (!target) throw new HttpError(404, "Not found", "Version not found");
  VERSIONS.filter((x) => x.model_id === ctx.params.id && x.status === "active").forEach((x) => (x.status = "registered"));
  target.status = "active";
  return json(target);
});

/* deployments */
route("GET", `${P}/deployments`, (ctx) => {
  requireAuth(ctx);
  syncDeployments(ctx.now);
  return page(DEPLOYMENTS);
});
route("POST", `${P}/deployments`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const b = asObj(ctx.body);
  const name = str(b.name).trim();
  const version = VERSIONS.find((v) => v.id === b.model_version_id);
  if (!name || !version) throw new HttpError(422, "Validation error", "name and a valid model_version_id are required");
  if (DEPLOYMENTS.some((d) => d.name === name)) throw new HttpError(409, "Conflict", `A deployment named "${name}" already exists.`);
  const model = MODELS.find((m) => m.id === version.model_id)!;
  const dep: Deployment = {
    id: liveId(KIND.deployment),
    org_id: ctx.claims!.org,
    project_id: null,
    name,
    model_version_id: version.id,
    kind: (b.kind as Deployment["kind"]) ?? "private",
    target: (b.target as Deployment["target"]) ?? "dgx_spark",
    strategy: (b.strategy as Deployment["strategy"]) ?? "direct",
    status: "provisioning",
    replicas_min: num(b.replicas_min, 1),
    replicas_max: num(b.replicas_max, 2),
    gpu_count: num(b.gpu_count, 1),
    vllm_args: { ...(asObj(b.vllm_args)), _p95: 180 + Math.round(((version.param_count ?? 7e9) / 1e9) * 14) },
    internal_url: `http://vllm-${name}.slm.svc:8000`,
    created_at: iso(Date.now()),
    updated_at: iso(Date.now()),
    stopped_at: null,
    model_name: model.name,
    model_version: version.version,
    endpoint_alias: name,
    p95_latency_ms: null,
    replicas_current: 0,
  };
  DEPLOYMENTS.unshift(dep);
  PROVISIONING_DONE[dep.id] = Date.now() + 7000;
  return json(dep, 201);
});
function findDep(ctx: Ctx): Deployment {
  const d = DEPLOYMENTS.find((x) => x.id === ctx.params.id);
  if (!d) throw new HttpError(404, "Not found", "Deployment not found");
  return d;
}
route("POST", `${P}/deployments/:id/start`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const d = findDep(ctx);
  if (d.status === "running") throw new HttpError(409, "Conflict", "Deployment is already running.");
  d.status = "provisioning";
  d.stopped_at = null;
  d.updated_at = iso(Date.now());
  PROVISIONING_DONE[d.id] = Date.now() + 5000;
  return json(d);
});
route("POST", `${P}/deployments/:id/stop`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const d = findDep(ctx);
  d.status = "stopped";
  d.replicas_current = 0;
  d.stopped_at = iso(Date.now());
  d.updated_at = iso(Date.now());
  delete PROVISIONING_DONE[d.id];
  return json(d);
});
route("POST", `${P}/deployments/:id/scale`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const d = findDep(ctx);
  const b = asObj(ctx.body);
  const min = num(b.replicas_min, d.replicas_min);
  const max = num(b.replicas_max, d.replicas_max);
  if (min < 0 || max < min) throw new HttpError(422, "Validation error", "replicas_max must be >= replicas_min");
  d.replicas_min = min;
  d.replicas_max = max;
  d.gpu_count = num(b.gpu_count, d.gpu_count);
  if (d.status === "running") d.replicas_current = Math.min(max, Math.max(min, d.replicas_current ?? min));
  d.updated_at = iso(Date.now());
  return json(d);
});
route("POST", `${P}/deployments/:id/rollback`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const d = findDep(ctx);
  d.status = "rolling_back";
  d.updated_at = iso(Date.now());
  PROVISIONING_DONE[d.id] = Date.now() + 4000;
  return json(d);
});
route("DELETE", `${P}/deployments/:id`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const d = findDep(ctx);
  DEPLOYMENTS.splice(DEPLOYMENTS.indexOf(d), 1);
  return noContent();
});

/* datasets */
route("GET", `${P}/datasets`, (ctx) => {
  requireAuth(ctx);
  return page([...DATASETS].sort((a, b) => b.created_at.localeCompare(a.created_at)));
});
route("POST", `${P}/datasets`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const file = ctx.form?.get("file");
  if (!(file instanceof File)) throw new HttpError(422, "Validation error", "multipart field `file` is required");
  const ext = file.name.split(".").pop()?.toLowerCase();
  const format = ext === "csv" ? "csv" : ext === "parquet" ? "parquet" : "jsonl";
  const size = Math.max(file.size, 1);
  const rows = Math.max(1, Math.round(size / 420));
  const ds: Dataset = {
    id: liveId(KIND.dataset),
    name: str(ctx.form?.get("name"), file.name) || file.name,
    purpose: ctx.form?.get("purpose") === "eval" ? "eval" : "finetune",
    format,
    storage_uri: `s3://datasets/${file.name}`,
    row_count: rows,
    token_count: rows * 760,
    size_bytes: size,
    schema_info: { columns: format === "csv" ? ["prompt", "completion"] : ["messages"] },
    created_at: iso(Date.now()),
  };
  DATASETS.unshift(ds);
  return json(ds, 201);
});

/* training jobs */
route("GET", `${P}/training-jobs`, (ctx) => {
  requireAuth(ctx);
  syncJobs(ctx.now);
  return page([...JOBS].sort((a, b) => b.created_at.localeCompare(a.created_at)));
});
route("POST", `${P}/training-jobs`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const b = asObj(ctx.body);
  const baseV = VERSIONS.find((v) => v.id === b.base_version_id);
  const dset = DATASETS.find((d) => d.id === b.dataset_id);
  if (!baseV || !dset) throw new HttpError(422, "Validation error", "valid base_version_id and dataset_id are required");
  const hp = asObj(b.hyperparams) as unknown as TrainingJob["hyperparams"];
  const gpu = num(b.gpu_count, 1);
  const steps = Math.min(700, Math.max(240, Math.ceil(((dset.row_count ?? 5000) * hp.epochs) / (hp.batch_size * gpu * 4))));
  const id = liveId(KIND.job);
  const totalTokens = (dset.token_count ?? 1_000_000) * hp.epochs;
  JOB_SIM[id] = { startedMs: null, stepSec: 0.45, totalSteps: steps, totalTokens, gpuHourUsd: 3.2, finalized: false, queuedUntil: Date.now() + 2500 };
  const model = MODELS.find((m) => m.id === baseV.model_id)!;
  const job: TrainingJob = {
    id,
    name: str(b.name, "untitled-job"),
    base_version_id: baseV.id,
    dataset_id: dset.id,
    method: (b.method as TrainingJob["method"]) ?? "qlora",
    hyperparams: hp,
    gpu_count: gpu,
    status: "queued",
    output_version_id: null,
    tokens_processed: 0,
    est_cost_usd: null,
    error: null,
    created_at: iso(Date.now()),
    started_at: null,
    finished_at: null,
    base_model_name: model.name,
    dataset_name: dset.name,
    total_steps: steps,
    current_step: 0,
    total_tokens: totalTokens,
    cost_so_far_usd: 0,
  };
  job.est_cost_usd = +estimateTraining({
    datasetTokens: dset.token_count ?? 1_000_000,
    paramsBillions: (baseV.param_count ?? 7e9) / 1e9,
    method: job.method,
    epochs: hp.epochs,
    gpuCount: gpu,
  }).costUsd.toFixed(2);
  JOBS.unshift(job);
  return json(job, 201);
});
function jobSeries(job: TrainingJob, now: number): TrainingMetric[] {
  syncJobs(now);
  const sim = JOB_SIM[job.id]!;
  const current = job.current_step ?? 0;
  const stride = Math.max(1, Math.ceil(current / 150));
  const seed = job.id.charCodeAt(job.id.length - 1);
  const out: TrainingMetric[] = [];
  for (let s = 0; s <= current; s += stride) {
    const base = 0.34 + 1.95 * Math.exp(-s / (sim.totalSteps * 0.17));
    const loss = base + (hash01(s, seed) - 0.5) * 0.07 * (1 + 1.5 * Math.exp(-s / 120));
    const evalPoint = s % Math.max(stride, 25) === 0;
    out.push({
      step: s,
      loss: +loss.toFixed(4),
      eval_loss: evalPoint ? +(base + 0.07 + (hash01(s, seed + 5) - 0.5) * 0.02).toFixed(4) : null,
      lr: Number((job.hyperparams.learning_rate * 0.5 * (1 + Math.cos((Math.PI * s) / sim.totalSteps))).toExponential(3)),
      tokens: Math.round((s / sim.totalSteps) * sim.totalTokens),
      gpu_util: +(90 + (hash01(s, seed + 9) - 0.5) * 12).toFixed(1),
      recorded_at: iso(((sim.startedMs ?? now) as number) + s * sim.stepSec * 1000),
    });
  }
  return out;
}
route("GET", `${P}/training-jobs/:id/metrics`, (ctx) => {
  requireAuth(ctx);
  const job = JOBS.find((j) => j.id === ctx.params.id);
  if (!job) throw new HttpError(404, "Not found", "Training job not found");
  return page(jobSeries(job, ctx.now));
});
route("POST", `${P}/training-jobs/:id/cancel`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const job = JOBS.find((j) => j.id === ctx.params.id);
  if (!job) throw new HttpError(404, "Not found", "Training job not found");
  syncJobs(ctx.now);
  if (job.status === "queued" || job.status === "running") {
    job.current_step = stepOf(job, ctx.now);
    job.status = "cancelled";
    job.finished_at = iso(ctx.now);
  }
  return json(job);
});

/* RAG */
route("GET", `${P}/rag/indexes`, (ctx) => {
  requireAuth(ctx);
  syncRag(ctx.now);
  return page(RAG_INDEXES);
});
route("POST", `${P}/rag/indexes`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const b = asObj(ctx.body);
  const name = str(b.name).trim();
  if (!name) throw new HttpError(422, "Validation error", "name is required");
  if (RAG_INDEXES.some((i) => i.name === name)) throw new HttpError(409, "Conflict", `An index named "${name}" already exists.`);
  const idx: RagIndex = {
    id: liveId(KIND.ragIndex),
    project_id: null,
    name,
    backend: (b.backend as RagIndex["backend"]) ?? "qdrant",
    collection: name.replace(/[^a-z0-9]+/gi, "_").toLowerCase(),
    embedding_model: str(b.embedding_model, "bge-m3"),
    embedding_dim: num(b.embedding_dim, 1024),
    chunk_size: num(b.chunk_size, 512),
    chunk_overlap: num(b.chunk_overlap, 64),
    hybrid_search: b.hybrid_search !== false,
    reranker_model: (b.reranker_model as string | null) ?? null,
    status: "creating",
    doc_count: 0,
    chunk_count: 0,
    created_at: iso(Date.now()),
  };
  RAG_INDEXES.unshift(idx);
  return json(idx, 201);
});
route("GET", `${P}/rag/indexes/:id/documents`, (ctx) => {
  requireAuth(ctx);
  syncRag(ctx.now);
  return page(RAG_DOCS.filter((d) => d.index_id === ctx.params.id).sort((a, b) => b.created_at.localeCompare(a.created_at)));
});
route("POST", `${P}/rag/indexes/:id/documents`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const idx = RAG_INDEXES.find((i) => i.id === ctx.params.id);
  if (!idx) throw new HttpError(404, "Not found", "Index not found");
  const file = ctx.form?.get("file");
  if (!(file instanceof File)) throw new HttpError(422, "Validation error", "multipart field `file` is required");
  const doc: RagDocument = {
    id: liveId(KIND.ragDoc),
    index_id: idx.id,
    filename: file.name,
    mime_type: file.type || "application/octet-stream",
    status: "queued",
    chunk_count: Math.max(3, Math.round(file.size / Math.max(200, idx.chunk_size * 3.2))),
    error: null,
    created_at: iso(Date.now()),
  };
  RAG_DOCS.unshift(doc);
  return json(doc, 201);
});
route("POST", `${P}/rag/indexes/:id/query`, async (ctx) => {
  requireAuth(ctx);
  const idx = RAG_INDEXES.find((i) => i.id === ctx.params.id);
  if (!idx) throw new HttpError(404, "Not found", "Index not found");
  const b = asObj(ctx.body);
  const q = str(b.query).toLowerCase();
  const topK = num(b.top_k, 4);
  const thr = num(b.score_threshold, 0);
  const terms = q.split(/\W+/).filter((t) => t.length > 2);
  const scored: RagMatch[] = RAG_PASSAGES.map((p, i) => {
    const text = p.text.toLowerCase();
    const hits = terms.filter((t) => text.includes(t)).length;
    const score = Math.min(0.99, 0.35 + hits * 0.17 + hash01(i, q.length) * 0.08);
    return { chunk_id: mkId(KIND.ragDoc, 900 + i), document: p.document, score: +score.toFixed(3), text: p.text };
  })
    .filter((m) => m.score >= thr)
    .sort((a, b) => b.score - a.score)
    .slice(0, topK);
  await sleep(120, ctx.signal ?? undefined);
  return json({ matches: scored, latency_ms: 38 + Math.round(hash01(q.length, 4) * 60) });
});

/* evaluation */
route("GET", `${P}/evaluations`, (ctx) => {
  requireAuth(ctx);
  syncEvaluations(ctx.now);
  return page([...EVALUATIONS].sort((a, b) => b.created_at.localeCompare(a.created_at)));
});
route("POST", `${P}/evaluations`, (ctx) => {
  requireRole(ctx, "ml_engineer");
  const b = asObj(ctx.body);
  const ids = Array.isArray(b.model_version_ids) ? (b.model_version_ids as string[]) : [];
  const dset = DATASETS.find((d) => d.id === b.dataset_id);
  if (!ids.length || !dset) throw new HttpError(422, "Validation error", "dataset_id and at least one model_version_id are required");
  const ev: Evaluation = {
    id: liveId(KIND.evaluation),
    name: str(b.name, "Untitled evaluation"),
    dataset_id: dset.id,
    model_version_ids: ids,
    metrics: (b.metrics as string[]) ?? ["accuracy"],
    judge_model: (b.judge_model as string | null) ?? null,
    status: "queued",
    created_at: iso(Date.now()),
    finished_at: null,
    dataset_name: dset.name,
  };
  EVALUATIONS.unshift(ev);
  EVAL_SIM[ev.id] = { readyAt: Date.now() + 8000 };
  return json(ev, 201);
});
route("GET", `${P}/evaluations/compare`, (ctx) => {
  requireAuth(ctx);
  syncEvaluations(ctx.now);
  const ids = (ctx.query.get("ids") ?? "").split(",").filter(Boolean);
  return page(EVAL_RESULTS.filter((r) => ids.includes(r.evaluation_id)));
});
route("GET", `${P}/evaluations/:id/results`, (ctx) => {
  requireAuth(ctx);
  syncEvaluations(ctx.now);
  if (!EVALUATIONS.some((e) => e.id === ctx.params.id)) throw new HttpError(404, "Not found", "Evaluation not found");
  return page(EVAL_RESULTS.filter((r) => r.evaluation_id === ctx.params.id));
});

/* monitoring */
route("GET", `${P}/monitoring/overview`, (ctx) => {
  requireAuth(ctx);
  syncDeployments(ctx.now);
  const org = orgOf(ctx);
  const window = ctx.query.get("window") ?? "1h";
  const points = seriesFor(window, ctx.now, org.load);
  const running = DEPLOYMENTS.filter((d) => d.status === "running");
  const totalW = running.reduce((s, d) => s + d.gpu_count * (d.replicas_current ?? 1), 0) || 1;
  const cur = points[points.length - 1]!;
  const deployments = DEPLOYMENTS.map((d) => {
    const w = (d.gpu_count * (d.replicas_current ?? 1)) / totalW;
    const on = d.status === "running";
    return {
      deployment_id: d.id,
      name: d.name,
      status: d.status,
      requests_per_min: on ? Math.round(cur.requests_per_min * w) : 0,
      p95_latency_ms: on ? (d.p95_latency_ms ?? 0) : 0,
      error_rate: on ? +(cur.error_rate * (0.6 + hash01(d.name.length, 2))).toFixed(2) : 0,
      gpu_util: on ? +Math.min(99, cur.gpu_util * (0.7 + w * 1.8)).toFixed(1) : 0,
    };
  });
  const sizeOf = (name: string, weight: number) => ({
    model: name,
    requests: Math.round(184_000 * org.load * weight),
    tokens: Math.round(184_000 * org.load * weight * 412),
  });
  return json({
    window,
    points,
    gpus: gpuStats(ctx.now),
    deployments,
    usage_by_model: [
      sizeOf("phi-4-mini", 1),
      sizeOf("llama-3.1-8b", 0.78),
      sizeOf("acme-support-bot", 0.66),
      sizeOf("acme-sql-assistant", 0.34),
      sizeOf("phi-4", 0.18),
      sizeOf("qwen2.5-7b", 0.09),
    ],
    alerts: ALERTS.map((a) => ({ id: a.id, severity: a.severity, title: a.title, detail: a.detail, resource: a.resource, fired_at: iso(T0 - a.ago) })),
  });
});

/* cost */
const COST_WEIGHTS: Record<CostGroupBy, [string, number][]> = {
  project: [["Support Bot", 0.38], ["Doc Intelligence", 0.24], ["Internal Tools", 0.18], ["Research Sandbox", 0.12], ["Unassigned", 0.08]],
  deployment: [["phi4-mini-prod", 0.27], ["llama31-8b-shared", 0.22], ["support-bot-v2", 0.19], ["sql-assistant", 0.13], ["phi4-staging", 0.08], ["qwen25-7b-dev", 0.04], ["fine-tuning jobs", 0.07]],
  model: [["phi-4-mini", 0.26], ["llama-3.1-8b", 0.21], ["acme-support-bot", 0.19], ["acme-sql-assistant", 0.14], ["phi-4", 0.1], ["gemma-3-4b", 0.05], ["mistral-7b", 0.03], ["embeddings (bge-m3)", 0.02]],
  org: [["Acme AI", 0.7], ["Globex Labs", 0.3]],
};
const DAY_MS = 86_400_000;
function dailyCost(dayIndex: number, load: number): number {
  const date = new Date(dayIndex * DAY_MS);
  const weekday = date.getUTCDay();
  const weekend = weekday === 0 || weekday === 6 ? 0.62 : 1;
  const trend = 1 + (dayIndex % 90) * 0.0025;
  return +(310 * load * weekend * trend * (0.9 + hash01(dayIndex, 31) * 0.2)).toFixed(2);
}
function costData(ctx: Ctx) {
  const groupBy = (ctx.query.get("group_by") ?? "project") as CostGroupBy;
  const to = ctx.query.get("to") ?? iso(Date.now()).slice(0, 10);
  const from = ctx.query.get("from") ?? iso(Date.now() - 29 * DAY_MS).slice(0, 10);
  const startIdx = Math.floor(Date.parse(from) / DAY_MS);
  const endIdx = Math.min(Math.floor(Date.parse(to) / DAY_MS), startIdx + 365);
  const org = orgOf(ctx);
  const platform = ctx.claims?.role === "platform_admin";
  const daily: { day: string; usd: number }[] = [];
  for (let d = startIdx; d <= endIdx; d++) daily.push({ day: new Date(d * DAY_MS).toISOString().slice(0, 10), usd: dailyCost(d, platform && groupBy === "org" ? 1 : org.load) });
  const total = +daily.reduce((s, d) => s + d.usd, 0).toFixed(2);
  let weights = COST_WEIGHTS[groupBy];
  if (groupBy === "org" && !platform) weights = [[org.name, 1]];
  const items = weights.map(([label, w]) => ({ key: label.toLowerCase().replace(/[^a-z0-9]+/g, "-"), label, usd: +(total * w).toFixed(2) }));
  const prevTotal = +(total * (0.88 + hash01(startIdx, 5) * 0.14)).toFixed(2);
  const resources: [string, number][] = [["gpu_hour", 0.62], ["output_token_1k", 0.16], ["input_token_1k", 0.12], ["train_token_1k", 0.07], ["storage_gb_month", 0.03]];
  const summary: CostSummary = {
    group_by: groupBy,
    from,
    to,
    total_usd: total,
    previous_total_usd: prevTotal,
    items,
    daily,
    by_resource: resources.map(([resource, w]) => ({ resource, usd: +(total * w).toFixed(2) })),
  };
  return summary;
}
route("GET", `${P}/cost/summary`, (ctx) => {
  requireRole(ctx, "org_admin");
  return json(costData(ctx));
});
route("GET", `${P}/cost/report.csv`, (ctx) => {
  requireRole(ctx, "org_admin");
  const s = costData(ctx);
  const lines = ["day,group_by,key,label,usd"];
  for (const d of s.daily) {
    for (const it of s.items) {
      lines.push([d.day, s.group_by, it.key, `"${it.label}"`, ((d.usd * it.usd) / (s.total_usd || 1)).toFixed(4)].join(","));
    }
  }
  return new Response(lines.join("\n") + "\n", { status: 200, headers: { "Content-Type": "text/csv", "Content-Disposition": `attachment; filename="cost-${s.from}-${s.to}.csv"` } });
});

/* OpenAI-compatible */
route("GET", "/v1/models", (ctx) => {
  requireAuth(ctx);
  syncDeployments(ctx.now);
  const aliases = new Set<string>();
  DEPLOYMENTS.filter((d) => d.status === "running").forEach((d) => aliases.add(d.endpoint_alias ?? d.name));
  ["phi-4", "gemma-3-4b", "qwen2.5-7b", "mistral-7b"].forEach((a) => aliases.add(a));
  return json({
    object: "list",
    data: [...aliases].map((id) => ({ id, object: "model", created: Math.floor(T0 / 1000), owned_by: id.includes("support") || id.includes("sql") ? "acme-ai" : "slm-studio" })),
  });
});
route("POST", "/v1/chat/completions", async (ctx) => {
  requireRole(ctx, "developer");
  const req = ctx.body as ChatCompletionRequest;
  if (!req?.model || !Array.isArray(req.messages)) throw new HttpError(400, "Bad request", "model and messages are required");
  if (req.stream) {
    return new Response(createMockChatStream(req, ctx.signal), {
      status: 200,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-SLM-Route": "mock", "X-Request-ID": randomString(16) },
    });
  }
  const reply = buildMockReply(req);
  const pt = estimateTokens(req.messages.map((m) => m.content ?? "").join("\n"));
  const ct = estimateTokens(reply.content || reply.toolCall?.arguments || "");
  return json({
    id: `chatcmpl-${randomString(10)}`,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model: req.model,
    choices: [
      {
        index: 0,
        finish_reason: reply.toolCall ? "tool_calls" : "stop",
        message: {
          role: "assistant",
          content: reply.content || null,
          ...(reply.toolCall ? { tool_calls: [{ id: `call_${randomString(8)}`, type: "function", function: reply.toolCall }] } : {}),
        },
      },
    ],
    usage: { prompt_tokens: pt, completion_tokens: ct, total_tokens: pt + ct },
  });
});

/* ---------------------------------------------------------------- entrypoint --- */
function verifyAccess(init?: RequestInit): Claims | null {
  const headers = new Headers(init?.headers);
  const auth = headers.get("Authorization");
  if (!auth?.startsWith("Bearer ")) return null;
  const token = auth.slice(7);
  if (token.startsWith("slm_")) {
    // API key (Playground override): treat as org-admin of the first org
    return { sub: USERS[0]!.id, org: ORGS[0]!.id, role: "developer", exp: Date.now() / 1000 + 3600 };
  }
  const claims = decodeJwt<Claims>(token);
  if (!claims || typeof claims.exp !== "number" || claims.exp < Date.now() / 1000) return null;
  return claims;
}

/**
 * A drop-in `fetch` replacement serving the full CONTRACT.md surface from in-memory seeded
 * data. Used when NEXT_PUBLIC_USE_MOCKS=true (or no API base is configured).
 */
export async function mockFetch(input: string, init?: RequestInit): Promise<Response> {
  const url = new URL(input, "http://mock.local");
  const method = (init?.method ?? "GET").toUpperCase();
  const signal = init?.signal ?? null;
  const isStream = url.pathname === "/v1/chat/completions";
  try {
    if (!isStream) await sleep(70 + Math.random() * 140, signal ?? undefined);

    const match = routes
      .map((r) => ({ r, m: r.method === method ? r.re.exec(url.pathname) : null }))
      .find((x) => x.m);
    if (!match || !match.m) {
      const known = routes.some((r) => r.re.test(url.pathname));
      return problem(known ? 405 : 404, known ? "Method not allowed" : "Not found", `${method} ${url.pathname}`);
    }
    const params: Record<string, string> = {};
    match.r.keys.forEach((k, i) => (params[k] = decodeURIComponent(match.m![i + 1]!)));

    let body: unknown = undefined;
    let form: FormData | null = null;
    if (init?.body instanceof FormData) form = init.body;
    else if (typeof init?.body === "string" && init.body) {
      try {
        body = JSON.parse(init.body);
      } catch {
        return problem(400, "Bad request", "Body is not valid JSON");
      }
    }

    const now = Date.now();
    const ctx: Ctx = { params, query: url.searchParams, body, form, claims: verifyAccess(init), signal, now };
    const isPublicAuth = url.pathname.startsWith(`${P}/auth/`) && !url.pathname.endsWith("/logout");
    if (!isPublicAuth && !ctx.claims) return problem(401, "Unauthorized", "Missing or expired access token");
    return await match.r.handler(ctx);
  } catch (e) {
    if (e instanceof HttpError) {
      if (url.pathname.startsWith("/v1/")) {
        return json({ error: { message: e.detail ?? e.title, type: e.status === 401 ? "authentication_error" : "invalid_request_error", code: String(e.status) } }, e.status);
      }
      return problem(e.status, e.title, e.detail);
    }
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    return problem(500, "Internal mock error", e instanceof Error ? e.message : String(e));
  }
}

