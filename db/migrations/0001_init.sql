-- SLM Studio — initial schema (PostgreSQL 15+)
-- Multi-tenancy: every tenant-owned table carries org_id and is protected by
-- row-level security keyed on the session setting `app.org_id`.
-- Services connect as role `slm_app` (NOT a superuser, NOT table owner) and run
--   SELECT set_config('app.org_id', '<uuid>', true);   -- per transaction
-- The auth service additionally uses `app.bypass_rls = 'on'` for login/lookup paths.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- ---------------------------------------------------------------- enums ----
CREATE TYPE org_role AS ENUM ('org_admin', 'ml_engineer', 'developer', 'viewer');
CREATE TYPE api_key_scope AS ENUM ('organization', 'project');
CREATE TYPE model_source AS ENUM ('huggingface', 'upload', 'finetune');
CREATE TYPE model_format AS ENUM ('hf', 'safetensors', 'gguf');
CREATE TYPE version_status AS ENUM ('registered', 'active', 'archived');
CREATE TYPE deploy_target AS ENUM ('dgx_spark', 'kubernetes', 'azure', 'aws', 'on_prem');
CREATE TYPE endpoint_kind AS ENUM ('private', 'shared', 'dedicated');
CREATE TYPE deploy_status AS ENUM ('pending', 'provisioning', 'running', 'stopped', 'failed', 'rolling_back');
CREATE TYPE deploy_strategy AS ENUM ('direct', 'blue_green', 'canary');
CREATE TYPE job_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');
CREATE TYPE dataset_format AS ENUM ('jsonl', 'csv', 'parquet');
CREATE TYPE tune_method AS ENUM ('lora', 'qlora', 'full');
CREATE TYPE vector_backend AS ENUM ('qdrant', 'weaviate', 'milvus');
CREATE TYPE index_status AS ENUM ('creating', 'ready', 'failed');

-- ------------------------------------------------------------- helpers ----
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION current_org() RETURNS uuid AS $$
  SELECT NULLIF(current_setting('app.org_id', true), '')::uuid
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION rls_bypass() RETURNS boolean AS $$
  SELECT COALESCE(current_setting('app.bypass_rls', true), 'off') = 'on'
$$ LANGUAGE sql STABLE;

-- ------------------------------------------------------ identity & tenancy --
CREATE TABLE organizations (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        citext NOT NULL UNIQUE,
  name        text NOT NULL,
  plan        text NOT NULL DEFAULT 'free',
  data_region text NOT NULL DEFAULT 'eu',            -- GDPR residency
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz
);

CREATE TABLE users (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email             citext NOT NULL UNIQUE,
  display_name      text NOT NULL,
  password_hash     text,                             -- argon2id; NULL for SSO-only users
  is_platform_admin boolean NOT NULL DEFAULT false,   -- the "Admin" role
  is_active         boolean NOT NULL DEFAULT true,
  mfa_secret_enc    bytea,
  last_login_at     timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz                       -- GDPR erasure = anonymise + set
);

CREATE TABLE user_identities (                        -- SSO links
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider    text NOT NULL CHECK (provider IN ('azure_ad', 'google', 'github', 'oidc')),
  subject     text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, subject)
);

CREATE TABLE org_memberships (
  org_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role       org_role NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, user_id)
);
CREATE INDEX ON org_memberships (user_id);

CREATE TABLE refresh_tokens (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  bytea NOT NULL UNIQUE,                  -- sha256
  family_id   uuid NOT NULL,                          -- rotation family; reuse => revoke family
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON refresh_tokens (user_id);

CREATE TABLE projects (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  slug        citext NOT NULL,
  name        text NOT NULL,
  description text,
  created_by  uuid REFERENCES users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz,
  UNIQUE (org_id, slug),
  UNIQUE (org_id, id)                                 -- target for composite FKs
);

CREATE TABLE api_keys (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  project_id    uuid,
  scope         api_key_scope NOT NULL,
  name          text NOT NULL,
  prefix        text NOT NULL,                        -- first 8 chars shown in UI, e.g. "slm_a1b2"
  key_hash      bytea NOT NULL UNIQUE,                -- sha256(secret); plaintext shown once
  created_by    uuid REFERENCES users(id),
  rate_limit_rpm integer,                             -- NULL => org default
  expires_at    timestamptz,
  rotated_from  uuid REFERENCES api_keys(id),         -- rotation chain
  grace_until   timestamptz,                          -- old key still valid until then
  last_used_at  timestamptz,
  revoked_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, project_id) REFERENCES projects (org_id, id) ON DELETE CASCADE,
  CHECK ((scope = 'project') = (project_id IS NOT NULL))
);
CREATE INDEX ON api_keys (org_id);

