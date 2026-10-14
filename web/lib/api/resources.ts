import type { ApiClient } from "./client";
import type {
  ApiKey,
  ApiKeyCreateInput,
  ApiKeyCreated,
  ApiKeyUsage,
  CostGroupBy,
  CostSummary,
  Dataset,
  Deployment,
  DeploymentCreateInput,
  Evaluation,
  EvaluationCreateInput,
  EvaluationResult,
  Me,
  Model,
  ModelCreateInput,
  ModelVersion,
  MonitoringOverview,
  OpenAIModel,
  Page,
  Project,
  RagDocument,
  RagIndex,
  RagIndexCreateInput,
  RagQueryInput,
  RagQueryResult,
  ScaleInput,
  TokenPair,
  TrainingJob,
  TrainingJobCreateInput,
  TrainingMetric,
} from "./types";

/** Follow `next_cursor` (CONTRACT.md §2 pagination) and return every item, capped for safety. */
async function listAll<T>(client: ApiClient, path: string, query: Record<string, string | number | undefined> = {}) {
  const out: T[] = [];
  let cursor: string | null | undefined;
  for (let i = 0; i < 20; i++) {
    const page: Page<T> = await client.get<Page<T>>(path, { query: { limit: 100, ...query, cursor } });
    out.push(...page.items);
    cursor = page.next_cursor;
    if (!cursor) break;
  }
  return out;
}

/** Typed wrappers over the exact paths in CONTRACT.md §4/§5. */
export function createResources(client: ApiClient) {
  const P = "/api/v1";
  return {
    auth: {
      login: (email: string, password: string) =>
        client.post<TokenPair>(`${P}/auth/login`, { email, password }, { auth: false }),
      register: (input: { email: string; password: string; display_name: string; org_name?: string }) =>
        client.post<TokenPair>(`${P}/auth/register`, input, { auth: false }),
      logout: () => client.post<void>(`${P}/auth/logout`),
      me: () => client.get<Me>(`${P}/me`),
      switchOrg: (org_id: string) => client.post<TokenPair>(`${P}/orgs/switch`, { org_id }),
      oauthStartUrl: (provider: "azure_ad" | "google" | "github") => `${P}/auth/oauth/${provider}/start`,
    },
    projects: {
      list: () => listAll<Project>(client, `${P}/projects`),
    },
    apiKeys: {
      list: () => listAll<ApiKey>(client, `${P}/api-keys`),
      create: (input: ApiKeyCreateInput) => client.post<ApiKeyCreated>(`${P}/api-keys`, input),
      rotate: (id: string) => client.post<ApiKeyCreated>(`${P}/api-keys/${id}/rotate`),
      revoke: (id: string) => client.delete<void>(`${P}/api-keys/${id}`),
      usage: (id: string) => client.get<ApiKeyUsage>(`${P}/api-keys/${id}/usage`),
    },
    models: {
      list: () => listAll<Model>(client, `${P}/models`),
      create: (input: ModelCreateInput) => client.post<Model>(`${P}/models`, input),
      archive: (id: string) => client.post<Model>(`${P}/models/${id}/archive`),
      versions: (id: string) => listAll<ModelVersion>(client, `${P}/models/${id}/versions`),
      rollback: (id: string, version: number) =>
        client.post<ModelVersion>(`${P}/models/${id}/versions/${version}/rollback`),
    },
    deployments: {
      list: () => listAll<Deployment>(client, `${P}/deployments`),
      create: (input: DeploymentCreateInput) => client.post<Deployment>(`${P}/deployments`, input),
      start: (id: string) => client.post<Deployment>(`${P}/deployments/${id}/start`),
      stop: (id: string) => client.post<Deployment>(`${P}/deployments/${id}/stop`),
      scale: (id: string, input: ScaleInput) => client.post<Deployment>(`${P}/deployments/${id}/scale`, input),
      rollback: (id: string) => client.post<Deployment>(`${P}/deployments/${id}/rollback`),
      remove: (id: string) => client.delete<void>(`${P}/deployments/${id}`),
    },
    datasets: {
      list: () => listAll<Dataset>(client, `${P}/datasets`),
      upload: (file: File, meta: { name?: string; purpose?: "finetune" | "eval" } = {}) => {
        const form = new FormData();
        form.append("file", file);
        form.append("name", meta.name ?? file.name);
        form.append("purpose", meta.purpose ?? "finetune");
        return client.request<Dataset>("POST", `${P}/datasets`, { form });
      },
    },
    trainingJobs: {
      list: () => listAll<TrainingJob>(client, `${P}/training-jobs`),
      create: (input: TrainingJobCreateInput) => client.post<TrainingJob>(`${P}/training-jobs`, input),
      metrics: (id: string) => client.get<{ items: TrainingMetric[] } | TrainingMetric[]>(`${P}/training-jobs/${id}/metrics`),
      cancel: (id: string) => client.post<TrainingJob>(`${P}/training-jobs/${id}/cancel`),
    },
    rag: {
      indexes: () => listAll<RagIndex>(client, `${P}/rag/indexes`),
      createIndex: (input: RagIndexCreateInput) => client.post<RagIndex>(`${P}/rag/indexes`, input),
      /** not in CONTRACT.md §5 – UI degrades to an empty list when the backend lacks it */
      documents: (indexId: string) => listAll<RagDocument>(client, `${P}/rag/indexes/${indexId}/documents`),
      uploadDocument: (indexId: string, file: File) => {
        const form = new FormData();
        form.append("file", file);
        return client.request<RagDocument>("POST", `${P}/rag/indexes/${indexId}/documents`, { form });
      },
      query: (indexId: string, input: RagQueryInput) =>
        client.post<RagQueryResult>(`${P}/rag/indexes/${indexId}/query`, input),
    },
    evaluations: {
      list: () => listAll<Evaluation>(client, `${P}/evaluations`),
      create: (input: EvaluationCreateInput) => client.post<Evaluation>(`${P}/evaluations`, input),
      results: (id: string) => client.get<{ items: EvaluationResult[] } | EvaluationResult[]>(`${P}/evaluations/${id}/results`),
      compare: (ids: string[]) =>
        client.get<{ items: EvaluationResult[] } | EvaluationResult[]>(`${P}/evaluations/compare`, {
          query: { ids: ids.join(",") },
        }),
    },
    monitoring: {
      overview: (window = "1h") => client.get<MonitoringOverview>(`${P}/monitoring/overview`, { query: { window } }),
    },
    cost: {
      summary: (group_by: CostGroupBy, from: string, to: string) =>
        client.get<CostSummary>(`${P}/cost/summary`, { query: { group_by, from, to } }),
      reportCsv: async (group_by: CostGroupBy, from: string, to: string): Promise<Blob> => {
        const res = await client.get<Response>(`${P}/cost/report.csv`, {
          query: { group_by, from, to },
          raw: true,
          headers: { Accept: "text/csv" },
        });
        return res.blob();
      },
    },
    openai: {
      models: () => client.get<{ object: "list"; data: OpenAIModel[] }>("/v1/models"),
    },
  };
}

export type Resources = ReturnType<typeof createResources>;

/** Some list endpoints return a Page, some a bare array - accept both. */
export function unwrapItems<T>(v: { items: T[] } | T[]): T[] {
  return Array.isArray(v) ? v : v.items;
}
