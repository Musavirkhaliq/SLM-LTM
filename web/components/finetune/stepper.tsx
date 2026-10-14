import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export const STEPS = ["Dataset", "Configure", "Base model", "Launch", "Monitor"] as const;

export function Stepper({
  current,
  maxReached,
  onSelect,
}: {
  current: number;
  maxReached: number;
  onSelect: (step: number) => void;
}) {
  return (
    <nav aria-label="Fine-tuning steps">
      <ol className="flex items-center gap-2 overflow-x-auto pb-1">
        {STEPS.map((label, i) => {
          const done = i < current;
          const active = i === current;
          const reachable = i <= maxReached || i === STEPS.length - 1; // monitoring is always available
          return (
            <li key={label} className="flex min-w-0 flex-1 items-center gap-2 last:flex-none">
              <button
                type="button"
                disabled={!reachable}
                onClick={() => onSelect(i)}
                aria-current={active ? "step" : undefined}
                className={cn(
                  "flex shrink-0 items-center gap-2 rounded-full py-1 pl-1 pr-3 text-sm font-medium transition-colors disabled:cursor-not-allowed",
                  active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground disabled:hover:text-muted-foreground",
                )}
              >
                <span
                  className={cn(
                    "flex h-6 w-6 items-center justify-center rounded-full border text-xs",
                    done && "border-primary bg-primary text-primary-foreground",
                    active && "border-primary text-primary",
                  )}
                >
                  {done ? <Check className="h-3.5 w-3.5" aria-hidden /> : i + 1}
                </span>
                <span className="whitespace-nowrap">{label}</span>
                {done && <span className="sr-only">(completed)</span>}
              </button>
              {i < STEPS.length - 1 && <span className={cn("h-px min-w-4 flex-1", done ? "bg-primary" : "bg-border")} aria-hidden />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