CREATE TABLE quotas (                                 -- per-tenant usage control
  org_id               uuid PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
  rpm_limit            integer NOT NULL DEFAULT 600,
  tpm_limit            integer NOT NULL DEFAULT 200000,
  monthly_token_limit  bigint,
  monthly_budget_usd   numeric(12,2),
  max_gpu_hours        numeric(10,2),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------ registry ----
CREATE TABLE models (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid REFERENCES organizations(id) ON DELETE CASCADE,  -- NULL = global catalogue
  name          text NOT NULL,                        -- e.g. "phi-4-mini"
  family        text,                                 -- phi | llama | gemma | qwen | mistral
  description   text,
  license       text,
  source        model_source NOT NULL,
  hf_repo       text,
  created_by    uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  archived_at   timestamptz
);
CREATE UNIQUE INDEX models_name_uq ON models (COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid), name);

CREATE TABLE model_versions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id         uuid NOT NULL REFERENCES models(id) ON DELETE CASCADE,
  org_id           uuid REFERENCES organizations(id) ON DELETE CASCADE,
  version          integer NOT NULL,
  status           version_status NOT NULL DEFAULT 'registered',
  format           model_format NOT NULL,
  param_count      bigint,
  quantization     text,                               -- none | awq | gptq | fp8 | q4_k_m ...
  context_length   integer,
  artifact_uri     text NOT NULL,                      -- s3://... or hf://...
  artifact_sha256  text,
  base_version_id  uuid REFERENCES model_versions(id), -- lineage for fine-tunes
  training_job_id  uuid,                               -- FK added after training_jobs
  mlflow_run_id    text,
  benchmark_scores jsonb NOT NULL DEFAULT '{}',        -- {"accuracy":0.81,"rouge_l":0.4,...}
  metadata         jsonb NOT NULL DEFAULT '{}',
  created_by       uuid REFERENCES users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (model_id, version)
);
CREATE INDEX ON model_versions (org_id);

-- --------------------------------------------------------- deployments ----
CREATE TABLE deployments (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  project_id       uuid,
  name             text NOT NULL,
  model_version_id uuid NOT NULL REFERENCES model_versions(id),
  kind             endpoint_kind NOT NULL DEFAULT 'private',
  target           deploy_target NOT NULL DEFAULT 'dgx_spark',
  strategy         deploy_strategy NOT NULL DEFAULT 'direct',
  status           deploy_status NOT NULL DEFAULT 'pending',
  replicas_min     integer NOT NULL DEFAULT 1,
  replicas_max     integer NOT NULL DEFAULT 1,
  gpu_count        integer NOT NULL DEFAULT 1,
  vllm_args        jsonb NOT NULL DEFAULT '{}',        -- max_model_len, speculative_config, enable_prefix_caching...
  internal_url     text,                               -- cluster-internal vLLM base URL
  previous_deployment_id uuid REFERENCES deployments(id), -- rollback pointer
  created_by       uuid REFERENCES users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  stopped_at       timestamptz,
  FOREIGN KEY (org_id, project_id) REFERENCES projects (org_id, id) ON DELETE SET NULL (project_id),
  UNIQUE (org_id, name)
);
CREATE INDEX ON deployments (org_id, status);

