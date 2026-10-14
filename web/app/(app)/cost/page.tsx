"use client";

import * as React from "react";
import { CalendarRange, Download, TrendingUp, Wallet } from "lucide-react";
import { toast } from "sonner";
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_PROPS, CHART_COLORS, ChartTooltip, GRID_PROPS } from "@/components/common/charts";
import { ErrorState, Field, PageHeader, RestrictedState } from "@/components/common/page";
import { StatCard } from "@/components/common/stat-card";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { useCostSummary } from "@/lib/api/hooks";
import type { CostGroupBy } from "@/lib/api/types";
import { cn, formatUsd, titleCase } from "@/lib/utils";

const day = (d: Date) => d.toISOString().slice(0, 10);
const daysAgo = (n: number) => day(new Date(Date.now() - n * 86_400_000));

const PRESETS: Record<string, { label: string; from: () => string }> = {
  "7": { label: "Last 7 days", from: () => daysAgo(6) },
  "30": { label: "Last 30 days", from: () => daysAgo(29) },
  "90": { label: "Last 90 days", from: () => daysAgo(89) },
  mtd: { label: "Month to date", from: () => day(new Date(Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), 1))) },
  custom: { label: "Custom range", from: () => daysAgo(29) },
};

const GROUPS: { id: CostGroupBy; label: string }[] = [
  { id: "project", label: "Project" },
  { id: "deployment", label: "Deployment" },
  { id: "model", label: "Model" },
  { id: "org", label: "Organization" },
];

