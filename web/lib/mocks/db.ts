import type {
  ApiKey,
  Dataset,
  Deployment,
  Evaluation,
  EvaluationResult,
  Membership,
  Model,
  ModelVersion,
  OrgRole,
  Project,
  RagDocument,
  RagIndex,
  TrainingJob,
} from "@/lib/api/types";
import { mkId } from "./rand";

/* In-memory, seeded mock database. Mutations made through the mock fetch layer live here
 * until the page is reloaded. Everything is deterministic apart from "now". */

export const T0 = Date.now();
const MIN = 60_000;
const HOUR = 3_600_000;
const DAY = 86_400_000;
const ago = (ms: number) => new Date(T0 - ms).toISOString();

export const KIND = {
  org: 1,
  user: 2,
  project: 3,
  model: 4,
  version: 5,
  deployment: 6,
  dataset: 7,
  job: 8,
  ragIndex: 9,
  ragDoc: 10,
  evaluation: 11,
  apiKey: 12,
} as const;

/* ------------------------------------------------------------ orgs & users --- */
export const ORGS = [
  { id: mkId(KIND.org, 1), slug: "acme-ai", name: "Acme AI", load: 1 },
  { id: mkId(KIND.org, 2), slug: "globex-labs", name: "Globex Labs", load: 0.42 },
];

export interface MockUser {
  id: string;
  email: string;
  display_name: string;
  is_platform_admin: boolean;
  memberships: Membership[];
}

const mem = (orgIdx: number, role: OrgRole): Membership => ({
  org_id: ORGS[orgIdx]!.id,
  org_name: ORGS[orgIdx]!.name,
  org_slug: ORGS[orgIdx]!.slug,
  role,
});

export const USERS: MockUser[] = [
  { id: mkId(KIND.user, 1), email: "admin@acme.io", display_name: "Ava Admin", is_platform_admin: false, memberships: [mem(0, "org_admin"), mem(1, "developer")] },
  { id: mkId(KIND.user, 2), email: "ml@acme.io", display_name: "Miles Engineer", is_platform_admin: false, memberships: [mem(0, "ml_engineer"), mem(1, "viewer")] },
  { id: mkId(KIND.user, 3), email: "dev@acme.io", display_name: "Dana Developer", is_platform_admin: false, memberships: [mem(0, "developer")] },
  { id: mkId(KIND.user, 4), email: "viewer@acme.io", display_name: "Vic Viewer", is_platform_admin: false, memberships: [mem(0, "viewer")] },
  { id: mkId(KIND.user, 5), email: "platform@slm.studio", display_name: "Pat Platform", is_platform_admin: true, memberships: [mem(0, "org_admin"), mem(1, "org_admin")] },
];

export const DEMO_ACCOUNTS = [
  { email: "admin@acme.io", label: "Org Admin", role: "org_admin" },
  { email: "ml@acme.io", label: "ML Engineer", role: "ml_engineer" },
  { email: "dev@acme.io", label: "Developer", role: "developer" },
  { email: "viewer@acme.io", label: "Viewer", role: "viewer" },
  { email: "platform@slm.studio", label: "Platform Admin", role: "platform_admin" },
] as const;

export const PROJECTS: Project[] = [
  ["support-bot", "Support Bot", "Customer support assistant and ticket triage"],
  ["doc-intelligence", "Doc Intelligence", "Contract and policy extraction with RAG"],
  ["internal-tools", "Internal Tools", "Text-to-SQL and developer copilots"],
  ["research-sandbox", "Research Sandbox", "Experiments and model bake-offs"],
].map(([slug, name, description], i) => ({
  id: mkId(KIND.project, i + 1),
  org_id: ORGS[0]!.id,
  slug: slug!,
  name: name!,
  description: description!,
  created_at: ago((120 - i * 20) * DAY),
}));

/* ------------------------------------------------------------------ models --- */
type Bench = Record<string, number>;
const bench = (mmlu: number, gsm8k: number, humaneval: number, arc: number, truthfulqa: number): Bench => ({
  mmlu,
  gsm8k,
  humaneval,
  arc_challenge: arc,
  truthfulqa,
});