CREATE TABLE deployment_events (                      -- deployment history / audit of state changes
  id            bigserial PRIMARY KEY,
  org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  deployment_id uuid NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
  event         text NOT NULL,                         -- created|scaled|rolled_back|failed|...
  detail        jsonb NOT NULL DEFAULT '{}',
  actor_id      uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON deployment_events (deployment_id, created_at DESC);

CREATE TABLE endpoints (                              -- what clients call: model alias -> routes
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  alias       text NOT NULL,                           -- value of "model" in OpenAI requests
  kind        endpoint_kind NOT NULL DEFAULT 'private',
  enabled     boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, alias)
);

CREATE TABLE endpoint_routes (                        -- A/B & canary: weighted targets per alias
  endpoint_id   uuid NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
  deployment_id uuid NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
  org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  weight        integer NOT NULL CHECK (weight BETWEEN 0 AND 100),
  label         text,                                  -- 'stable' | 'canary' | 'variant-b'
  PRIMARY KEY (endpoint_id, deployment_id)
);

-- ------------------------------------------------- datasets & training ----
CREATE TABLE datasets (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  project_id    uuid,
  name          text NOT NULL,
  purpose       text NOT NULL DEFAULT 'finetune' CHECK (purpose IN ('finetune', 'eval')),
  format        dataset_format NOT NULL,
  storage_uri   text NOT NULL,
  row_count     bigint,
  token_count   bigint,
  size_bytes    bigint,
  schema_info   jsonb NOT NULL DEFAULT '{}',
  created_by    uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, project_id) REFERENCES projects (org_id, id) ON DELETE SET NULL (project_id)
);
CREATE INDEX ON datasets (org_id);

CREATE TABLE training_jobs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  project_id       uuid,
  name             text NOT NULL,
  base_version_id  uuid NOT NULL REFERENCES model_versions(id),
  dataset_id       uuid NOT NULL REFERENCES datasets(id),
  method           tune_method NOT NULL DEFAULT 'qlora',
  hyperparams      jsonb NOT NULL,                     -- lr, epochs, batch, lora_r, lora_alpha, max_seq_len...
  gpu_count        integer NOT NULL DEFAULT 1,
  status           job_status NOT NULL DEFAULT 'queued',
  k8s_job_name     text,
  mlflow_run_id    text,
  wandb_run_url    text,
  output_version_id uuid REFERENCES model_versions(id),
  tokens_processed bigint NOT NULL DEFAULT 0,
  est_cost_usd     numeric(12,4),
  error            text,
  created_by       uuid REFERENCES users(id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  started_at       timestamptz,
  finished_at      timestamptz,
  FOREIGN KEY (org_id, project_id) REFERENCES projects (org_id, id) ON DELETE SET NULL (project_id)
);
CREATE INDEX ON training_jobs (org_id, status);
ALTER TABLE model_versions
  ADD CONSTRAINT model_versions_training_job_fk FOREIGN KEY (training_job_id) REFERENCES training_jobs(id);

CREATE TABLE training_metrics (
  job_id     uuid NOT NULL REFERENCES training_jobs(id) ON DELETE CASCADE,
  org_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  step       integer NOT NULL,
  loss       double precision,
  eval_loss  double precision,
  lr         double precision,
  tokens     bigint,
  gpu_util   real,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (job_id, step)
);

CREATE TABLE checkpoints (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id     uuid NOT NULL REFERENCES training_jobs(id) ON DELETE CASCADE,
  org_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  step       integer NOT NULL,
  uri        text NOT NULL,
  size_bytes bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (job_id, step)
);

-- ------------------------------------------------------------------ RAG ----
CREATE TABLE rag_indexes (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  project_id     uuid,
  name           text NOT NULL,
  backend        vector_backend NOT NULL DEFAULT 'qdrant',
  collection     text NOT NULL,
  embedding_model text NOT NULL,
  embedding_dim  integer NOT NULL,
  chunk_size     integer NOT NULL DEFAULT 512,
  chunk_overlap  integer NOT NULL DEFAULT 64,
  hybrid_search  boolean NOT NULL DEFAULT true,
  reranker_model text,
  status         index_status NOT NULL DEFAULT 'creating',
  doc_count      integer NOT NULL DEFAULT 0,
  chunk_count    bigint NOT NULL DEFAULT 0,
  created_by     uuid REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, project_id) REFERENCES projects (org_id, id) ON DELETE SET NULL (project_id),
  UNIQUE (org_id, name)
);

