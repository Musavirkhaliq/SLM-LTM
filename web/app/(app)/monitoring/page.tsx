"use client";

import * as React from "react";
import { Activity, AlertTriangle, Clock, Cpu, Gauge, HardDrive, Zap } from "lucide-react";
import { Meter } from "@/components/common/charts";
import { ErrorState, PageHeader } from "@/components/common/page";
import { StatCard } from "@/components/common/stat-card";
import { StatusBadge } from "@/components/common/status-badge";
import { MetricChart } from "@/components/monitoring/metric-chart";
import { useLiveMetrics, type Transport } from "@/components/monitoring/use-live-metrics";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn, formatNumber } from "@/lib/utils";

const C1 = "hsl(var(--chart-1))";
const C2 = "hsl(var(--chart-2))";
const C3 = "hsl(var(--chart-3))";
const C4 = "hsl(var(--chart-4))";
const C5 = "hsl(var(--chart-5))";

const TRANSPORT_LABEL: Record<Transport, string> = {
  "mock-stream": "Live · simulated stream",
  websocket: "Live · WebSocket",
  polling: "Live · polling every 5s",
  paused: "Paused",
};

export default function MonitoringPage() {
  const [window, setWindow] = React.useState("1h");
  const [live, setLive] = React.useState(true);
  const { overview, points, transport } = useLiveMetrics(window, live);
  const last = points[points.length - 1];
  const loading = overview.isLoading;

  return (
    <div className="page-container animate-fade-in">
      <PageHeader
        title="Monitoring"
        description="Real-time fleet metrics: traffic, latency, GPU and error rates."
        actions={
          <>
            <div className="flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm">
              <span className={cn("h-2 w-2 rounded-full", live ? "bg-success animate-pulse-dot" : "bg-muted-foreground")} aria-hidden />
              <label htmlFor="live-toggle" className="cursor-pointer text-xs">
                {TRANSPORT_LABEL[transport]}
              </label>
              <Switch id="live-toggle" checked={live} onCheckedChange={setLive} aria-label="Toggle live updates" />
            </div>
            <Select value={window} onValueChange={setWindow}>
              <SelectTrigger className="w-32" aria-label="Time window">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="15m">Last 15 min</SelectItem>
                <SelectItem value="1h">Last hour</SelectItem>
                <SelectItem value="6h">Last 6 hours</SelectItem>
                <SelectItem value="24h">Last 24 hours</SelectItem>
              </SelectContent>
            </Select>
          </>
        }
      />

      {overview.isError ? (
        <ErrorState error={overview.error} onRetry={() => overview.refetch()} title="Could not load metrics" />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label="Requests / min" icon={Activity} loading={loading} value={last ? formatNumber(last.requests_per_min, 0) : "—"} />
            <StatCard label="Tokens / min" icon={Zap} loading={loading} value={last ? formatNumber(last.tokens_per_min) : "—"} hint={last ? `${formatNumber(last.throughput_tps, 0)} tokens/s throughput` : undefined} />
            <StatCard label="Latency p95" icon={Clock} loading={loading} value={last ? `${last.latency_p95_ms} ms` : "—"} hint={last ? `p50 ${last.latency_p50_ms} ms` : undefined} />
            <StatCard label="Error rate" icon={AlertTriangle} loading={loading} value={last ? `${last.error_rate.toFixed(2)}%` : "—"} hint="5xx and upstream failures" />
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {loading ? (
              [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-72 w-full" />)
            ) : (
              <>
                <MetricChart title="Requests per minute" description="Inference requests handled by the gateway" data={points} window={window} series={[{ key: "requests_per_min", name: "Requests/min", color: C1 }]} format={(v) => formatNumber(v, 0)} />
                <MetricChart title="Tokens per minute" description="Prompt + completion tokens" data={points} window={window} series={[{ key: "tokens_per_min", name: "Tokens/min", color: C2 }]} format={(v) => formatNumber(v)} />
                <MetricChart title="Latency" description="End-to-end request latency percentiles" type="line" data={points} window={window} series={[{ key: "latency_p50_ms", name: "p50", color: C5 }, { key: "latency_p95_ms", name: "p95", color: C3 }]} format={(v) => `${Math.round(v)} ms`} />
                <MetricChart title="Throughput" description="Generated tokens per second" type="line" data={points} window={window} series={[{ key: "throughput_tps", name: "Tokens/s", color: C4 }]} format={(v) => formatNumber(v, 0)} />
                <MetricChart title="GPU utilisation" description="Average across all devices" data={points} window={window} series={[{ key: "gpu_util", name: "GPU %", color: C1 }]} format={(v) => `${Math.round(v)}%`} domain={[0, 100]} />
                <MetricChart title="GPU memory" description="HBM / unified memory in use" type="line" data={points} window={window} series={[{ key: "gpu_memory_util", name: "Memory %", color: C2 }]} format={(v) => `${Math.round(v)}%`} domain={[0, 100]} />
                <div className="lg:col-span-2">
                  <MetricChart title="Error rate" description="Share of requests returning an error" data={points} window={window} series={[{ key: "error_rate", name: "Errors %", color: "hsl(var(--destructive))" }]} format={(v) => `${v.toFixed(1)}%`} />
                </div>
              </>
            )}
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-5">
            <Card className="lg:col-span-3">
              <CardHeader>
                <CardTitle>Deployments</CardTitle>
                <CardDescription>Current per-deployment health</CardDescription>
              </CardHeader>
              <CardContent className="px-0 pb-2">
                {loading ? (
                  <Skeleton className="mx-5 h-40" />
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead>Name</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead className="text-right">Req/min</TableHead>
                        <TableHead className="text-right">p95</TableHead>
                        <TableHead className="text-right">Errors</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {overview.data?.deployments.map((d) => (
                        <TableRow key={d.deployment_id}>
                          <TableCell className="font-medium">{d.name}</TableCell>
                          <TableCell>
                            <StatusBadge status={d.status} />
                          </TableCell>
                          <TableCell className="text-right tabular-nums">{d.status === "running" ? d.requests_per_min : "—"}</TableCell>
                          <TableCell className="text-right tabular-nums">{d.status === "running" ? `${d.p95_latency_ms} ms` : "—"}</TableCell>
                          <TableCell className={cn("text-right tabular-nums", d.error_rate > 0.8 && "text-warning")}>{d.status === "running" ? `${d.error_rate.toFixed(2)}%` : "—"}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>

            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Devices</CardTitle>
                <CardDescription>Per-GPU utilisation and memory</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {loading
                  ? [0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)
                  : overview.data?.gpus.map((g) => (
                      <div key={g.id}>
                        <div className="mb-1 flex items-center justify-between text-xs">
                          <span className="flex items-center gap-1.5 font-medium">
                            <Cpu className="h-3.5 w-3.5 text-muted-foreground" aria-hidden /> {g.id}
                            <span className="font-normal text-muted-foreground">{g.node}</span>
                          </span>
                          <span className="tabular-nums text-muted-foreground">
                            <Gauge className="mr-1 inline h-3 w-3" aria-hidden />
                            {g.util.toFixed(0)}% · <HardDrive className="mx-1 inline h-3 w-3" aria-hidden />
                            {g.memory_used_gb.toFixed(0)}/{g.memory_total_gb} GB
                          </span>
                        </div>
                        <Meter value={g.util} label={`${g.id} utilisation`} />
                      </div>
                    ))}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
