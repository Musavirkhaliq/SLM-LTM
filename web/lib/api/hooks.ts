"use client";

import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./index";
import { unwrapItems } from "./resources";
import type {
  ApiKeyCreateInput,
  CostGroupBy,
  DeploymentCreateInput,
  EvaluationCreateInput,
  ModelCreateInput,
  RagIndexCreateInput,
  RagQueryInput,
  ScaleInput,
  TrainingJobCreateInput,
} from "./types";

export const qk = {
  projects: ["projects"] as const,
  apiKeys: ["api-keys"] as const,
  apiKeyUsage: (id: string) => ["api-keys", id, "usage"] as const,
  models: ["models"] as const,
  versions: (id: string) => ["models", id, "versions"] as const,
  deployments: ["deployments"] as const,
  datasets: ["datasets"] as const,
  jobs: ["training-jobs"] as const,
  jobMetrics: (id: string) => ["training-jobs", id, "metrics"] as const,
  ragIndexes: ["rag", "indexes"] as const,
  ragDocs: (id: string) => ["rag", "indexes", id, "documents"] as const,
  evaluations: ["evaluations"] as const,
  evalResults: (id: string) => ["evaluations", id, "results"] as const,
  monitoring: (w: string) => ["monitoring", w] as const,
  cost: (g: string, f: string, t: string) => ["cost", g, f, t] as const,
  openaiModels: ["openai-models"] as const,
};

/* queries -------------------------------------------------------------------- */
export const useProjects = () => useQuery({ queryKey: qk.projects, queryFn: api.projects.list });
export const useApiKeys = () => useQuery({ queryKey: qk.apiKeys, queryFn: api.apiKeys.list });
export const useApiKeyUsage = (id: string) =>
  useQuery({ queryKey: qk.apiKeyUsage(id), queryFn: () => api.apiKeys.usage(id), staleTime: 60_000 });

export const useModels = () => useQuery({ queryKey: qk.models, queryFn: api.models.list });
export const useModelVersions = (id: string | null) =>
  useQuery({ queryKey: qk.versions(id ?? ""), queryFn: () => api.models.versions(id!), enabled: !!id });

export const useDeployments = () =>
  useQuery({ queryKey: qk.deployments, queryFn: api.deployments.list, refetchInterval: 5_000 });

export const useDatasets = () => useQuery({ queryKey: qk.datasets, queryFn: api.datasets.list });
export const useTrainingJobs = () =>
  useQuery({ queryKey: qk.jobs, queryFn: api.trainingJobs.list, refetchInterval: 3_000 });
export const useTrainingMetrics = (id: string | null, live: boolean) =>
  useQuery({
    queryKey: qk.jobMetrics(id ?? ""),
    queryFn: async () => unwrapItems(await api.trainingJobs.metrics(id!)),
    enabled: !!id,
    refetchInterval: live ? 2_000 : false,
    placeholderData: keepPreviousData,
  });

export const useRagIndexes = () =>
  useQuery({ queryKey: qk.ragIndexes, queryFn: api.rag.indexes, refetchInterval: 4_000 });
export const useRagDocuments = (indexId: string | null) =>
  useQuery({
    queryKey: qk.ragDocs(indexId ?? ""),
    // The documents listing is not part of CONTRACT.md §5: degrade to "no documents" on 404/405.
    queryFn: () => api.rag.documents(indexId!).catch(() => []),
    enabled: !!indexId,
    refetchInterval: 3_000,
  });

export const useEvaluations = () =>
  useQuery({ queryKey: qk.evaluations, queryFn: api.evaluations.list, refetchInterval: 4_000 });
export function useEvaluationResultsMany(ids: string[]) {
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: qk.evalResults(id),
      queryFn: async () => unwrapItems(await api.evaluations.results(id)),
    })),
  });
}

