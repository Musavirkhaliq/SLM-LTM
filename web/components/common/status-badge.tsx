import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn, titleCase } from "@/lib/utils";

type Variant = NonNullable<BadgeProps["variant"]>;

const STATUS_VARIANT: Record<string, { variant: Variant; pulse?: boolean }> = {
  running: { variant: "success", pulse: true },
  succeeded: { variant: "success" },
  ready: { variant: "success" },
  active: { variant: "success" },
  provisioning: { variant: "info", pulse: true },
  rolling_back: { variant: "info", pulse: true },
  creating: { variant: "info", pulse: true },
  pending: { variant: "warning" },
  queued: { variant: "warning" },
  registered: { variant: "info" },
  stopped: { variant: "secondary" },
  archived: { variant: "secondary" },
  cancelled: { variant: "secondary" },
  revoked: { variant: "destructive" },
  failed: { variant: "destructive" },
  expired: { variant: "destructive" },
  rotating: { variant: "warning" },
};

export function StatusBadge({ status, className, label }: { status: string; className?: string; label?: string }) {
  const cfg = STATUS_VARIANT[status] ?? { variant: "secondary" as Variant };
  return (
    <Badge variant={cfg.variant} className={cn("gap-1.5", className)}>
      <span
        aria-hidden
        className={cn("h-1.5 w-1.5 rounded-full bg-current", cfg.pulse && "animate-pulse-dot")}
      />
      {label ?? titleCase(status)}
    </Badge>
  );
}
