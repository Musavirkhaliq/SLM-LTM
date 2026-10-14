"use client";

import * as React from "react";
import { GitCompareArrows } from "lucide-react";
import { Bar, BarChart, CartesianGrid, PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_PROPS, ChartTooltip, GRID_PROPS, Legend } from "@/components/common/charts";
import { EmptyState, ErrorState, Field } from "@/components/common/page";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useEvaluationCompare, useEvaluationResultsMany, useEvaluations } from "@/lib/api/hooks";
import type { EvaluationResult } from "@/lib/api/types";
import { buildRadar, LOWER_IS_BETTER, pctDelta, pickWinner } from "@/lib/compare";
import { cn, formatUsd, titleCase } from "@/lib/utils";

const COLOR_A = "hsl(var(--chart-1))";
const COLOR_B = "hsl(var(--chart-3))";

const QUALITY_METRICS = ["accuracy", "f1", "rouge_l", "faithfulness", "groundedness", "json_validity"];
const SAFETY_METRICS = ["hallucination", "toxicity"];

const label = (r: EvaluationResult) => (r.model_name ? `${r.model_name} v${r.model_version ?? "?"}` : `version ${r.model_version_id.slice(0, 8)}`);
const keyOf = (r: EvaluationResult) => `${r.evaluation_id}::${r.model_version_id}`;

function formatMetric(metric: string, v: number | null | undefined): string {
  if (v == null) return "—";
  if (metric === "cost_per_1k_tokens_usd") return formatUsd(v, { digits: 5 });
  if (metric.startsWith("latency")) return `${Math.round(v)} ms`;
  if (metric === "sample_count") return v.toLocaleString();
  return `${(v * 100).toFixed(1)}%`;
}