interface ModelSeed {
  name: string;
  family: string;
  description: string;
  license: string;
  source: "huggingface" | "finetune";
  hf_repo: string | null;
  org: boolean;
  created: number;
  versions: Omit<ModelVersion, "id" | "model_id" | "created_at">[];
}

const base = (v: Partial<ModelVersion> & Pick<ModelVersion, "version" | "param_count" | "benchmark_scores" | "artifact_uri">) => ({
  status: "active" as const,
  format: "safetensors" as const,
  quantization: "none",
  context_length: 8192,
  base_version_id: null,
  training_job_id: null,
  ...v,
});

const MODEL_SEEDS: ModelSeed[] = [
  {
    name: "phi-4",
    family: "phi",
    description: "Microsoft Phi-4 (14B) - strong reasoning and math for its size.",
    license: "MIT",
    source: "huggingface",
    hf_repo: "microsoft/phi-4",
    org: false,
    created: 160,
    versions: [
      base({ version: 1, param_count: 14_700_000_000, context_length: 16384, benchmark_scores: bench(0.848, 0.935, 0.826, 0.902, 0.629), artifact_uri: "hf://microsoft/phi-4" }),
      base({ version: 2, status: "registered", quantization: "awq", param_count: 14_700_000_000, context_length: 16384, benchmark_scores: bench(0.841, 0.927, 0.817, 0.896, 0.622), artifact_uri: "s3://models/phi-4/v2-awq" }),
    ],
  },
  {
    name: "phi-4-mini",
    family: "phi",
    description: "Microsoft Phi-4 Mini (3.8B) - compact instruct model with 128K context.",
    license: "MIT",
    source: "huggingface",
    hf_repo: "microsoft/Phi-4-mini-instruct",
    org: false,
    created: 150,
    versions: [
      base({ version: 1, param_count: 3_800_000_000, context_length: 131072, benchmark_scores: bench(0.673, 0.886, 0.744, 0.832, 0.581), artifact_uri: "hf://microsoft/Phi-4-mini-instruct" }),
    ],
  },
  {
    name: "llama-3.1-8b",
    family: "llama",
    description: "Meta Llama 3.1 8B Instruct - dependable general-purpose assistant.",
    license: "Llama 3.1",
    source: "huggingface",
    hf_repo: "meta-llama/Llama-3.1-8B-Instruct",
    org: false,
    created: 150,
    versions: [
      base({ version: 1, param_count: 8_000_000_000, context_length: 131072, benchmark_scores: bench(0.694, 0.845, 0.726, 0.831, 0.453), artifact_uri: "hf://meta-llama/Llama-3.1-8B-Instruct" }),
      base({ version: 2, status: "registered", quantization: "fp8", param_count: 8_000_000_000, context_length: 131072, benchmark_scores: bench(0.689, 0.838, 0.72, 0.826, 0.449), artifact_uri: "s3://models/llama-3.1-8b/v2-fp8" }),
    ],
  },
  {
    name: "gemma-3-4b",
    family: "gemma",
    description: "Google Gemma 3 4B - multilingual, long-context instruct model.",
    license: "Gemma",
    source: "huggingface",
    hf_repo: "google/gemma-3-4b-it",
    org: false,
    created: 140,
    versions: [
      base({ version: 1, param_count: 4_300_000_000, context_length: 131072, benchmark_scores: bench(0.596, 0.892, 0.713, 0.714, 0.502), artifact_uri: "hf://google/gemma-3-4b-it" }),
    ],
  },
  {
    name: "qwen2.5-7b",
    family: "qwen",
    description: "Qwen 2.5 7B Instruct - excellent code and structured-output skills.",
    license: "Apache-2.0",
    source: "huggingface",
    hf_repo: "Qwen/Qwen2.5-7B-Instruct",
    org: false,
    created: 140,
    versions: [
      base({ version: 1, param_count: 7_600_000_000, context_length: 32768, benchmark_scores: bench(0.742, 0.915, 0.846, 0.861, 0.564), artifact_uri: "hf://Qwen/Qwen2.5-7B-Instruct" }),
      base({ version: 2, status: "registered", quantization: "gptq", param_count: 7_600_000_000, context_length: 32768, benchmark_scores: bench(0.735, 0.907, 0.839, 0.853, 0.559), artifact_uri: "s3://models/qwen2.5-7b/v2-gptq" }),
    ],
  },
  {
    name: "mistral-7b",
    family: "mistral",
    description: "Mistral 7B Instruct v0.3 - fast, permissively licensed baseline.",
    license: "Apache-2.0",
    source: "huggingface",
    hf_repo: "mistralai/Mistral-7B-Instruct-v0.3",
    org: false,
    created: 130,
    versions: [
      base({ version: 1, param_count: 7_200_000_000, context_length: 32768, benchmark_scores: bench(0.625, 0.521, 0.305, 0.782, 0.426), artifact_uri: "hf://mistralai/Mistral-7B-Instruct-v0.3" }),
    ],
  },
  {
    name: "acme-support-bot",
    family: "phi",
    description: "Phi-4 Mini fine-tuned (QLoRA) on 24K resolved support tickets.",
    license: "MIT",
    source: "finetune",
    hf_repo: null,
    org: true,
    created: 45,
    versions: [
      base({ version: 1, status: "archived", format: "safetensors", param_count: 3_800_000_000, context_length: 16384, benchmark_scores: bench(0.671, 0.884, 0.739, 0.829, 0.601), artifact_uri: "s3://models/acme-support-bot/v1" }),
      base({ version: 2, quantization: "none", param_count: 3_800_000_000, context_length: 16384, benchmark_scores: bench(0.676, 0.889, 0.742, 0.836, 0.688), artifact_uri: "s3://models/acme-support-bot/v2" }),
      base({ version: 3, status: "registered", quantization: "awq", param_count: 3_800_000_000, context_length: 16384, benchmark_scores: bench(0.672, 0.881, 0.735, 0.831, 0.683), artifact_uri: "s3://models/acme-support-bot/v3-awq" }),
    ],
  },
  {
    name: "acme-sql-assistant",
    family: "qwen",
    description: "Qwen 2.5 7B LoRA-tuned for internal text-to-SQL on the warehouse schema.",
    license: "Apache-2.0",
    source: "finetune",
    hf_repo: null,
    org: true,
    created: 21,
    versions: [
      base({ version: 1, param_count: 7_600_000_000, context_length: 32768, benchmark_scores: bench(0.741, 0.914, 0.871, 0.859, 0.561), artifact_uri: "s3://models/acme-sql-assistant/v1" }),
    ],
  },
];