export const useEvaluationCompare = (ids: string[]) =>
  useQuery({
    queryKey: ["evaluations", "compare", ...ids] as const,
    queryFn: async () => unwrapItems(await api.evaluations.compare(ids)),
    enabled: ids.length > 0,
  });

export const useMonitoringOverview = (window: string) =>
  useQuery({ queryKey: qk.monitoring(window), queryFn: () => api.monitoring.overview(window), refetchInterval: 15_000 });

export const useCostSummary = (group: CostGroupBy, from: string, to: string, enabled = true) =>
  useQuery({
    queryKey: qk.cost(group, from, to),
    queryFn: () => api.cost.summary(group, from, to),
    enabled,
    placeholderData: keepPreviousData,
  });

export const useOpenAIModels = (enabled = true) =>
  useQuery({ queryKey: qk.openaiModels, queryFn: async () => (await api.openai.models()).data, enabled });

/* mutations ------------------------------------------------------------------ */
function useInvalidatingMutation<TVars, TData>(fn: (v: TVars) => Promise<TData>, keys: (readonly unknown[])[]) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      keys.forEach((k) => qc.invalidateQueries({ queryKey: k }));
    },
  });
}

export const useCreateApiKey = () => useInvalidatingMutation((v: ApiKeyCreateInput) => api.apiKeys.create(v), [qk.apiKeys]);
export const useRotateApiKey = () => useInvalidatingMutation((id: string) => api.apiKeys.rotate(id), [qk.apiKeys]);
export const useRevokeApiKey = () => useInvalidatingMutation((id: string) => api.apiKeys.revoke(id), [qk.apiKeys]);

export const useCreateModel = () => useInvalidatingMutation((v: ModelCreateInput) => api.models.create(v), [qk.models]);
export const useRollbackVersion = () =>
  useInvalidatingMutation((v: { id: string; version: number }) => api.models.rollback(v.id, v.version), [qk.models, ["models"]]);

export const useCreateDeployment = () =>
  useInvalidatingMutation((v: DeploymentCreateInput) => api.deployments.create(v), [qk.deployments]);
export const useStartDeployment = () => useInvalidatingMutation((id: string) => api.deployments.start(id), [qk.deployments]);
export const useStopDeployment = () => useInvalidatingMutation((id: string) => api.deployments.stop(id), [qk.deployments]);
export const useScaleDeployment = () =>
  useInvalidatingMutation((v: { id: string } & ScaleInput) => api.deployments.scale(v.id, v), [qk.deployments]);
export const useRollbackDeployment = () =>
  useInvalidatingMutation((id: string) => api.deployments.rollback(id), [qk.deployments]);
export const useDeleteDeployment = () => useInvalidatingMutation((id: string) => api.deployments.remove(id), [qk.deployments]);

export const useUploadDataset = () =>
  useInvalidatingMutation((v: { file: File; name?: string; purpose?: "finetune" | "eval" }) => api.datasets.upload(v.file, v), [
    qk.datasets,
  ]);
export const useCreateTrainingJob = () =>
  useInvalidatingMutation((v: TrainingJobCreateInput) => api.trainingJobs.create(v), [qk.jobs]);
export const useCancelTrainingJob = () => useInvalidatingMutation((id: string) => api.trainingJobs.cancel(id), [qk.jobs]);

export const useCreateRagIndex = () =>
  useInvalidatingMutation((v: RagIndexCreateInput) => api.rag.createIndex(v), [qk.ragIndexes]);
export const useUploadRagDocument = () =>
  useInvalidatingMutation((v: { indexId: string; file: File }) => api.rag.uploadDocument(v.indexId, v.file), [
    ["rag"],
  ]);
export const useRagQuery = () =>
  useMutation({ mutationFn: (v: { indexId: string } & RagQueryInput) => api.rag.query(v.indexId, v) });

export const useCreateEvaluation = () =>
  useInvalidatingMutation((v: EvaluationCreateInput) => api.evaluations.create(v), [qk.evaluations]);
