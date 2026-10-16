/**
 * Wire types for the SLM Studio control plane (CONTRACT.md §5) and the OpenAI-compatible
 * inference API (§4). Field names mirror db/migrations/0001_init.sql.
 *
 * Fields marked "UI convenience" are denormalised values the UI would like the API to embed;
 * they are always optional so the UI keeps working against a leaner backend.
 */

export type OrgRole = "viewer" | "developer" | "ml_engineer" | "org_admin";
export type Role = OrgRole | "platform_admin";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

/* ------------------------------------------------------------------ auth --- */
export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

export interface Membership {
  org_id: string;
  org_name: string;
  org_slug: string;
  role: OrgRole;
}

export interface Me {
  user: { id: string; email: string; display_name: string; is_platform_admin: boolean };
  memberships: Membership[];
  active_org_id: string;
}

export interface Project {
  id: string;
  org_id: string;
  slug: string;
  name: string;
  description: string | null;
  created_at: string;
}

export type ApiKeyScope = "organization" | "project";

export interface ApiKey {
  id: string;
  org_id: string;
  project_id: string | null;
  scope: ApiKeyScope;
  name: string;
  prefix: string;
  rate_limit_rpm: number | null;
  expires_at: string | null;
  rotated_from: string | null;
  grace_until: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

/** Returned once on create / rotate. `key` is the plaintext secret. */
export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export interface ApiKeyCreateInput {
  name: string;
  scope: ApiKeyScope;
  project_id?: string | null;
  rate_limit_rpm?: number | null;
  expires_at?: string | null;
}

export interface UsagePoint {
  t: string;
  requests: number;
  tokens: number;
}

export interface ApiKeyUsage {
  api_key_id: string;
  total_requests: number;
  total_tokens: number;
  points: UsagePoint[];
}

/* -------------------------------------------------------------- registry --- */
export type ModelSource = "huggingface" | "upload" | "finetune";
export type VersionStatus = "registered" | "active" | "archived";

export interface BenchmarkScores {
  [metric: string]: number;
}

export interface ModelVersion {
  id: string;
  model_id: string;
  version: number;
  status: VersionStatus;
  format: "hf" | "safetensors" | "gguf";
  param_count: number | null;
  quantization: string | null;
  context_length: number | null;
  artifact_uri: string;
  base_version_id: string | null;
  training_job_id: string | null;
  benchmark_scores: BenchmarkScores;
  created_at: string;
}

export interface Model {
  id: string;
  org_id: string | null;
  name: string;
  family: string | null;
  description: string | null;
  license: string | null;
  source: ModelSource;
  hf_repo: string | null;
  created_at: string;
  archived_at: string | null;
  /** UI convenience: embedded latest active version. */
  latest_version?: ModelVersion | null;
  /** UI convenience */
  version_count?: number;
}

export interface ModelCreateInput {
  name: string;
  family?: string | null;
  description?: string | null;
  license?: string | null;
  source: ModelSource;
  hf_repo?: string | null;
}

/* ----------------------------------------------------------- deployments --- */
export type DeployStatus = "pending" | "provisioning" | "running" | "stopped" | "failed" | "rolling_back";
export type DeployTarget = "dgx_spark" | "kubernetes" | "azure" | "aws" | "on_prem";
export type DeployStrategy = "direct" | "blue_green" | "canary";
export type EndpointKind = "private" | "shared" | "dedicated";

export interface Deployment {
  id: string;
  org_id: string;
  project_id: string | null;
  name: string;
  model_version_id: string;
  kind: EndpointKind;
  target: DeployTarget;
  strategy: DeployStrategy;
  status: DeployStatus;
  replicas_min: number;
  replicas_max: number;
  gpu_count: number;
  vllm_args: Record<string, unknown>;
  internal_url: string | null;
  created_at: string;
  updated_at: string;
  stopped_at: string | null;
  /** UI convenience (joined server-side or by the BFF) */
  model_name?: string;
  model_version?: number;
  endpoint_alias?: string;
  endpoint_url?: string;
  p95_latency_ms?: number | null;
  replicas_current?: number;
}

export interface DeploymentCreateInput {
  name: string;
  model_version_id: string;
  kind?: EndpointKind;
  target?: DeployTarget;
  strategy?: DeployStrategy;
  replicas_min?: number;
  replicas_max?: number;
  gpu_count?: number;
  vllm_args?: Record<string, unknown>;
}

export interface ScaleInput {
  replicas_min: number;
  replicas_max: number;
  gpu_count?: number;
}

/* ----------------------------------------------------------- fine-tuning --- */
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type TuneMethod = "lora" | "qlora" | "full";

export interface Dataset {
  id: string;
  name: string;
  purpose: "finetune" | "eval";
  format: "jsonl" | "csv" | "parquet";
  storage_uri: string;
  row_count: number | null;
  token_count: number | null;
  size_bytes: number | null;
  schema_info: Record<string, unknown>;
  created_at: string;
}

export interface Hyperparams {
  learning_rate: number;
  epochs: number;
  batch_size: number;
  lora_r: number;
  lora_alpha: number;
  max_seq_len: number;
}

export interface TrainingJob {
  id: string;
  name: string;
  base_version_id: string;
  dataset_id: string;
  method: TuneMethod;
  hyperparams: Hyperparams;
  gpu_count: number;
  status: JobStatus;
  output_version_id: string | null;
  tokens_processed: number;
  est_cost_usd: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** UI convenience */
  base_model_name?: string;
  dataset_name?: string;
  total_steps?: number;
  current_step?: number;
  total_tokens?: number;
  cost_so_far_usd?: number;
}

export interface TrainingJobCreateInput {
  name: string;
  base_version_id: string;
  dataset_id: string;
  method: TuneMethod;
  hyperparams: Hyperparams;
  gpu_count: number;
}

export interface TrainingMetric {
  step: number;
  loss: number | null;
  eval_loss: number | null;
  lr: number | null;
  tokens: number | null;
  gpu_util: number | null;
  recorded_at: string;
}

/* ------------------------------------------------------------------- RAG --- */
export type VectorBackend = "qdrant" | "weaviate" | "milvus";
export type IndexStatus = "creating" | "ready" | "failed";

export interface RagIndex {
  id: string;
  project_id: string | null;
  name: string;
  backend: VectorBackend;
  collection: string;
  embedding_model: string;
  embedding_dim: number;
  chunk_size: number;
  chunk_overlap: number;
  hybrid_search: boolean;
  reranker_model: string | null;
  status: IndexStatus;
  doc_count: number;
  chunk_count: number;
  created_at: string;
}

export interface RagIndexCreateInput {
  name: string;
  backend: VectorBackend;
  embedding_model: string;
  embedding_dim: number;
  chunk_size: number;
  chunk_overlap: number;
  hybrid_search: boolean;
  reranker_model: string | null;
}

export interface RagDocument {
  id: string;
  index_id: string;
  filename: string;
  mime_type: string;
  status: JobStatus;
  chunk_count: number;
  error: string | null;
  created_at: string;
}

export interface RagQueryInput {
  query: string;
  top_k?: number;
  hybrid?: boolean;
  score_threshold?: number;
}

export interface RagMatch {
  chunk_id: string;
  document: string;
  score: number;
  text: string;
}

export interface RagQueryResult {
  matches: RagMatch[];
  latency_ms: number;
}

/* ------------------------------------------------------------ evaluation --- */
export interface Evaluation {
  id: string;
  name: string;
  dataset_id: string;
  model_version_ids: string[];
  metrics: string[];
  judge_model: string | null;
  status: JobStatus;
  created_at: string;
  finished_at: string | null;
  /** UI convenience */
  dataset_name?: string;
}

export interface EvaluationCreateInput {
  name: string;
  dataset_id: string;
  model_version_ids: string[];
  metrics: string[];
  judge_model?: string | null;
}

export interface EvaluationResult {
  evaluation_id: string;
  model_version_id: string;
  scores: Record<string, number>;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  cost_per_1k_tokens_usd: number | null;
  sample_count: number | null;
  /** UI convenience */
  model_name?: string;
  model_version?: number;
}

/* ------------------------------------------------------------ monitoring --- */
export interface MetricPoint {
  t: number; // epoch ms
  requests_per_min: number;
  tokens_per_min: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
  gpu_util: number; // %
  gpu_memory_util: number; // %
  throughput_tps: number;
  error_rate: number; // %
}

export interface GpuStat {
  id: string;
  name: string;
  node: string;
  util: number;
  memory_used_gb: number;
  memory_total_gb: number;
  temperature_c: number;
}

export interface DeploymentMetric {
  deployment_id: string;
  name: string;
  status: DeployStatus;
  requests_per_min: number;
  p95_latency_ms: number;
  error_rate: number;
  gpu_util: number;
}

export interface ModelUsage {
  model: string;
  requests: number;
  tokens: number;
}

export type AlertSeverity = "info" | "warning" | "critical";

export interface AlertItem {
  id: string;
  severity: AlertSeverity;
  title: string;
  detail: string;
  resource: string;
  fired_at: string;
}

export interface MonitoringOverview {
  window: string;
  points: MetricPoint[];
  gpus: GpuStat[];
  deployments: DeploymentMetric[];
  usage_by_model: ModelUsage[];
  alerts: AlertItem[];
}

/* ------------------------------------------------------------------ cost --- */
export type CostGroupBy = "project" | "deployment" | "model" | "org";

export interface CostItem {
  key: string;
  label: string;
  usd: number;
  quantity?: number;
  unit?: string;
}

export interface CostSummary {
  group_by: CostGroupBy;
  from: string;
  to: string;
  total_usd: number;
  previous_total_usd: number;
  items: CostItem[];
  daily: { day: string; usd: number }[];
  by_resource: { resource: string; usd: number }[];
}

/* ---------------------------------------------------- OpenAI-compatible --- */
export interface OpenAIModel {
  id: string;
  object: "model";
  created: number;
  owned_by: string;
}

export interface ToolDefinition {
  type: "function";
  function: { name: string; description?: string; parameters?: Record<string, unknown> };
}

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
}

export interface ChatCompletionRequest {
  model: string;
  messages: ChatMessage[];
  temperature?: number;
  max_tokens?: number;
  stream?: boolean;
  stream_options?: { include_usage: boolean };
  response_format?: { type: "json_object" | "text" };
  tools?: ToolDefinition[];
}

export interface ChatCompletionChunk {
  id: string;
  object: "chat.completion.chunk";
  created: number;
  model: string;
  choices: {
    index: number;
    delta: {
      role?: "assistant";
      content?: string | null;
      tool_calls?: {
        index: number;
        id?: string;
        type?: "function";
        function?: { name?: string; arguments?: string };
      }[];
    };
    finish_reason: "stop" | "length" | "tool_calls" | null;
  }[];
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number } | null;
}

export interface ApiProblem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  error?: { message: string; type?: string; code?: string };
  report?: {
    format?: string;
    schema?: string | null;
    ok?: boolean;
    total_rows?: number;
    valid_rows?: number;
    error_count?: number;
    errors?: Array<{ row: number; message: string }>;
    warnings?: string[];
  };
  extra?: Record<string, unknown>;
}
