"use client";

import * as React from "react";
import { MoreHorizontal, Pause, Play, Plus, RotateCcw, Rocket, Scaling, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { CopyButton } from "@/components/common/copy-button";
import { EmptyState, ErrorState, Field, PageHeader, TableSkeleton } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { DeployDialog, TARGET_LABELS } from "@/components/deployments/deploy-dialog";
import { Gate } from "@/components/rbac/gate";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCan } from "@/components/providers/auth-provider";
import {
  useDeleteDeployment,
  useDeployments,
  useRollbackDeployment,
  useScaleDeployment,
  useStartDeployment,
  useStopDeployment,
} from "@/lib/api/hooks";
import type { Deployment } from "@/lib/api/types";
import { PUBLIC_GATEWAY_URL } from "@/lib/config";
import { cn, timeAgo } from "@/lib/utils";

function endpointUrl(d: Deployment) {
  return d.endpoint_url ?? `${PUBLIC_GATEWAY_URL}/v1/chat/completions`;
}

function ScaleDialog({ deployment, onOpenChange }: { deployment: Deployment | null; onOpenChange: (o: boolean) => void }) {
  const scale = useScaleDeployment();
  const [min, setMin] = React.useState(1);
  const [max, setMax] = React.useState(1);
  const [gpu, setGpu] = React.useState(1);
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (deployment) {
      setMin(deployment.replicas_min);
      setMax(deployment.replicas_max);
      setGpu(deployment.gpu_count);
      setError(null);
    }
  }, [deployment]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!deployment) return;
    if (max < 1 || min < 0 || max < min) return setError("Max replicas must be ≥ 1 and ≥ min replicas.");
    if (gpu < 1) return setError("At least 1 GPU per replica.");
    try {
      await scale.mutateAsync({ id: deployment.id, replicas_min: min, replicas_max: max, gpu_count: gpu });
      toast.success(`Scaled ${deployment.name}`, { description: `${min}–${max} replicas × ${gpu} GPU` });
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scaling failed");
    }
  }

  return (
    <Dialog open={!!deployment} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Scale {deployment?.name}</DialogTitle>
          <DialogDescription>The autoscaler keeps replicas between the minimum and maximum based on queue depth and GPU utilisation.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-4" noValidate>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Min replicas" htmlFor="sc-min">
              <Input id="sc-min" type="number" min={0} max={32} value={min} onChange={(e) => setMin(Number(e.target.value))} />
            </Field>
            <Field label="Max replicas" htmlFor="sc-max">
              <Input id="sc-max" type="number" min={1} max={32} value={max} onChange={(e) => setMax(Number(e.target.value))} />
            </Field>
            <Field label="GPUs / replica" htmlFor="sc-gpu">
              <Input id="sc-gpu" type="number" min={1} max={8} value={gpu} onChange={(e) => setGpu(Number(e.target.value))} />
            </Field>
          </div>
          <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
            Peak capacity: <span className="font-medium text-foreground">{max * gpu} GPU</span> · always-on:{" "}
            <span className="font-medium text-foreground">{min * gpu} GPU</span>
          </p>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={scale.isPending}>
              Apply
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function DeploymentsPage() {
  const deployments = useDeployments();
  const start = useStartDeployment();
  const stop = useStopDeployment();
  const rollback = useRollbackDeployment();
  const remove = useDeleteDeployment();
  const canManage = useCan("manage_deploy");
  const [createOpen, setCreateOpen] = React.useState(false);
  const [scaleTarget, setScaleTarget] = React.useState<Deployment | null>(null);
  const [stopTarget, setStopTarget] = React.useState<Deployment | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<Deployment | null>(null);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast.success(ok);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Action failed");
    }
  };

  return (
    <div className="page-container animate-fade-in">
      <PageHeader
        title="Deployments"
        description="Serving endpoints for your model versions. Start, stop and scale them without downtime."
        actions={
          <Gate capability="manage_deploy">
            <Button onClick={() => setCreateOpen(true)}>
              <Plus /> New deployment
            </Button>
          </Gate>
        }
      />

      <Card>
        {deployments.isLoading ? (
          <TableSkeleton rows={6} cols={6} />
        ) : deployments.isError ? (
          <ErrorState error={deployments.error} onRetry={() => deployments.refetch()} className="m-6" title="Could not load deployments" />
        ) : !deployments.data?.length ? (
          <EmptyState
            icon={Rocket}
            title="No deployments yet"
            description="Deploy a model version to get a private, OpenAI-compatible endpoint."
            className="m-6"
            action={
              <Gate capability="manage_deploy">
                <Button onClick={() => setCreateOpen(true)}>
                  <Plus /> New deployment
                </Button>
              </Gate>
            }
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>Deployment</TableHead>
                <TableHead>Endpoint</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">p95 latency</TableHead>
                <TableHead className="w-[1%] text-right">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {deployments.data.map((d) => {
                const running = d.status === "running";
                const transitional = d.status === "provisioning" || d.status === "rolling_back";
                return (
                  <TableRow key={d.id}>
                    <TableCell className="min-w-[170px]">
                      <p className="whitespace-nowrap font-medium">{d.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {d.model_name} v{d.model_version}
                      </p>
                      <p className="text-xs text-muted-foreground">{TARGET_LABELS[d.target]}</p>
                    </TableCell>
                    <TableCell className="max-w-[260px]">
                      <div className="flex items-center gap-1">
                        <code className="truncate rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]" title={endpointUrl(d)}>
                          {endpointUrl(d)}
                        </code>
                        <CopyButton value={endpointUrl(d)} label={`Copy endpoint URL for ${d.name}`} />
                      </div>
                      <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                        model: <code className="font-mono">{d.endpoint_alias ?? d.name}</code>
                        <CopyButton value={d.endpoint_alias ?? d.name} label={`Copy model alias for ${d.name}`} className="h-6 w-6" />
                      </div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={d.status} />
                      <p className="mt-1 whitespace-nowrap text-[11px] tabular-nums text-muted-foreground">
                        {d.replicas_current ?? 0}/{d.replicas_max} replicas · {d.gpu_count} GPU
                      </p>
                      <p className="whitespace-nowrap text-[11px] text-muted-foreground">updated {timeAgo(d.updated_at)}</p>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {running && d.p95_latency_ms ? (
                        <span className={cn("font-medium", d.p95_latency_ms > 350 && "text-warning")}>{d.p95_latency_ms} ms</span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1.5">
                        {running || transitional ? (
                          <Gate capability="manage_deploy">
                            <Button size="sm" variant="outline" onClick={() => setStopTarget(d)} disabled={transitional}>
                              <Pause /> <span className="sr-only 2xl:not-sr-only">Stop</span>
                            </Button>
                          </Gate>
                        ) : (
                          <Gate capability="manage_deploy">
                            <Button
                              size="sm"
                              variant="outline"
                              loading={start.isPending && start.variables === d.id}
                              onClick={() => act(() => start.mutateAsync(d.id), `Starting ${d.name}`)}
                            >
                              <Play /> <span className="sr-only 2xl:not-sr-only">Start</span>
                            </Button>
                          </Gate>
                        )}
                        <Gate capability="manage_deploy">
                          <Button size="sm" variant="outline" onClick={() => setScaleTarget(d)}>
                            <Scaling /> <span className="sr-only 2xl:not-sr-only">Scale</span>
                          </Button>
                        </Gate>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button size="icon-sm" variant="ghost" aria-label={`More actions for ${d.name}`}>
                              <MoreHorizontal />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              disabled={!canManage}
                              onSelect={() => act(() => rollback.mutateAsync(d.id), `Rolling back ${d.name}`)}
                            >
                              <RotateCcw /> Roll back
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem destructive disabled={!canManage} onSelect={() => setDeleteTarget(d)}>
                              <Trash2 /> Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Card>

      <DeployDialog open={createOpen} onOpenChange={setCreateOpen} />
      <ScaleDialog deployment={scaleTarget} onOpenChange={(o) => !o && setScaleTarget(null)} />
      <ConfirmDialog
        open={!!stopTarget}
        onOpenChange={(o) => !o && setStopTarget(null)}
        title={`Stop ${stopTarget?.name}?`}
        description="The endpoint will stop serving traffic and GPU capacity is released. In-flight requests are drained first. You can start it again at any time."
        confirmLabel="Stop deployment"
        destructive
        onConfirm={() => act(() => stop.mutateAsync(stopTarget!.id), `Stopped ${stopTarget!.name}`)}
      />
      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title={`Delete ${deleteTarget?.name}?`}
        description="This permanently removes the deployment and its endpoint routes. This cannot be undone."
        confirmLabel="Delete"
        destructive
        onConfirm={() => act(() => remove.mutateAsync(deleteTarget!.id), `Deleted ${deleteTarget!.name}`)}
      />
    </div>
  );
}
