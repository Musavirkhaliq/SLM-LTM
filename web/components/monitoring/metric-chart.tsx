"use client";

import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_PROPS, ChartTooltip, GRID_PROPS, Legend } from "@/components/common/charts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { MetricPoint } from "@/lib/api/types";

export interface SeriesDef {
  key: keyof MetricPoint;
  name: string;
  color: string;
}

export function MetricChart({
  title,
  description,
  data,
  series,
  type = "area",
  format,
  domain,
  window,
}: {
  title: string;
  description: string;
  data: MetricPoint[];
  series: SeriesDef[];
  type?: "area" | "line";
  format: (v: number) => string;
  domain?: [number | string, number | string];
  window: string;
}) {
  const long = window === "6h" || window === "24h";
  const tick = (t: number) => new Date(t).toLocaleTimeString([], long ? { hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit", second: window === "15m" ? "2-digit" : undefined });
  const Chart = type === "area" ? AreaChart : LineChart;
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="space-y-1">
            <CardTitle>{title}</CardTitle>
            <CardDescription>{description}</CardDescription>
          </div>
          {series.length > 1 && <Legend items={series.map((s) => ({ label: s.name, color: s.color }))} />}
        </div>
      </CardHeader>
      <CardContent>
        <div className="h-52" role="img" aria-label={`${title} over time`}>
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <Chart data={data} margin={{ left: -4, right: 8, top: 4, bottom: 0 }}>
              <defs>
                {series.map((s) => (
                  <linearGradient key={s.key} id={`g-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={s.color} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={s.color} stopOpacity={0} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid {...GRID_PROPS} />
              <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} {...AXIS_PROPS} tickFormatter={tick} minTickGap={48} />
              <YAxis {...AXIS_PROPS} domain={domain ?? [0, "auto"]} tickFormatter={format} width={52} />
              <Tooltip content={<ChartTooltip labelFormatter={(l) => new Date(Number(l)).toLocaleTimeString()} valueFormatter={(v) => format(v)} />} />
              {series.map((s) =>
                type === "area" ? (
                  <Area key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2} fill={`url(#g-${s.key})`} isAnimationActive={false} dot={false} />
                ) : (
                  <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={2} isAnimationActive={false} dot={false} />
                ),
              )}
            </Chart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