export const MODELS: Model[] = [];
export const VERSIONS: ModelVersion[] = [];
let vSeq = 0;
MODEL_SEEDS.forEach((seed, mi) => {
  const modelId = mkId(KIND.model, mi + 1);
  MODELS.push({
    id: modelId,
    org_id: seed.org ? ORGS[0]!.id : null,
    name: seed.name,
    family: seed.family,
    description: seed.description,
    license: seed.license,
    source: seed.source,
    hf_repo: seed.hf_repo,
    created_at: ago(seed.created * DAY),
    archived_at: null,
  });
  seed.versions.forEach((v) => {
    VERSIONS.push({
      ...v,
      id: mkId(KIND.version, ++vSeq),
      model_id: modelId,
      created_at: ago((seed.created - (v.version - 1) * 9) * DAY),
    });
  });
});
// lineage: fine-tunes derive from phi-4-mini v1 / qwen v1
{
  const phiMini = VERSIONS.find((v) => v.model_id === MODELS[1]!.id)!;
  const qwen = VERSIONS.find((v) => v.model_id === MODELS[4]!.id)!;
  VERSIONS.filter((v) => v.model_id === MODELS[6]!.id).forEach((v) => (v.base_version_id = phiMini.id));
  VERSIONS.filter((v) => v.model_id === MODELS[7]!.id).forEach((v) => (v.base_version_id = qwen.id));
}

