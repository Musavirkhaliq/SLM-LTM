"use client";

import { Boxes } from "lucide-react";
import { CardGridSkeleton, EmptyState, ErrorState } from "@/components/common/page";
import { Badge } from "@/components/ui/badge";
import { useModels } from "@/lib/api/hooks";
import { cn, formatParams } from "@/lib/utils";

export function StepModel({ versionId, onSelect }: { versionId: string | null; onSelect: (versionId: string, modelName: string) => void }) {
  const models = useModels();
  if (models.isLoading) return <CardGridSkeleton count={6} />;
  if (models.isError) return <ErrorState error={models.error} onRetry={() => models.refetch()} />;
  const usable = (models.data ?? []).filter((m) => m.latest_version);
  if (!usable.length) return <EmptyState icon={Boxes} title="No base models available" description="Register a model in the registry first." />;
  return (
    <div role="radiogroup" aria-label="Base model" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {usable.map((m) => {
        const v = m.latest_version!;
        const selected = v.id === versionId;
        return (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onSelect(v.id, m.name)}
            className={cn("rounded-lg border p-4 text-left transition-colors hover:bg-muted/40", selected && "border-primary bg-accent/60 ring-1 ring-primary")}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-semibold">{m.name}</span>
              <Badge variant="secondary">v{v.version}</Badge>
            </div>
            <p className="mt-1 line-clamp-2 min-h-[2.25rem] text-xs text-muted-foreground">{m.description}</p>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <Badge variant="outline">{formatParams(v.param_count)}</Badge>
              {v.context_length ? <Badge variant="outline">{Math.round(v.context_length / 1024)}K ctx</Badge> : null}
              {m.license && <Badge variant="outline">{m.license}</Badge>}
              {m.source === "finetune" && <Badge>Fine-tuned</Badge>}
            </div>
          </button>
        );
      })}
    </div>
  );
}
