"use client";

import { Database, Plus } from "lucide-react";
import { CardGridSkeleton, EmptyState, ErrorState } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { Gate } from "@/components/rbac/gate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useRagIndexes } from "@/lib/api/hooks";
import { formatNumber, timeAgo } from "@/lib/utils";

export function IndexesTab({ onNew, onOpenDocs }: { onNew: () => void; onOpenDocs: (id: string) => void }) {
  const indexes = useRagIndexes();
  if (indexes.isLoading) return <CardGridSkeleton count={3} />;
  if (indexes.isError) return <ErrorState error={indexes.error} onRetry={() => indexes.refetch()} />;
  if (!indexes.data?.length)
    return (
      <EmptyState
        icon={Database}
        title="No indexes yet"
        description="Design a pipeline to create your first vector index."
        action={
          <Gate capability="manage_ml">
            <Button onClick={onNew}>
              <Plus /> New index
            </Button>
          </Gate>
        }
      />
    );
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {indexes.data.map((i) => (
        <Card key={i.id} className="flex flex-col p-5">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold">{i.name}</h3>
              <p className="truncate font-mono text-[11px] text-muted-foreground">{i.collection}</p>
            </div>
            <StatusBadge status={i.status} />
          </div>
          <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
            {[
              ["Documents", formatNumber(i.doc_count, 0)],
              ["Chunks", formatNumber(i.chunk_count, 1)],
              ["Dimensions", String(i.embedding_dim)],
            ].map(([k, v]) => (
              <div key={k} className="rounded-md bg-muted/60 px-2 py-2">
                <dd className="text-base font-semibold tabular-nums">{v}</dd>
                <dt className="text-[11px] text-muted-foreground">{k}</dt>
              </div>
            ))}
          </dl>
          <div className="mt-4 flex flex-wrap gap-1.5">
            <Badge variant="secondary" className="capitalize">
              {i.backend}
            </Badge>
            <Badge variant="secondary">{i.embedding_model}</Badge>
            <Badge variant="outline">
              chunk {i.chunk_size}/{i.chunk_overlap}
            </Badge>
            {i.hybrid_search && <Badge variant="outline">hybrid</Badge>}
            {i.reranker_model && <Badge variant="outline">rerank</Badge>}
          </div>
          <div className="mt-auto flex items-center justify-between pt-4 text-xs text-muted-foreground">
            <span>Created {timeAgo(i.created_at)}</span>
            <Button size="sm" variant="outline" onClick={() => onOpenDocs(i.id)}>
              Documents & query
            </Button>
          </div>
        </Card>
      ))}
    </div>
  );
}
