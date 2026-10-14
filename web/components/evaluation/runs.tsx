"use client";

import { FlaskConical } from "lucide-react";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useEvaluations } from "@/lib/api/hooks";
import { timeAgo, titleCase } from "@/lib/utils";

export function Runs() {
  const evaluations = useEvaluations();
  if (evaluations.isLoading) return <Card><TableSkeleton rows={4} cols={5} /></Card>;
  if (evaluations.isError) return <ErrorState error={evaluations.error} onRetry={() => evaluations.refetch()} />;
  if (!evaluations.data?.length) return <EmptyState icon={FlaskConical} title="No evaluation runs" description="Start an evaluation from a model card or the New evaluation button." />;
  return (
    <Card>
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead>Name</TableHead>
            <TableHead>Dataset</TableHead>
            <TableHead>Models</TableHead>
            <TableHead>Metrics</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Started</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {evaluations.data.map((e) => (
            <TableRow key={e.id}>
              <TableCell className="font-medium">{e.name}</TableCell>
              <TableCell className="text-muted-foreground">{e.dataset_name ?? e.dataset_id.slice(0, 8)}</TableCell>
              <TableCell className="tabular-nums">{e.model_version_ids.length}</TableCell>
              <TableCell className="max-w-[260px] truncate text-xs text-muted-foreground" title={e.metrics.map(titleCase).join(", ")}>
                {e.metrics.map(titleCase).join(", ")}
              </TableCell>
              <TableCell>
                <StatusBadge status={e.status} />
              </TableCell>
              <TableCell className="text-muted-foreground">{timeAgo(e.created_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}
