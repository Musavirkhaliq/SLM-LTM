"use client";

import * as React from "react";
import { Trophy } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_PROPS, CHART_COLORS, ChartTooltip, GRID_PROPS, Legend } from "@/components/common/charts";
import { EmptyState, ErrorState } from "@/components/common/page";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useModels } from "@/lib/api/hooks";
import { cn, formatParams } from "@/lib/utils";

export const BENCH_LABELS: Record<string, string> = {
  mmlu: "MMLU",
  gsm8k: "GSM8K",
  humaneval: "HumanEval",
  arc_challenge: "ARC-C",
  truthfulqa: "TruthfulQA",
};
const BENCH_KEYS = Object.keys(BENCH_LABELS);

export function Benchmarks() {
  const models = useModels();
  const rows = React.useMemo(
    () =>
      (models.data ?? [])
        .filter((m) => m.latest_version && Object.keys(m.latest_version.benchmark_scores).length)
        .map((m) => {
          const s = m.latest_version!.benchmark_scores;
          const avg = BENCH_KEYS.reduce((sum, k) => sum + (s[k] ?? 0), 0) / BENCH_KEYS.length;
          return { id: m.id, name: m.name, params: m.latest_version!.param_count, scores: s, avg };
        })
        .sort((a, b) => b.avg - a.avg),
    [models.data],
  );

  const [selected, setSelected] = React.useState<string[] | null>(null);
  const shown = selected ?? rows.slice(0, 4).map((r) => r.id);

  if (models.isLoading) return <Skeleton className="h-96 w-full" />;
  if (models.isError) return <ErrorState error={models.error} onRetry={() => models.refetch()} />;
  if (!rows.length) return <EmptyState icon={Trophy} title="No benchmark data yet" description="Register models with benchmark scores or run an evaluation." />;

  const chartData = BENCH_KEYS.map((k) => {
    const point: Record<string, string | number> = { benchmark: BENCH_LABELS[k]! };
    rows.filter((r) => shown.includes(r.id)).forEach((r) => (point[r.name] = +((r.scores[k] ?? 0) * 100).toFixed(1)));
    return point;
  });
  const shownRows = rows.filter((r) => shown.includes(r.id));
  const best = Object.fromEntries(BENCH_KEYS.map((k) => [k, Math.max(...rows.map((r) => r.scores[k] ?? 0))]));

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle>Benchmark scores</CardTitle>
              <CardDescription>Public benchmark results recorded on each model&apos;s active version (higher is better)</CardDescription>
            </div>
            <Legend items={shownRows.map((r, i) => ({ label: r.name, color: CHART_COLORS[i % CHART_COLORS.length]! }))} />
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-6 lg:grid-cols-[1fr_220px]">
            <div className="h-72" role="img" aria-label="Grouped bar chart of benchmark scores for the selected models">
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                <BarChart data={chartData} margin={{ left: -12, right: 8, top: 4, bottom: 0 }}>
                  <CartesianGrid {...GRID_PROPS} />
                  <XAxis dataKey="benchmark" {...AXIS_PROPS} />
                  <YAxis domain={[0, 100]} {...AXIS_PROPS} tickFormatter={(v: number) => `${v}`} width={36} />
                  <Tooltip cursor={{ fill: "hsl(var(--muted) / 0.5)" }} content={<ChartTooltip valueFormatter={(v) => v.toFixed(1)} />} />
                  {shownRows.map((r, i) => (
                    <Bar key={r.id} dataKey={r.name} fill={CHART_COLORS[i % CHART_COLORS.length]} radius={[3, 3, 0, 0]} maxBarSize={22} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
            <fieldset>
              <legend className="mb-2 text-xs font-medium text-muted-foreground">Models in chart</legend>
              <div className="max-h-64 space-y-1.5 overflow-auto pr-1">
                {rows.map((r) => (
                  <label key={r.id} className="flex cursor-pointer items-center gap-2 text-sm">
                    <Checkbox
                      checked={shown.includes(r.id)}
                      onCheckedChange={(c) => setSelected(c ? [...shown, r.id] : shown.filter((x) => x !== r.id))}
                      aria-label={`Show ${r.name}`}
                    />
                    <span className="truncate">{r.name}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Leaderboard</CardTitle>
          <CardDescription>Ranked by mean score across the five benchmarks</CardDescription>
        </CardHeader>
        <CardContent className="px-0 pb-2">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-10">#</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Size</TableHead>
                {BENCH_KEYS.map((k) => (
                  <TableHead key={k} className="text-right">
                    {BENCH_LABELS[k]}
                  </TableHead>
                ))}
                <TableHead className="text-right">Mean</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r, i) => (
                <TableRow key={r.id}>
                  <TableCell className="text-muted-foreground">{i + 1}</TableCell>
                  <TableCell className="font-medium">
                    {r.name}
                    {i === 0 && (
                      <Badge variant="warning" className="ml-2">
                        <Trophy className="h-3 w-3" aria-hidden /> Top
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatParams(r.params)}</TableCell>
                  {BENCH_KEYS.map((k) => {
                    const v = r.scores[k];
                    return (
                      <TableCell key={k} className={cn("text-right tabular-nums", v === best[k] && "font-semibold text-success")}>
                        {v === undefined ? "—" : (v * 100).toFixed(1)}
                      </TableCell>
                    );
                  })}
                  <TableCell className="text-right font-semibold tabular-nums">{(r.avg * 100).toFixed(1)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
