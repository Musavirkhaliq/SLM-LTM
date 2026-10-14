"use client";

import { History, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Gate } from "@/components/rbac/gate";
import { EmptyState, ErrorState } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useModelVersions, useRollbackVersion } from "@/lib/api/hooks";
import type { Model } from "@/lib/api/types";
import { formatDate, formatParams, titleCase } from "@/lib/utils";

export function VersionsSheet({ model, onOpenChange }: { model: Model | null; onOpenChange: (open: boolean) => void }) {
  const versions = useModelVersions(model?.id ?? null);
  const rollback = useRollbackVersion();

  return (
    <Sheet open={!!model} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col gap-0 overflow-y-auto p-0">
        <SheetHeader className="border-b p-6 pr-12">
          <SheetTitle>{model?.name} versions</SheetTitle>
          <SheetDescription>Immutable snapshots with lineage and benchmark scores. Activate an older version to roll back.</SheetDescription>
        </SheetHeader>
        <div className="flex-1 space-y-3 p-6">
          {versions.isLoading ? (
            [0, 1].map((i) => <Skeleton key={i} className="h-32 w-full" />)
          ) : versions.isError ? (
            <ErrorState error={versions.error} onRetry={() => versions.refetch()} />
          ) : !versions.data?.length ? (
            <EmptyState icon={History} title="No versions" description="Register a version to make this model deployable." />
          ) : (
            versions.data.map((v) => (
              <div key={v.id} className="rounded-lg border p-4">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold">v{v.version}</span>
                    <StatusBadge status={v.status} />
                  </div>
                  <Gate capability="manage_ml">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={v.status === "active"}
                      loading={rollback.isPending && rollback.variables?.version === v.version}
                      onClick={async () => {
                        try {
                          await rollback.mutateAsync({ id: v.model_id, version: v.version });
                          toast.success(`v${v.version} is now the active version`);
                        } catch (e) {
                          toast.error(e instanceof Error ? e.message : "Rollback failed");
                        }
                      }}
                    >
                      <RotateCcw /> Activate
                    </Button>
                  </Gate>
                </div>
                <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                  <div>
                    <dt className="text-muted-foreground">Parameters</dt>
                    <dd className="font-medium">{formatParams(v.param_count)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Quantization</dt>
                    <dd className="font-medium">{v.quantization && v.quantization !== "none" ? v.quantization.toUpperCase() : "None (BF16)"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Context</dt>
                    <dd className="font-medium">{v.context_length ? `${(v.context_length / 1024).toFixed(0)}K tokens` : "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Created</dt>
                    <dd className="font-medium">{formatDate(v.created_at)}</dd>
                  </div>
                  <div className="col-span-2">
                    <dt className="text-muted-foreground">Artifact</dt>
                    <dd className="truncate font-mono text-[11px]" title={v.artifact_uri}>
                      {v.artifact_uri}
                    </dd>
                  </div>
                </dl>
                {Object.keys(v.benchmark_scores).length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {Object.entries(v.benchmark_scores).map(([k, s]) => (
                      <Badge key={k} variant="secondary" className="font-normal">
                        {titleCase(k)} <span className="font-semibold tabular-nums">{(s * 100).toFixed(1)}</span>
                      </Badge>
                    ))}
                  </div>
                )}
                {v.training_job_id && <p className="mt-2 text-xs text-muted-foreground">Produced by a fine-tuning job</p>}
              </div>
            ))
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
