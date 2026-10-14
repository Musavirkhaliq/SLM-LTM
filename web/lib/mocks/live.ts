import type { MetricPoint } from "@/lib/api/types";
import { ORGS } from "./db";
import { subscribeMockMetrics } from "./metrics";

/** Simulated `WS /api/v1/monitoring/stream` for the active org (mock mode only). */
export function subscribeLiveMetrics(orgId: string | null, cb: (p: MetricPoint) => void, intervalMs = 2000): () => void {
  const load = ORGS.find((o) => o.id === orgId)?.load ?? 1;
  return subscribeMockMetrics(cb, intervalMs, load);
}
