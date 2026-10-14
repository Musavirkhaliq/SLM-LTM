"use client";

import Link from "next/link";
import { Activity, AlertOctagon, AlertTriangle, ArrowRight, Cpu, Info, Rocket, Wallet } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_PROPS, CHART_COLORS, ChartTooltip, GRID_PROPS, Meter, Sparkline } from "@/components/common/charts";
import { EmptyState, ErrorState, PageHeader, RestrictedState } from "@/components/common/page";
import { StatCard } from "@/components/common/stat-card";
import { StatusBadge } from "@/components/common/status-badge";
import { useAuth } from "@/components/providers/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCostSummary, useDeployments, useMonitoringOverview, useTrainingJobs } from "@/lib/api/hooks";
import type { AlertItem } from "@/lib/api/types";
import { cn, formatNumber, formatUsd, timeAgo } from "@/lib/utils";

const SEVERITY: Record<AlertItem["severity"], { icon: typeof Info; tone: string; label: string }> = {
  critical: { icon: AlertOctagon, tone: "text-destructive", label: "Critical" },
  warning: { icon: AlertTriangle, tone: "text-warning", label: "Warning" },
  info: { icon: Info, tone: "text-info", label: "Info" },
};

function monthRange() {
  const now = new Date();
  const from = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString().slice(0, 10);
  return { from, to: now.toISOString().slice(0, 10) };
}