export default function CostPage() {
  const { can } = useAuth();
  const allowed = can("org_admin");
  const [group, setGroup] = React.useState<CostGroupBy>("project");
  const [preset, setPreset] = React.useState("30");
  const [from, setFrom] = React.useState(PRESETS["30"]!.from());
  const [to, setTo] = React.useState(day(new Date()));
  const [downloading, setDownloading] = React.useState(false);

  const rangeError = from > to ? "Start date must be before the end date." : (Date.parse(to) - Date.parse(from)) / 86_400_000 > 365 ? "Range cannot exceed 365 days." : null;
  const cost = useCostSummary(group, from, to, allowed && !rangeError);

  function choosePreset(p: string) {
    setPreset(p);
    if (p !== "custom") {
      setFrom(PRESETS[p]!.from());
      setTo(day(new Date()));
    }
  }

  async function download() {
    setDownloading(true);
    try {
      const blob = await api.cost.reportCsv(group, from, to);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `slm-studio-cost-${group}-${from}_${to}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast.success("CSV report downloaded");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not download the report");
    } finally {
      setDownloading(false);
    }
  }

  if (!allowed) {
    return (
      <div className="page-container">
        <PageHeader title="Cost" description="Spend by project, deployment, model and organization." />
        <RestrictedState title="Cost reports require the Org Admin role" description="Ask an organization admin to share a report, or to grant you access." />
      </div>
    );
  }

  const data = cost.data;
  const delta = data && data.previous_total_usd ? ((data.total_usd - data.previous_total_usd) / data.previous_total_usd) * 100 : undefined;
  const days = data?.daily.length ?? 0;
  const top = data?.items.slice().sort((a, b) => b.usd - a.usd)[0];
  const sortedItems = data?.items.slice().sort((a, b) => b.usd - a.usd) ?? [];

  return (
    <div className="page-container animate-fade-in">
      <PageHeader
        title="Cost"
        description="Understand where your GPU and token spend goes."
        actions={
          <Button variant="outline" onClick={download} loading={downloading} disabled={!!rangeError}>
            <Download /> Download CSV
          </Button>
        }
      />

      <Card className="mb-4 p-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <Tabs value={group} onValueChange={(v) => setGroup(v as CostGroupBy)}>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">Group by</p>
            <TabsList aria-label="Group by">
              {GROUPS.map((g) => (
                <TabsTrigger key={g.id} value={g.id}>
                  {g.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Range" htmlFor="cost-range">
              <Select value={preset} onValueChange={choosePreset}>
                <SelectTrigger id="cost-range" className="w-44">
                  <CalendarRange className="mr-1 h-4 w-4 text-muted-foreground" aria-hidden />
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(PRESETS).map(([k, v]) => (
                    <SelectItem key={k} value={k}>
                      {v.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="From" htmlFor="cost-from">
              <Input id="cost-from" type="date" value={from} max={to} onChange={(e) => { setFrom(e.target.value); setPreset("custom"); }} className="w-40" />
            </Field>
            <Field label="To" htmlFor="cost-to" error={rangeError}>
              <Input id="cost-to" type="date" value={to} min={from} max={day(new Date())} onChange={(e) => { setTo(e.target.value); setPreset("custom"); }} className="w-40" />
            </Field>
          </div>
        </div>
      </Card>

      {cost.isError ? (
        <ErrorState error={cost.error} onRetry={() => cost.refetch()} title="Could not load cost data" />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label="Total spend" icon={Wallet} loading={cost.isLoading} value={data ? formatUsd(data.total_usd) : "—"} delta={delta} deltaGoodWhen="down" hint="vs. previous period" />
            <StatCard label="Average per day" icon={TrendingUp} loading={cost.isLoading} value={data && days ? formatUsd(data.total_usd / days) : "—"} hint={`${days} days`} />
            <StatCard label={`Top ${group}`} loading={cost.isLoading} value={top?.label ?? "—"} hint={top && data ? `${formatUsd(top.usd)} · ${((top.usd / data.total_usd) * 100).toFixed(0)}% of spend` : undefined} className="[&_p.text-2xl]:truncate [&_p.text-2xl]:text-xl" />
            <StatCard label="Previous period" loading={cost.isLoading} value={data ? formatUsd(data.previous_total_usd) : "—"} hint="Same length, immediately before" />
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Daily spend</CardTitle>
                <CardDescription>
                  {from} → {to}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {cost.isLoading ? (
                  <Skeleton className="h-64 w-full" />
                ) : (
                  <div className="h-64" role="img" aria-label="Bar chart of daily spend">
                    <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                      <BarChart data={data?.daily} margin={{ left: -4, right: 8, top: 4, bottom: 0 }}>
                        <CartesianGrid {...GRID_PROPS} />
                        <XAxis dataKey="day" {...AXIS_PROPS} tickFormatter={(d: string) => d.slice(5)} minTickGap={28} />
                        <YAxis {...AXIS_PROPS} tickFormatter={(v: number) => `$${v}`} width={52} />
                        <Tooltip cursor={{ fill: "hsl(var(--muted) / 0.5)" }} content={<ChartTooltip valueFormatter={(v) => formatUsd(v)} />} />
                        <Bar dataKey="usd" name="Spend" fill="hsl(var(--chart-1))" radius={[3, 3, 0, 0]} maxBarSize={20} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>By resource</CardTitle>
                <CardDescription>What the spend is made of</CardDescription>
              </CardHeader>
              <CardContent>
                {cost.isLoading ? (
                  <Skeleton className="h-64 w-full" />
                ) : (
                  <>
                    <div className="h-40" role="img" aria-label="Donut chart of spend by resource">
                      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                        <PieChart>
                          <Pie data={data?.by_resource} dataKey="usd" nameKey="resource" innerRadius={42} outerRadius={64} paddingAngle={2} stroke="hsl(var(--card))" isAnimationActive={false}>
                            {data?.by_resource.map((_, i) => (
                              <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                            ))}
                          </Pie>
                          <Tooltip content={<ChartTooltip valueFormatter={(v) => formatUsd(v)} />} />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>
                    <ul className="mt-2 space-y-1.5 text-xs">
                      {data?.by_resource.map((r, i) => (
                        <li key={r.resource} className="flex items-center gap-2">
                          <span className="h-2 w-2 rounded-full" style={{ background: CHART_COLORS[i % CHART_COLORS.length] }} aria-hidden />
                          <span className="text-muted-foreground">{titleCase(r.resource)}</span>
                          <span className="ml-auto tabular-nums font-medium">{formatUsd(r.usd, { digits: 0 })}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </CardContent>
            </Card>
          </div>

          <Card className="mt-4">
            <CardHeader>
              <CardTitle>Breakdown by {group}</CardTitle>
              <CardDescription>Sorted by spend</CardDescription>
            </CardHeader>
            <CardContent className="px-0 pb-2">
              {cost.isLoading ? (
                <Skeleton className="mx-5 h-40" />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>{titleCase(group)}</TableHead>
                      <TableHead className="w-[40%]">Share</TableHead>
                      <TableHead className="text-right">Spend</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sortedItems.map((it, i) => {
                      const share = data ? (it.usd / data.total_usd) * 100 : 0;
                      return (
                        <TableRow key={it.key}>
                          <TableCell className="font-medium">{it.label}</TableCell>
                          <TableCell>
                            <div className="flex items-center gap-3">
                              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary">
                                <div className={cn("h-full rounded-full")} style={{ width: `${share}%`, background: CHART_COLORS[i % CHART_COLORS.length] }} />
                              </div>
                              <span className="w-10 text-right text-xs tabular-nums text-muted-foreground">{share.toFixed(0)}%</span>
                            </div>
                          </TableCell>
                          <TableCell className="text-right font-medium tabular-nums">{formatUsd(it.usd)}</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
