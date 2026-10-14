"use client";

import * as React from "react";
import { Line, LineChart, ResponsiveContainer } from "recharts";
import { cn } from "@/lib/utils";

/** Chart palette resolved from CSS variables so it adapts to the active theme. */
export const CHART_COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
];

export const AXIS_PROPS = {
  stroke: "hsl(var(--muted-foreground))",
  fontSize: 11,
  tickLine: false,
  axisLine: false,
} as const;

export const GRID_PROPS = { stroke: "hsl(var(--border))", strokeDasharray: "3 3", vertical: false } as const;

export function ChartTooltip({
  active,
  payload,
  label,
  labelFormatter,
  valueFormatter,
}: {
  active?: boolean;
  payload?: { name?: string | number; value?: number | string; color?: string; dataKey?: string | number }[];
  label?: string | number;
  labelFormatter?: (l: string | number) => string;
  valueFormatter?: (v: number, name: string) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-lg">
      {label !== undefined && <p className="mb-1 font-medium">{labelFormatter ? labelFormatter(label) : String(label)}</p>}
      <ul className="space-y-0.5">
        {payload.map((p) => (
          <li key={String(p.dataKey ?? p.name)} className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full" style={{ background: p.color }} aria-hidden />
            <span className="text-muted-foreground">{p.name}</span>
            <span className="ml-auto pl-3 font-medium tabular-nums">
              {typeof p.value === "number" && valueFormatter ? valueFormatter(p.value, String(p.name)) : p.value}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Sparkline({
  data,
  color = CHART_COLORS[0],
  height = 28,
  className,
  label,
}: {
  data: number[];
  color?: string;
  height?: number;
  className?: string;
  label?: string;
}) {
  const points = React.useMemo(() => data.map((v, i) => ({ i, v })), [data]);
  return (
    <div className={cn("w-full", className)} style={{ height }} role="img" aria-label={label ?? "Trend sparkline"}>
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <LineChart data={points} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.75} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {items.map((it) => (
        <li key={it.label} className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full" style={{ background: it.color }} aria-hidden />
          {it.label}
        </li>
      ))}
    </ul>
  );
}

/** Radial/linear utilisation meter with colour thresholds. */
export function Meter({ value, className, label }: { value: number; className?: string; label?: string }) {
  const tone = value >= 90 ? "bg-destructive" : value >= 75 ? "bg-warning" : "bg-success";
  return (
    <div
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-secondary", className)}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value)}
      aria-label={label ?? "Utilisation"}
    >
      <div className={cn("h-full rounded-full transition-all", tone)} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  );
}