export default function DashboardPage() {
  const { can, me } = useAuth();
  const isAdmin = can("org_admin");
  const overview = useMonitoringOverview("24h");
  const deployments = useDeployments();
  const jobs = useTrainingJobs();
  const { from, to } = monthRange();
  const cost = useCostSummary("model", from, to, isAdmin);

  const running = deployments.data?.filter((d) => d.status === "running") ?? [];
  const points = overview.data?.points ?? [];
  const last = points[points.length - 1];
  const gpus = overview.data?.gpus ?? [];
  const avgGpu = gpus.length ? gpus.reduce((s, g) => s + g.util, 0) / gpus.length : 0;
  const totalRequests = points.reduce((s, p) => s + p.requests_per_min * 15, 0); // 15-min buckets
  const costDelta = cost.data && cost.data.previous_total_usd ? ((cost.data.total_usd - cost.data.previous_total_usd) / cost.data.previous_total_usd) * 100 : undefined;

  return (
    <div className="page-container animate-fade-in">
      <PageHeader
        title={`Welcome back${me ? `, ${me.user.display_name.split(" ")[0]}` : ""}`}
        description="Fleet health, usage and spend across your deployments."
        actions={
          <>
            <Button variant="outline" asChild>
              <Link href="/playground">Open Playground</Link>
            </Button>
            <Button asChild>
              <Link href="/models">
                <Rocket /> Deploy a model
              </Link>
            </Button>
          </>
        }
      />

      {/* KPI row */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Active deployments"
          icon={Rocket}
          loading={deployments.isLoading}
          value={`${running.length}`}
          hint={deployments.data ? `${deployments.data.length} total · ${deployments.data.filter((d) => d.status === "failed").length} failed` : undefined}
        />
        <StatCard label="GPU utilisation" icon={Cpu} loading={overview.isLoading} value={`${avgGpu.toFixed(0)}%`} hint={`${gpus.length} GPUs across ${new Set(gpus.map((g) => g.node)).size} nodes`}>
          <Meter value={avgGpu} className="mt-3" label="Average GPU utilisation" />
        </StatCard>
        <StatCard label="Requests (24h)" icon={Activity} loading={overview.isLoading} value={formatNumber(totalRequests)} hint={last ? `${last.requests_per_min.toLocaleString()} req/min now` : undefined}>
          <Sparkline className="mt-2" data={points.map((p) => p.requests_per_min)} label="Requests per minute, last 24 hours" />
        </StatCard>
        {isAdmin ? (
          <StatCard
            label="Spend this month"
            icon={Wallet}
            loading={cost.isLoading}
            value={cost.data ? formatUsd(cost.data.total_usd, { digits: 0 }) : "—"}
            delta={costDelta}
            deltaGoodWhen="down"
            hint="vs. same period last month"
          />
        ) : (
          <StatCard label="Spend this month" icon={Wallet} value="Restricted" hint="Cost reports require the Org Admin role" />
        )}
      </div>

      {/* Usage + alerts */}
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Model usage</CardTitle>
            <CardDescription>Tokens served per model, last 24 hours</CardDescription>
          </CardHeader>
          <CardContent>
            {overview.isLoading ? (
              <Skeleton className="h-64 w-full" />
            ) : overview.isError ? (
              <ErrorState error={overview.error} onRetry={() => overview.refetch()} />
            ) : (
              <div className="h-64" role="img" aria-label="Bar chart of tokens served per model">
                <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                  <BarChart data={overview.data?.usage_by_model} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
                    <CartesianGrid {...GRID_PROPS} horizontal={false} vertical />
                    <XAxis type="number" {...AXIS_PROPS} tickFormatter={(v: number) => formatNumber(v, 0)} />
                    <YAxis type="category" dataKey="model" width={132} {...AXIS_PROPS} fontSize={12} />
                    <Tooltip cursor={{ fill: "hsl(var(--muted) / 0.5)" }} content={<ChartTooltip valueFormatter={(v) => `${formatNumber(v, 1)} tokens`} />} />
                    <Bar dataKey="tokens" name="Tokens" radius={[0, 4, 4, 0]} barSize={18}>
                      {overview.data?.usage_by_model.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div className="space-y-1">
              <CardTitle>Alerts</CardTitle>
              <CardDescription>Needs attention</CardDescription>
            </div>
            {overview.data && <Badge variant="secondary">{overview.data.alerts.length}</Badge>}
          </CardHeader>
          <CardContent>
            {overview.isLoading ? (
              <div className="space-y-3">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-14 w-full" />
                ))}
              </div>
            ) : overview.data?.alerts.length ? (
              <ul className="-mx-2 max-h-64 space-y-1 overflow-auto">
                {overview.data.alerts.slice(0, 5).map((a) => {
                  const s = SEVERITY[a.severity];
                  return (
                    <li key={a.id} className="flex gap-3 rounded-md px-2 py-2 hover:bg-muted/50">
                      <s.icon className={cn("mt-0.5 h-4 w-4 shrink-0", s.tone)} aria-label={s.label} />
                      <div className="min-w-0">
                        <p className="text-sm font-medium leading-snug">{a.title}</p>
                        <p className="line-clamp-2 text-xs text-muted-foreground">{a.detail}</p>
                        <p className="mt-0.5 text-[11px] text-muted-foreground/80">{timeAgo(a.fired_at)}</p>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <EmptyState title="All clear" description="No active alerts." className="py-8" />
            )}
          </CardContent>
        </Card>
      </div>

      {/* GPU + deployments */}
      <div className="mt-4 grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>GPU usage</CardTitle>
            <CardDescription>Utilisation and memory per device</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {overview.isLoading
              ? [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10 w-full" />)
              : gpus.map((g) => (
                  <div key={g.id}>
                    <div className="mb-1 flex items-center justify-between text-xs">
                      <span className="font-medium">
                        {g.id} <span className="font-normal text-muted-foreground">· {g.name}</span>
                      </span>
                      <span className="tabular-nums text-muted-foreground">
                        {g.util.toFixed(0)}% · {g.memory_used_gb.toFixed(0)}/{g.memory_total_gb} GB · {g.temperature_c}°C
                      </span>
                    </div>
                    <Meter value={g.util} label={`${g.id} utilisation`} />
                  </div>
                ))}
          </CardContent>
        </Card>

        <Card className="lg:col-span-3">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div className="space-y-1">
              <CardTitle>Active deployments</CardTitle>
              <CardDescription>Live endpoints and their p95 latency</CardDescription>
            </div>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/deployments">
                View all <ArrowRight />
              </Link>
            </Button>
          </CardHeader>
          <CardContent>
            {deployments.isLoading ? (
              <div className="space-y-3">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : deployments.isError ? (
              <ErrorState error={deployments.error} onRetry={() => deployments.refetch()} />
            ) : deployments.data?.length ? (
              <ul className="divide-y">
                {deployments.data.slice(0, 6).map((d) => (
                  <li key={d.id} className="flex items-center gap-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{d.name}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {d.model_name} v{d.model_version} · {d.gpu_count} GPU × {d.replicas_current ?? 0} replicas
                      </p>
                    </div>
                    <span className="w-16 text-right text-xs tabular-nums text-muted-foreground">{d.p95_latency_ms ? `${d.p95_latency_ms} ms` : "—"}</span>
                    <StatusBadge status={d.status} />
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState icon={Rocket} title="No deployments yet" description="Deploy a model from the registry to get an OpenAI-compatible endpoint." />
            )}
          </CardContent>
        </Card>
      </div>

      {/* Cost + training */}
      <div className="mt-4 grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div className="space-y-1">
              <CardTitle>Cost summary</CardTitle>
              <CardDescription>Daily spend, month to date</CardDescription>
            </div>
            {isAdmin && (
              <Button variant="ghost" size="sm" asChild>
                <Link href="/cost">
                  Details <ArrowRight />
                </Link>
              </Button>
            )}
          </CardHeader>
          <CardContent>
            {!isAdmin ? (
              <RestrictedState title="Cost reports are restricted" description="Ask an organization admin for access to spend breakdowns." className="py-10" />
            ) : cost.isLoading ? (
              <Skeleton className="h-56 w-full" />
            ) : cost.isError ? (
              <ErrorState error={cost.error} onRetry={() => cost.refetch()} />
            ) : (
              <div className="h-56" role="img" aria-label="Area chart of daily spend this month">
                <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                  <AreaChart data={cost.data?.daily} margin={{ left: -8, right: 8, top: 4, bottom: 0 }}>
                    <defs>
                      <linearGradient id="costFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="hsl(var(--chart-1))" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="hsl(var(--chart-1))" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid {...GRID_PROPS} />
                    <XAxis dataKey="day" {...AXIS_PROPS} tickFormatter={(d: string) => d.slice(5)} minTickGap={24} />
                    <YAxis {...AXIS_PROPS} tickFormatter={(v: number) => `$${v}`} width={48} />
                    <Tooltip content={<ChartTooltip valueFormatter={(v) => formatUsd(v)} />} />
                    <Area type="monotone" dataKey="usd" name="Spend" stroke="hsl(var(--chart-1))" strokeWidth={2} fill="url(#costFill)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div className="space-y-1">
              <CardTitle>Recent training jobs</CardTitle>
              <CardDescription>Fine-tuning activity</CardDescription>
            </div>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/fine-tuning">
                Open <ArrowRight />
              </Link>
            </Button>
          </CardHeader>
          <CardContent>
            {jobs.isLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : jobs.data?.length ? (
              <ul className="divide-y">
                {jobs.data.slice(0, 4).map((j) => (
                  <li key={j.id} className="flex items-center gap-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{j.name}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {j.base_model_name} · {j.method.toUpperCase()} · {timeAgo(j.created_at)}
                      </p>
                    </div>
                    <StatusBadge status={j.status} />
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="No training jobs" description="Start a fine-tuning run from the Fine-tuning page." />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
