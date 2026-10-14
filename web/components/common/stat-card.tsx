import * as React from "react";
import { ArrowDownRight, ArrowUpRight, type LucideIcon } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  icon: Icon,
  hint,
  delta,
  deltaGoodWhen = "up",
  loading,
  children,
  className,
}: {
  label: string;
  value?: React.ReactNode;
  icon?: LucideIcon;
  hint?: React.ReactNode;
  /** percentage change vs previous period */
  delta?: number;
  deltaGoodWhen?: "up" | "down";
  loading?: boolean;
  children?: React.ReactNode;
  className?: string;
}) {
  const good = delta === undefined ? true : deltaGoodWhen === "up" ? delta >= 0 : delta <= 0;
  return (
    <Card className={cn("p-5", className)}>
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        {Icon && <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />}
      </div>
      {loading ? (
        <Skeleton className="mt-3 h-8 w-24" />
      ) : (
        <div className="mt-2 flex items-baseline gap-2">
          <p className="text-2xl font-semibold tracking-tight tabular-nums">{value}</p>
          {delta !== undefined && (
            <span className={cn("inline-flex items-center text-xs font-medium", good ? "text-success" : "text-destructive")}>
              {delta >= 0 ? <ArrowUpRight className="h-3.5 w-3.5" aria-hidden /> : <ArrowDownRight className="h-3.5 w-3.5" aria-hidden />}
              {Math.abs(delta).toFixed(1)}%
              <span className="sr-only"> versus previous period</span>
            </span>
          )}
        </div>
      )}
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      {children}
    </Card>
  );
}