CREATE TABLE rag_documents (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  index_id    uuid NOT NULL REFERENCES rag_indexes(id) ON DELETE CASCADE,
  org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  filename    text NOT NULL,
  mime_type   text NOT NULL,
  storage_uri text NOT NULL,
  status      job_status NOT NULL DEFAULT 'queued',
  chunk_count integer NOT NULL DEFAULT 0,
  metadata    jsonb NOT NULL DEFAULT '{}',
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON rag_documents (index_id);

-- ----------------------------------------------------------- evaluation ----
CREATE TABLE evaluations (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  project_id        uuid,
  name              text NOT NULL,
  dataset_id        uuid NOT NULL REFERENCES datasets(id),
  model_version_ids uuid[] NOT NULL,                   -- >=1; 2+ => comparison (A vs B)
  metrics           text[] NOT NULL,                   -- accuracy,f1,rouge,bleu,hallucination,faithfulness,groundedness,toxicity,json_validity
  judge_model       text,                              -- LLM-as-judge for hallucination/faithfulness
  status            job_status NOT NULL DEFAULT 'queued',
  created_by        uuid REFERENCES users(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  finished_at       timestamptz,
  FOREIGN KEY (org_id, project_id) REFERENCES projects (org_id, id) ON DELETE SET NULL (project_id)
);

CREATE TABLE evaluation_results (
  evaluation_id    uuid NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
  org_id           uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  model_version_id uuid NOT NULL REFERENCES model_versions(id),
  scores           jsonb NOT NULL,                     -- {"accuracy":0.8,...}
  latency_p50_ms   integer,
  latency_p95_ms   integer,
  cost_per_1k_tokens_usd numeric(12,6),
  sample_count     integer,
  PRIMARY KEY (evaluation_id, model_version_id)
);

-- ---------------------------------------------------- usage & auditing ----
-- High volume: range-partitioned by month. Partitions are created by the
-- monitoring service (pg_partman in production). Default partition catches strays.
CREATE TABLE usage_logs (
  id              bigint GENERATED ALWAYS AS IDENTITY,
  org_id          uuid NOT NULL,
  project_id      uuid,
  api_key_id      uuid,
  deployment_id   uuid,
  model_version_id uuid,
  endpoint_alias  text,
  route_label     text,
  request_id      text NOT NULL,
  status_code     smallint NOT NULL,
  prompt_tokens   integer NOT NULL DEFAULT 0,
  completion_tokens integer NOT NULL DEFAULT 0,
  latency_ms      integer,
  ttft_ms         integer,                             -- time to first token (streaming)
  cached_tokens   integer NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE TABLE usage_logs_default PARTITION OF usage_logs DEFAULT;
CREATE INDEX ON usage_logs (org_id, created_at DESC);
CREATE INDEX ON usage_logs (deployment_id, created_at DESC);

CREATE TABLE cost_rates (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  resource      text NOT NULL CHECK (resource IN ('gpu_hour', 'input_token_1k', 'output_token_1k', 'storage_gb_month', 'train_token_1k')),
  gpu_type      text,
  usd           numeric(12,6) NOT NULL,
  effective_from date NOT NULL DEFAULT current_date
);

CREATE TABLE cost_records (                           -- rolled-up daily by the cost service
  day           date NOT NULL,
  org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  project_id    uuid,
  deployment_id uuid,
  model_version_id uuid,
  resource      text NOT NULL,
  quantity      numeric(18,4) NOT NULL,
  usd           numeric(14,6) NOT NULL
);
-- expressions are not allowed in PRIMARY KEY, so uniqueness is a unique index (upsert target)
CREATE UNIQUE INDEX cost_records_uq ON cost_records (
  day, org_id, resource,
  COALESCE(deployment_id, '00000000-0000-0000-0000-000000000000'::uuid),
  COALESCE(project_id,    '00000000-0000-0000-0000-000000000000'::uuid));

CREATE TABLE audit_logs (                             -- append-only (see trigger)
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id      uuid,
  actor_id    uuid,
  actor_type  text NOT NULL DEFAULT 'user' CHECK (actor_type IN ('user', 'api_key', 'system')),
  action      text NOT NULL,                           -- e.g. api_key.create, deployment.scale
  resource    text NOT NULL,
  resource_id text,
  ip          inet,
  request_id  text,
  detail      jsonb NOT NULL DEFAULT '{}',
  prev_hash   bytea,                                   -- hash chain for tamper evidence
  row_hash    bytea,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_logs (org_id, created_at DESC);

CREATE OR REPLACE FUNCTION audit_logs_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END $$ LANGUAGE plpgsql;
CREATE TRIGGER audit_logs_no_update BEFORE UPDATE OR DELETE ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();

-- ----------------------------------------------------------- triggers ----
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['organizations','users','projects','models','deployments','endpoints','quotas']
  LOOP
    EXECUTE format('CREATE TRIGGER %I_updated BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
  END LOOP;
END $$;

-- ------------------------------------------------- row-level security ----
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'projects','api_keys','quotas','model_versions','deployments','deployment_events','endpoints',
    'endpoint_routes','datasets','training_jobs','training_metrics','checkpoints','rag_indexes',
    'rag_documents','evaluations','evaluation_results','usage_logs','cost_records']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (rls_bypass() OR org_id = current_org()) WITH CHECK (rls_bypass() OR org_id = current_org())', t);
  END LOOP;
END $$;

-- models: global catalogue rows (org_id IS NULL) are readable by everyone, writable only by bypass.
ALTER TABLE models ENABLE ROW LEVEL SECURITY;
ALTER TABLE models FORCE ROW LEVEL SECURITY;
CREATE POLICY models_read  ON models FOR SELECT USING (rls_bypass() OR org_id IS NULL OR org_id = current_org());
CREATE POLICY models_write ON models FOR ALL    USING (rls_bypass() OR org_id = current_org())
                                                WITH CHECK (rls_bypass() OR org_id = current_org());
-- model_versions: same global-read rule (replace the generic policy above)
DROP POLICY tenant_isolation ON model_versions;
CREATE POLICY mv_read  ON model_versions FOR SELECT USING (rls_bypass() OR org_id IS NULL OR org_id = current_org());
CREATE POLICY mv_write ON model_versions FOR ALL    USING (rls_bypass() OR org_id = current_org())
                                                    WITH CHECK (rls_bypass() OR org_id = current_org());

-- audit_logs: tenants may read their own; inserts by anyone with a context
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_read   ON audit_logs FOR SELECT USING (rls_bypass() OR org_id = current_org());
CREATE POLICY audit_insert ON audit_logs FOR INSERT WITH CHECK (true);

-- ------------------------------------------------------- seed catalogue ----
SET LOCAL app.bypass_rls = 'on';
INSERT INTO models (org_id, name, family, license, source, hf_repo, description) VALUES
  (NULL, 'phi-4',           'phi',     'MIT',        'huggingface', 'microsoft/phi-4',                'Microsoft Phi-4 (14B)'),
  (NULL, 'phi-4-mini',      'phi',     'MIT',        'huggingface', 'microsoft/Phi-4-mini-instruct',  'Microsoft Phi-4 Mini (3.8B)'),
  (NULL, 'llama-3.1-8b',    'llama',   'Llama 3.1',  'huggingface', 'meta-llama/Llama-3.1-8B-Instruct','Meta Llama 3.1 8B Instruct'),
  (NULL, 'gemma-3-4b',      'gemma',   'Gemma',      'huggingface', 'google/gemma-3-4b-it',           'Google Gemma 3 4B'),
  (NULL, 'qwen2.5-7b',      'qwen',    'Apache-2.0', 'huggingface', 'Qwen/Qwen2.5-7B-Instruct',       'Qwen 2.5 7B Instruct'),
  (NULL, 'mistral-7b',      'mistral', 'Apache-2.0', 'huggingface', 'mistralai/Mistral-7B-Instruct-v0.3','Mistral 7B Instruct v0.3');

COMMIT;
