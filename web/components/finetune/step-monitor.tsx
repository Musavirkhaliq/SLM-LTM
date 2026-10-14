"use client";

import * as React from "react";
import Link from "next/link";
import { Activity, Coins, Cpu, Layers, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_PROPS, ChartTooltip, GRID_PROPS, Legend } from "@/components/common/charts";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { EmptyState, ErrorState } from "@/components/common/page";
import { StatCard } from "@/components/common/stat-card";
import { StatusBadge } from "@/components/common/status-badge";
import { Gate } from "@/components/rbac/gate";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { useCancelTrainingJob, useTrainingJobs, useTrainingMetrics } from "@/lib/api/hooks";
import { cn, formatDuration, formatNumber, formatUsd, timeAgo } from "@/lib/utils";

export function StepMonitor({ jobId, onSelect }: { jobId: string | null; onSelect: (id: string) => void }) {
  const jobs = useTrainingJobs();
  const cancel = useCancelTrainingJob();
  const [confirmCancel, setConfirmCancel] = React.useState(false);

  const selectedId = jobId ?? jobs.data?.[0]?.id ?? null;
  const job = jobs.data?.find((j) => j.id === selectedId);
  const live = job?.status === "running" || job?.status === "queued";
  const metrics = useTrainingMetrics(selectedId, !!live);

  if (jobs.isLoading) return <Skeleton className="h-96 w-full" />;
  if (jobs.isError) return <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} />;
  if (!jobs.data?.length || !job) {
    return <EmptyState icon={Activity} title="No training jobs yet" description="Launch a job to monitor its loss curve, tokens and cost here." />;
  }

  const m = metrics.data ?? [];
  const lastMetric = m[m.length - 1];
  const step = job.current_step ?? 0;
  const total = job.total_steps ?? 0;
  const pct = total ? Math.min(100, (step / total) * 100) : job.status === "succeeded" ? 100 : 0;
  const started = job.started_at ? Date.parse(job.started_at) : null;
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  const elapsed = started ? Math.max(0, (end - started) / 1000) : 0;
  const eta = live && step > 0 && total ? (elapsed / step) * (total - step) : null;
  const lossData = m.map((p) => ({ step: p.step, loss: p.loss, eval: p.eval_loss }));

  return (
    <div className="grid gap-6 lg:grid-cols-4">
      <aside aria-label="Training jobs" className="lg:col-span-1">
        <h3 className="mb-2 text-sm font-semibold">Jobs</h3>
        <ul className="space-y-1.5">
          {jobs.data.map((j) => (
            <li key={j.id}>
              <button
                type="button"
                onClick={() => onSelect(j.id)}
                aria-current={j.id === selectedId ? "true" : undefined}
                className={cn("w-full rounded-lg border p-3 text-left transition-colors hover:bg-muted/40", j.id === selectedId && "border-primary bg-accent/60 ring-1 ring-primary")}
              >
                <span className="block truncate text-sm font-medium">{j.name}</span>
                <span className="mt-1.5 block">
                  <StatusBadge status={j.status} />
                </span>
                <span className="mt-1.5 block truncate text-xs text-muted-foreground">
                  {j.base_model_name} · {timeAgo(j.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="space-y-4 lg:col-span-3" aria-live="polite">
        <Card className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="truncate text-base font-semibold">{job.name}</h3>
                <StatusBadge status={job.status} />
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {job.base_model_name} · {job.method.toUpperCase()} · {job.dataset_name} · {job.gpu_count} GPU
              </p>
            </div>
            {(job.status === "running" || job.status === "queued") && (
              <Gate capability="manage_ml">
                <Button variant="outline" size="sm" onClick={() => setConfirmCancel(true)}>
                  <XCircle /> Cancel job
                </Button>
              </Gate>
            )}
            {job.status === "succeeded" && (
              <Button size="sm" asChild>
                <Link href="/models">View model</Link>
              </Button>
            )}
          </div>
          <div className="mt-4">
            <div className="mb-1.5 flex justify-between text-xs text-muted-foreground">
              <span>
                Step {step.toLocaleString()} / {total ? total.toLocaleString() : "—"}
              </span>
              <span className="tabular-nums">
                {pct.toFixed(0)}%{eta ? ` · ~${formatDuration(eta)} left` : elapsed ? ` · ${formatDuration(elapsed)} elapsed` : ""}
              </span>
            </div>
            <Progress value={pct} aria-label="Training progress" indicatorClassName={cn(job.status === "failed" && "bg-destructive", job.status === "cancelled" && "bg-muted-foreground")} />
          </div>
          {job.error && (
            <p role="alert" className="mt-4 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {job.error}
            </p>
          )}
        </Card>

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard label="Current loss" icon={Activity} value={lastMetric?.loss != null ? lastMetric.loss.toFixed(3) : "—"} hint={lastMetric?.eval_loss != null ? `eval ${lastMetric.eval_loss.toFixed(3)}` : undefined} />
          <StatCard label="Tokens processed" icon={Layers} value={formatNumber(job.tokens_processed)} hint={job.total_tokens ? `of ${formatNumber(job.total_tokens)}` : undefined} />
          <StatCard label="GPU utilisation" icon={Cpu} value={lastMetric?.gpu_util != null && live ? `${lastMetric.gpu_util.toFixed(0)}%` : "—"} hint={live ? "live" : "not running"} />
          <StatCard label="Cost so far" icon={Coins} value={formatUsd(job.cost_so_far_usd ?? 0)} hint={job.est_cost_usd ? `est. ${formatUsd(job.est_cost_usd)} total` : undefined} />
        </div>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="space-y-1">
                <CardTitle>Loss curve</CardTitle>
                <CardDescription>Training loss per step with periodic evaluation loss</CardDescription>
              </div>
              <Legend items={[{ label: "Train loss", color: "hsl(var(--chart-1))" }, { label: "Eval loss", color: "hsl(var(--chart-3))" }]} />
            </div>
          </CardHeader>
          <CardContent>
            {metrics.isLoading ? (
              <Skeleton className="h-64 w-full" />
            ) : lossData.length < 2 ? (
              <EmptyState icon={Activity} title="Waiting for the first steps" description="Metrics appear as soon as the job starts training." className="py-10" />
            ) : (
              <div className="h-64" role="img" aria-label="Line chart of training and evaluation loss by step">
                <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                  <LineChart data={lossData} margin={{ left: -8, right: 12, top: 4, bottom: 0 }}>
                    <CartesianGrid {...GRID_PROPS} />
                    <XAxis dataKey="step" type="number" domain={[0, "dataMax"]} {...AXIS_PROPS} />
                    <YAxis {...AXIS_PROPS} domain={["auto", "auto"]} tickFormatter={(v: number) => v.toFixed(1)} width={40} />
                    <Tooltip content={<ChartTooltip labelFormatter={(l) => `Step ${l}`} valueFormatter={(v) => v.toFixed(4)} />} />
                    <Line type="monotone" dataKey="loss" name="Train loss" stroke="hsl(var(--chart-1))" strokeWidth={2} dot={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="eval" name="Eval loss" stroke="hsl(var(--chart-3))" strokeWidth={2} connectNulls dot={{ r: 2.5 }} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>GPU utilisation</CardTitle>
            <CardDescription>Percent busy across assigned devices</CardDescription>
          </CardHeader>
          <CardContent>
            {m.length < 2 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">No samples yet.</p>
            ) : (
              <div className="h-36" role="img" aria-label="Area chart of GPU utilisation during training">
                <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                  <AreaChart data={m.map((p) => ({ step: p.step, util: p.gpu_util }))} margin={{ left: -8, right: 12, top: 4, bottom: 0 }}>
                    <CartesianGrid {...GRID_PROPS} />
                    <XAxis dataKey="step" type="number" domain={[0, "dataMax"]} {...AXIS_PROPS} />
                    <YAxis domain={[0, 100]} {...AXIS_PROPS} width={40} tickFormatter={(v: number) => `${v}%`} />
                    <Tooltip content={<ChartTooltip labelFormatter={(l) => `Step ${l}`} valueFormatter={(v) => `${v.toFixed(0)}%`} />} />
                    <Area type="monotone" dataKey="util" name="GPU" stroke="hsl(var(--chart-2))" fill="hsl(var(--chart-2) / 0.18)" strokeWidth={2} isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title={`Cancel ${job.name}?`}
        description="Training stops immediately. Checkpoints saved so far are kept, but no model version will be produced."
        confirmLabel="Cancel job"
        destructive
        onConfirm={async () => {
          try {
            await cancel.mutateAsync(job.id);
            toast.success("Job cancelled");
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Could not cancel the job");
          }
        }}
      />
    </div>
  );
}