export function Compare() {
  const evaluations = useEvaluations();
  const done = (evaluations.data ?? []).filter((e) => e.status === "succeeded");
  const resultQueries = useEvaluationResultsMany(done.map((e) => e.id));
  const loadingResults = resultQueries.some((q) => q.isLoading);

  const options = React.useMemo(() => {
    const out: { key: string; text: string; evaluationId: string; versionId: string }[] = [];
    done.forEach((e, i) => {
      (resultQueries[i]?.data ?? []).forEach((r) => out.push({ key: keyOf(r), text: `${label(r)} — ${e.name}`, evaluationId: e.id, versionId: r.model_version_id }));
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evaluations.data, resultQueries.map((q) => q.dataUpdatedAt).join(",")]);

  const [aKey, setAKey] = React.useState<string>("");
  const [bKey, setBKey] = React.useState<string>("");
  React.useEffect(() => {
    if (options.length >= 2 && (!aKey || !bKey)) {
      setAKey(options[0]!.key);
      setBKey((options.find((o) => o.evaluationId === options[0]!.evaluationId && o.key !== options[0]!.key) ?? options[1]!).key);
    }
  }, [options, aKey, bKey]);

  const optA = options.find((o) => o.key === aKey);
  const optB = options.find((o) => o.key === bKey);
  const evalIds = [...new Set([optA?.evaluationId, optB?.evaluationId].filter(Boolean) as string[])];
  const compare = useEvaluationCompare(evalIds);
  const a = compare.data?.find((r) => optA && keyOf(r) === optA.key);
  const b = compare.data?.find((r) => optB && keyOf(r) === optB.key);

  if (evaluations.isLoading || loadingResults) return <Skeleton className="h-96 w-full" />;
  if (evaluations.isError) return <ErrorState error={evaluations.error} onRetry={() => evaluations.refetch()} />;
  if (options.length < 2)
    return <EmptyState icon={GitCompareArrows} title="Nothing to compare yet" description="Run an evaluation with two or more models, or run two separate evaluations, then compare them here." />;

  const nameA = a ? label(a) : "Model A";
  const nameB = b ? label(b) : "Model B";

  const barData = (metrics: string[]) =>
    metrics.map((m) => ({ metric: titleCase(m), A: +((a?.scores[m] ?? 0) * 100).toFixed(1), B: +((b?.scores[m] ?? 0) * 100).toFixed(1) }));

  const tableRows: { metric: string; name: string; a: number | null | undefined; b: number | null | undefined }[] = a && b
    ? [
        ...[...QUALITY_METRICS, ...SAFETY_METRICS].map((m) => ({ metric: m, name: titleCase(m), a: a.scores[m], b: b.scores[m] })),
        { metric: "latency_p50_ms", name: "Latency p50", a: a.latency_p50_ms, b: b.latency_p50_ms },
        { metric: "latency_p95_ms", name: "Latency p95", a: a.latency_p95_ms, b: b.latency_p95_ms },
        { metric: "cost_per_1k_tokens_usd", name: "Cost / 1K tokens", a: a.cost_per_1k_tokens_usd, b: b.cost_per_1k_tokens_usd },
      ]
    : [];
  const wins = { a: 0, b: 0 };
  tableRows.forEach((r) => {
    const w = pickWinner(r.metric, r.a, r.b, 0.0005);
    if (w !== "tie") wins[w]++;
  });

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Model A" htmlFor="cmp-a">
            <Select value={aKey} onValueChange={setAKey}>
              <SelectTrigger id="cmp-a" className="border-l-4" style={{ borderLeftColor: COLOR_A }}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options.map((o) => (
                  <SelectItem key={o.key} value={o.key} disabled={o.key === bKey}>
                    {o.text}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Model B" htmlFor="cmp-b">
            <Select value={bKey} onValueChange={setBKey}>
              <SelectTrigger id="cmp-b" className="border-l-4" style={{ borderLeftColor: COLOR_B }}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {options.map((o) => (
                  <SelectItem key={o.key} value={o.key} disabled={o.key === aKey}>
                    {o.text}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>
      </Card>

      {compare.isLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : compare.isError ? (
        <ErrorState error={compare.error} onRetry={() => compare.refetch()} />
      ) : !a || !b ? (
        <EmptyState title="Results unavailable" description="The selected evaluation has no results for one of the models." />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            {[
              { title: "Accuracy", av: a.scores.accuracy, bv: b.scores.accuracy, metric: "accuracy" },
              { title: "Cost / 1K tokens", av: a.cost_per_1k_tokens_usd, bv: b.cost_per_1k_tokens_usd, metric: "cost_per_1k_tokens_usd" },
              { title: "Latency p95", av: a.latency_p95_ms, bv: b.latency_p95_ms, metric: "latency_p95_ms" },
            ].map((c) => {
              const w = pickWinner(c.metric, c.av, c.bv);
              return (
                <Card key={c.title} className="p-5">
                  <p className="text-xs font-medium text-muted-foreground">{c.title}</p>
                  <div className="mt-2 flex items-end justify-between gap-2">
                    <div>
                      <p className={cn("text-lg font-semibold tabular-nums", w === "a" && "text-success")}>{formatMetric(c.metric, c.av)}</p>
                      <p className="text-[11px] text-muted-foreground">A</p>
                    </div>
                    <div className="text-right">
                      <p className={cn("text-lg font-semibold tabular-nums", w === "b" && "text-success")}>{formatMetric(c.metric, c.bv)}</p>
                      <p className="text-[11px] text-muted-foreground">B</p>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="space-y-1">
                    <CardTitle>Quality metrics</CardTitle>
                    <CardDescription>Higher is better</CardDescription>
                  </div>
                  <Legend items={[{ label: nameA, color: COLOR_A }, { label: nameB, color: COLOR_B }]} />
                </div>
              </CardHeader>
              <CardContent>
                <div className="h-72" role="img" aria-label={`Bar chart comparing ${nameA} and ${nameB} on quality metrics`}>
                  <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                    <BarChart data={barData(QUALITY_METRICS)} margin={{ left: -12, right: 8, top: 4, bottom: 0 }}>
                      <CartesianGrid {...GRID_PROPS} />
                      <XAxis dataKey="metric" {...AXIS_PROPS} interval={0} fontSize={10} />
                      <YAxis domain={[0, 100]} {...AXIS_PROPS} width={36} />
                      <Tooltip cursor={{ fill: "hsl(var(--muted) / 0.5)" }} content={<ChartTooltip valueFormatter={(v) => `${v.toFixed(1)}%`} />} />
                      <Bar dataKey="A" name={nameA} fill={COLOR_A} radius={[3, 3, 0, 0]} maxBarSize={24} />
                      <Bar dataKey="B" name={nameB} fill={COLOR_B} radius={[3, 3, 0, 0]} maxBarSize={24} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <div className="space-y-1">
                  <CardTitle>Overall profile</CardTitle>
                  <CardDescription>Quality, safety, speed and cost normalised to 0–100 (outer is better)</CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <div className="h-72" role="img" aria-label={`Radar chart comparing ${nameA} and ${nameB} across accuracy, faithfulness, safety, speed and cost`}>
                  <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                    <RadarChart data={buildRadar(a, b)} outerRadius="72%">
                      <PolarGrid stroke="hsl(var(--border))" />
                      <PolarAngleAxis dataKey="axis" tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }} />
                      <PolarRadiusAxis angle={90} domain={[0, 100]} tick={false} axisLine={false} />
                      <Radar name={nameA} dataKey="a" stroke={COLOR_A} fill={COLOR_A} fillOpacity={0.22} strokeWidth={2} />
                      <Radar name={nameB} dataKey="b" stroke={COLOR_B} fill={COLOR_B} fillOpacity={0.22} strokeWidth={2} />
                      <Tooltip content={<ChartTooltip valueFormatter={(v) => v.toFixed(1)} />} />
                    </RadarChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="space-y-1">
                  <CardTitle>Metrics table</CardTitle>
                  <CardDescription>Winner is highlighted per metric (lower is better for hallucination, toxicity, latency and cost)</CardDescription>
                </div>
                <div className="flex gap-2 text-xs">
                  <Badge variant="default">A wins {wins.a}</Badge>
                  <Badge variant="warning">B wins {wins.b}</Badge>
                </div>
              </div>
            </CardHeader>
            <CardContent className="px-0 pb-2">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Metric</TableHead>
                    <TableHead className="text-right">{nameA}</TableHead>
                    <TableHead className="text-right">{nameB}</TableHead>
                    <TableHead className="text-right">Δ (B vs A)</TableHead>
                    <TableHead>Better</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tableRows.map((r) => {
                    const w = pickWinner(r.metric, r.a, r.b, 0.0005);
                    const delta = r.a != null && r.b != null ? pctDelta(r.a, r.b) : null;
                    return (
                      <TableRow key={r.metric}>
                        <TableCell className="font-medium">
                          {r.name}
                          {LOWER_IS_BETTER.has(r.metric) && <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">↓ lower is better</span>}
                        </TableCell>
                        <TableCell className={cn("text-right tabular-nums", w === "a" && "font-semibold text-success")}>{formatMetric(r.metric, r.a)}</TableCell>
                        <TableCell className={cn("text-right tabular-nums", w === "b" && "font-semibold text-success")}>{formatMetric(r.metric, r.b)}</TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">{delta === null ? "—" : `${delta > 0 ? "+" : ""}${delta.toFixed(1)}%`}</TableCell>
                        <TableCell>{w === "tie" ? <span className="text-muted-foreground">Tie</span> : <Badge variant={w === "a" ? "default" : "warning"}>{w === "a" ? "A" : "B"}</Badge>}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