export const modelByName = (name: string) => MODELS.find((m) => m.name === name)!;
export const activeVersion = (modelId: string) =>
  VERSIONS.filter((v) => v.model_id === modelId && v.status === "active").sort((a, b) => b.version - a.version)[0] ??
  VERSIONS.filter((v) => v.model_id === modelId).sort((a, b) => b.version - a.version)[0];
export const versionOf = (modelName: string, version = 1) =>
  VERSIONS.find((v) => v.model_id === modelByName(modelName).id && v.version === version)!;

/* ------------------------------------------------------------- deployments --- */
const dep = (
  n: number,
  name: string,
  model: string,
  version: number,
  over: Partial<Deployment> & { p95: number; alias: string },
): Deployment => {
  const { p95, alias, ...rest } = over;
  return {
    id: mkId(KIND.deployment, n),
    org_id: ORGS[0]!.id,
    project_id: null,
    name,
    model_version_id: versionOf(model, version).id,
    kind: "private",
    target: "dgx_spark",
    strategy: "direct",
    status: "running",
    replicas_min: 1,
    replicas_max: 2,
    gpu_count: 1,
    vllm_args: { max_model_len: 16384, gpu_memory_utilization: 0.9, enable_prefix_caching: true },
    internal_url: `http://vllm-${name}.slm.svc:8000`,
    created_at: ago((30 - n * 3) * DAY),
    updated_at: ago(n * HOUR),
    stopped_at: null,
    model_name: model,
    model_version: version,
    endpoint_alias: alias,
    p95_latency_ms: p95,
    replicas_current: rest.replicas_min ?? 1,
    ...rest,
  };
};

export const DEPLOYMENTS: Deployment[] = [
  dep(1, "phi4-mini-prod", "phi-4-mini", 1, { alias: "phi-4-mini", p95: 214, replicas_min: 2, replicas_max: 4, replicas_current: 3, strategy: "canary", project_id: PROJECTS[0]!.id }),
  dep(2, "support-bot-v2", "acme-support-bot", 2, { alias: "support-bot", p95: 296, target: "kubernetes", strategy: "blue_green", replicas_min: 1, replicas_max: 3, replicas_current: 2, project_id: PROJECTS[0]!.id }),
  dep(3, "llama31-8b-shared", "llama-3.1-8b", 1, { alias: "llama-3.1-8b", p95: 372, kind: "shared", target: "azure", gpu_count: 2, replicas_min: 2, replicas_max: 6, replicas_current: 4 }),
  dep(4, "sql-assistant", "acme-sql-assistant", 1, { alias: "sql-assistant", p95: 341, target: "kubernetes", gpu_count: 1, replicas_min: 1, replicas_max: 2, replicas_current: 1, project_id: PROJECTS[2]!.id }),
  dep(5, "qwen25-7b-dev", "qwen2.5-7b", 1, { alias: "qwen2.5-7b-dev", p95: 0, status: "stopped", stopped_at: ago(2 * DAY), replicas_current: 0, project_id: PROJECTS[3]!.id }),
  dep(6, "phi4-staging", "phi-4", 1, { alias: "phi-4-staging", p95: 0, status: "provisioning", gpu_count: 2, replicas_current: 0, project_id: PROJECTS[1]!.id }),
  dep(7, "mistral-7b-poc", "mistral-7b", 1, { alias: "mistral-7b-poc", p95: 0, status: "failed", replicas_current: 0, project_id: PROJECTS[3]!.id }),
];
/** timestamp when the "provisioning" seed deployment becomes running (simulated) */
export const PROVISIONING_DONE: Record<string, number> = { [DEPLOYMENTS[5]!.id]: T0 + 25_000 };

/* ---------------------------------------------------------------- datasets --- */
const ds = (n: number, name: string, format: Dataset["format"], rows: number, tokens: number, mb: number, purpose: Dataset["purpose"] = "finetune", days = 30): Dataset => ({
  id: mkId(KIND.dataset, n),
  name,
  purpose,
  format,
  storage_uri: `s3://datasets/${name}`,
  row_count: rows,
  token_count: tokens,
  size_bytes: mb * 1024 * 1024,
  schema_info: { columns: format === "csv" ? ["question", "answer"] : ["messages"] },
  created_at: ago(days * DAY),
});
export const DATASETS: Dataset[] = [
  ds(1, "support-tickets-2024.jsonl", "jsonl", 24_310, 18_400_000, 96, "finetune", 52),
  ds(2, "sql-pairs-warehouse.jsonl", "jsonl", 8_940, 4_100_000, 21, "finetune", 24),
  ds(3, "contracts-qa.csv", "csv", 3_220, 2_650_000, 14, "finetune", 12),
  ds(4, "golden-eval-set.jsonl", "jsonl", 1_200, 640_000, 3, "eval", 40),
];

/* ------------------------------------------------------------ training jobs --- */
export interface JobSim {
  startedMs: number | null;
  stepSec: number;
  totalSteps: number;
  totalTokens: number;
  gpuHourUsd: number;
  finalized: boolean;
  queuedUntil?: number;
  failAtStep?: number;
}
export const JOB_SIM: Record<string, JobSim> = {};
export const JOBS: TrainingJob[] = [];

function seedJob(n: number, p: Partial<TrainingJob> & { name: string; base: [string, number]; dataset: number; status: TrainingJob["status"]; startedAgo?: number; sim: Partial<JobSim> }) {
  const baseV = versionOf(p.base[0], p.base[1]);
  const dset = DATASETS[p.dataset - 1]!;
  const id = mkId(KIND.job, n);
  const sim: JobSim = { startedMs: p.startedAgo != null ? T0 - p.startedAgo : null, stepSec: 1.6, totalSteps: 900, totalTokens: dset.token_count ?? 0, gpuHourUsd: 3.2, finalized: p.status === "succeeded", ...p.sim };
  JOB_SIM[id] = sim;
  JOBS.push({
    id,
    name: p.name,
    base_version_id: baseV.id,
    dataset_id: dset.id,
    method: "qlora",
    hyperparams: { learning_rate: 0.0002, epochs: 3, batch_size: 16, lora_r: 16, lora_alpha: 32, max_seq_len: 4096 },
    gpu_count: 1,
    status: p.status,
    output_version_id: null,
    tokens_processed: 0,
    est_cost_usd: 18.4,
    error: p.error ?? null,
    created_at: ago((p.startedAgo ?? HOUR) + 3 * MIN),
    started_at: p.startedAgo != null ? ago(p.startedAgo) : null,
    finished_at: p.finished_at ?? null,
    base_model_name: p.base[0],
    dataset_name: dset.name,
    ...(p.hyperparams ? { hyperparams: p.hyperparams } : {}),
    ...(p.method ? { method: p.method } : {}),
    ...(p.gpu_count ? { gpu_count: p.gpu_count } : {}),
  });
}
seedJob(1, { name: "support-bot-qlora-r2", base: ["phi-4-mini", 1], dataset: 1, status: "succeeded", startedAgo: 9 * DAY, finished_at: ago(9 * DAY - 25 * MIN), sim: { totalSteps: 900, stepSec: 1.6 } });
JOBS[0]!.output_version_id = VERSIONS.find((v) => v.model_id === MODELS[6]!.id && v.version === 2)!.id;
seedJob(2, { name: "sql-assistant-lora-r1", base: ["qwen2.5-7b", 1], dataset: 2, status: "running", startedAgo: 11 * MIN, method: "lora", sim: { totalSteps: 1000, stepSec: 1.4 } });
seedJob(3, { name: "contracts-qa-qlora", base: ["llama-3.1-8b", 1], dataset: 3, status: "failed", startedAgo: 3 * DAY, finished_at: ago(3 * DAY - 6 * MIN), error: "CUDA out of memory at step 212 (batch_size=32, max_seq_len=8192). Reduce batch size or sequence length.", sim: { totalSteps: 600, failAtStep: 212, stepSec: 1.7 } });

/* --------------------------------------------------------------------- RAG --- */
export const RAG_INDEXES: RagIndex[] = [
  { id: mkId(KIND.ragIndex, 1), project_id: PROJECTS[0]!.id, name: "product-docs", backend: "qdrant", collection: "product_docs_v3", embedding_model: "bge-m3", embedding_dim: 1024, chunk_size: 512, chunk_overlap: 64, hybrid_search: true, reranker_model: "bge-reranker-v2-m3", status: "ready", doc_count: 6, chunk_count: 2_418, created_at: ago(34 * DAY) },
  { id: mkId(KIND.ragIndex, 2), project_id: PROJECTS[1]!.id, name: "hr-policies", backend: "weaviate", collection: "hr_policies", embedding_model: "bge-small-en-v1.5", embedding_dim: 384, chunk_size: 384, chunk_overlap: 48, hybrid_search: false, reranker_model: null, status: "ready", doc_count: 3, chunk_count: 611, created_at: ago(18 * DAY) },
  { id: mkId(KIND.ragIndex, 3), project_id: PROJECTS[1]!.id, name: "legal-contracts", backend: "milvus", collection: "legal_contracts", embedding_model: "nomic-embed-text-v1.5", embedding_dim: 768, chunk_size: 1024, chunk_overlap: 128, hybrid_search: true, reranker_model: "bge-reranker-v2-m3", status: "creating", doc_count: 0, chunk_count: 0, created_at: ago(2000) },
];
export const RAG_DOCS: RagDocument[] = [];
{
  const files: [number, string, string, number, number][] = [
    [1, "getting-started.md", "text/markdown", 142, 40],
    [1, "api-reference.pdf", "application/pdf", 1_030, 36],
    [1, "troubleshooting-guide.pdf", "application/pdf", 512, 22],
    [1, "release-notes-2024.md", "text/markdown", 288, 14],
    [1, "security-whitepaper.pdf", "application/pdf", 301, 9],
    [1, "faq.html", "text/html", 145, 6],
    [2, "employee-handbook.pdf", "application/pdf", 340, 18],
    [2, "leave-policy.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 120, 12],
    [2, "expenses.md", "text/markdown", 151, 8],
  ];
  files.forEach(([idx, filename, mime, chunks, days], i) =>
    RAG_DOCS.push({ id: mkId(KIND.ragDoc, i + 1), index_id: mkId(KIND.ragIndex, idx), filename, mime_type: mime, status: "succeeded", chunk_count: chunks, error: null, created_at: ago(days * DAY) }),
  );
}

export const RAG_PASSAGES: { document: string; text: string }[] = [
  { document: "getting-started.md", text: "To create your first deployment, open Models, choose a base model and click Deploy. Endpoints become available behind the gateway as OpenAI-compatible routes within a minute." },
  { document: "api-reference.pdf", text: "POST /v1/chat/completions accepts the standard OpenAI payload. Set stream=true to receive server-sent events. Rate limits are returned in the X-RateLimit-* response headers." },
  { document: "troubleshooting-guide.pdf", text: "A 429 response means the organisation's requests-per-minute or tokens-per-minute quota was exceeded. Retry with exponential backoff or request a quota increase from an org admin." },
  { document: "security-whitepaper.pdf", text: "All data is encrypted in transit with TLS 1.3 and at rest with AES-256. API keys are stored only as SHA-256 hashes and are shown to the user exactly once." },
  { document: "release-notes-2024.md", text: "Version 2.4 introduces speculative decoding, prefix caching and LoRA adapter hot-swap on shared base models, reducing p95 latency by up to 38 percent." },
  { document: "faq.html", text: "Fine-tuning supports LoRA and QLoRA. A typical 20K-example dataset trains in under 30 minutes on a single GPU for a 4B parameter model." },
  { document: "employee-handbook.pdf", text: "Employees accrue 25 days of paid leave per year. Requests should be submitted at least two weeks in advance through the HR portal." },
  { document: "expenses.md", text: "Expenses above 250 USD require manager approval. Submit receipts within 30 days of the purchase date." },
];

/* -------------------------------------------------------------- evaluation --- */
export const EVALUATIONS: Evaluation[] = [];
export const EVAL_RESULTS: EvaluationResult[] = [];
export interface EvalSim {
  readyAt: number;
}
export const EVAL_SIM: Record<string, EvalSim> = {};

export const EVAL_METRICS = ["accuracy", "f1", "rouge_l", "faithfulness", "groundedness", "json_validity", "hallucination", "toxicity"] as const;

/** Synthesise result rows for a model version from its benchmark profile. */
export function synthResult(evaluationId: string, v: ModelVersion, seedNoise = 0): EvaluationResult {
  const model = MODELS.find((m) => m.id === v.model_id)!;
  const b = v.benchmark_scores;
  const avg = ((b.mmlu ?? 0.6) + (b.gsm8k ?? 0.6) + (b.arc_challenge ?? 0.6)) / 3;
  const tuned = model.source === "finetune" ? 0.06 : 0;
  const params = (v.param_count ?? 7e9) / 1e9;
  const wob = (k: number) => Math.sin(v.version * 7 + params + k + seedNoise) * 0.012;
  const clamp = (n: number) => +Math.min(0.995, Math.max(0.02, n)).toFixed(3);
  return {
    evaluation_id: evaluationId,
    model_version_id: v.id,
    scores: {
      accuracy: clamp(avg + tuned + wob(1)),
      f1: clamp(avg - 0.03 + tuned + wob(2)),
      rouge_l: clamp(avg * 0.62 + tuned * 0.8 + wob(3)),
      faithfulness: clamp((b.truthfulqa ?? 0.5) + 0.22 + tuned * 0.5 + wob(4)),
      groundedness: clamp((b.truthfulqa ?? 0.5) + 0.18 + tuned * 0.5 + wob(5)),
      json_validity: clamp(0.78 + (b.humaneval ?? 0.5) * 0.2 + tuned * 0.4 + wob(6)),
      hallucination: clamp(0.42 - (b.truthfulqa ?? 0.5) * 0.45 - tuned * 0.3 + wob(7)),
      toxicity: clamp(0.03 + (1 - avg) * 0.04 + wob(8)),
    },
    latency_p50_ms: Math.round(48 + params * 14 + wob(9) * 100),
    latency_p95_ms: Math.round(120 + params * 34 + wob(10) * 200),
    cost_per_1k_tokens_usd: +(0.00012 * params + 0.00008).toFixed(6),
    sample_count: 1200,
    model_name: model.name,
    model_version: v.version,
  };
}

function seedEval(n: number, name: string, dataset: number, versions: ModelVersion[], status: Evaluation["status"], days: number) {
  const id = mkId(KIND.evaluation, n);
  EVALUATIONS.push({
    id,
    name,
    dataset_id: DATASETS[dataset - 1]!.id,
    model_version_ids: versions.map((v) => v.id),
    metrics: [...EVAL_METRICS],
    judge_model: "phi-4",
    status,
    created_at: ago(days * DAY),
    finished_at: status === "succeeded" ? ago(days * DAY - 12 * MIN) : null,
    dataset_name: DATASETS[dataset - 1]!.name,
  });
  if (status === "succeeded") versions.forEach((v) => EVAL_RESULTS.push(synthResult(id, v)));
}
seedEval(1, "Support QA: base vs fine-tuned", 4, [versionOf("phi-4-mini", 1), versionOf("acme-support-bot", 2)], "succeeded", 8);
seedEval(2, "Base model bake-off", 4, [versionOf("phi-4", 1), versionOf("phi-4-mini", 1), versionOf("llama-3.1-8b", 1), versionOf("gemma-3-4b", 1), versionOf("qwen2.5-7b", 1), versionOf("mistral-7b", 1)], "succeeded", 20);
seedEval(3, "SQL generation regression", 4, [versionOf("qwen2.5-7b", 1), versionOf("acme-sql-assistant", 1)], "succeeded", 3);
seedEval(4, "Support bot v3 AWQ check", 4, [versionOf("acme-support-bot", 2), versionOf("acme-support-bot", 3)], "running", 0);
EVAL_SIM[EVALUATIONS[3]!.id] = { readyAt: T0 + 20_000 };

/* ---------------------------------------------------------------- API keys --- */
export const API_KEYS: ApiKey[] = [
  { id: mkId(KIND.apiKey, 1), org_id: ORGS[0]!.id, project_id: null, scope: "organization", name: "prod-backend", prefix: "slm_k9Xa2", rate_limit_rpm: 1200, expires_at: null, rotated_from: null, grace_until: null, last_used_at: ago(2 * MIN), revoked_at: null, created_at: ago(90 * DAY) },
  { id: mkId(KIND.apiKey, 2), org_id: ORGS[0]!.id, project_id: PROJECTS[0]!.id, scope: "project", name: "support-bot-service", prefix: "slm_Qm4ZT", rate_limit_rpm: 600, expires_at: ago(-120 * DAY), rotated_from: null, grace_until: null, last_used_at: ago(11 * 1000), revoked_at: null, created_at: ago(61 * DAY) },
  { id: mkId(KIND.apiKey, 3), org_id: ORGS[0]!.id, project_id: PROJECTS[2]!.id, scope: "project", name: "sql-copilot-ide", prefix: "slm_b7LpR", rate_limit_rpm: null, expires_at: null, rotated_from: null, grace_until: null, last_used_at: ago(37 * MIN), revoked_at: null, created_at: ago(33 * DAY) },
  { id: mkId(KIND.apiKey, 4), org_id: ORGS[0]!.id, project_id: PROJECTS[3]!.id, scope: "project", name: "ci-evaluations", prefix: "slm_Tn83w", rate_limit_rpm: 120, expires_at: ago(-20 * DAY), rotated_from: null, grace_until: ago(-2 * DAY), last_used_at: ago(3 * HOUR), revoked_at: null, created_at: ago(14 * DAY) },
  { id: mkId(KIND.apiKey, 5), org_id: ORGS[0]!.id, project_id: PROJECTS[1]!.id, scope: "project", name: "legacy-ocr-pipeline", prefix: "slm_Zc19d", rate_limit_rpm: 300, expires_at: null, rotated_from: null, grace_until: null, last_used_at: ago(41 * DAY), revoked_at: ago(40 * DAY), created_at: ago(150 * DAY) },
];

/* ------------------------------------------------------------------ alerts --- */
export const ALERTS = [
  { id: "al-1", severity: "critical" as const, title: "Deployment failed to start", detail: "mistral-7b-poc: vLLM exited with code 1 (insufficient GPU memory for max_model_len=32768).", resource: "mistral-7b-poc", ago: 38 * MIN },
  { id: "al-2", severity: "warning" as const, title: "p95 latency above SLO", detail: "support-bot-v2 p95 296 ms is within 8% of the 320 ms objective for 15 minutes.", resource: "support-bot-v2", ago: 12 * MIN },
  { id: "al-3", severity: "warning" as const, title: "GPU memory pressure", detail: "gpu-1 (k8s-gpu-a) is above 90% memory utilisation. Consider lowering gpu_memory_utilization or scaling out.", resource: "gpu-1", ago: 6 * MIN },
  { id: "al-4", severity: "info" as const, title: "Monthly budget at 78%", detail: "Spend is on track to reach 96% of the 12,000 USD monthly budget.", resource: "budget", ago: 5 * HOUR },
  { id: "al-5", severity: "info" as const, title: "Training job finished", detail: "support-bot-qlora-r2 completed; version 2 of acme-support-bot is available.", resource: "support-bot-qlora-r2", ago: 9 * DAY },
];
