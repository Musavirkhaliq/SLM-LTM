"use client";

import * as React from "react";
import { useAuth } from "@/components/providers/auth-provider";
import { apiClient } from "@/lib/api";
import { useMonitoringOverview } from "@/lib/api/hooks";
import type { MetricPoint } from "@/lib/api/types";
import { API_BASE, USE_MOCKS } from "@/lib/config";

export const WINDOW_POINTS: Record<string, number> = { "15m": 90, "1h": 60, "6h": 72, "24h": 96 };
export type Transport = "mock-stream" | "websocket" | "polling" | "paused";

/**
 * Initial series from `GET /monitoring/overview`, then live updates:
 *  - mock mode: a simulated push stream,
 *  - real mode: `WS /api/v1/monitoring/stream`, falling back to polling the overview if the socket fails.
 */
export function useLiveMetrics(window: string, live: boolean) {
  const { orgId } = useAuth();
  const overview = useMonitoringOverview(window);
  const [points, setPoints] = React.useState<MetricPoint[]>([]);
  const [wsFailed, setWsFailed] = React.useState(false);
  const limit = WINDOW_POINTS[window] ?? 60;
  const { refetch } = overview;

  React.useEffect(() => {
    if (overview.data) setPoints(overview.data.points);
  }, [overview.data]);

  const push = React.useCallback((p: MetricPoint) => setPoints((prev) => [...prev.slice(-(limit - 1)), p]), [limit]);

  React.useEffect(() => {
    if (!live || !overview.data) return;
    if (USE_MOCKS) {
      let stop: (() => void) | undefined;
      let cancelled = false;
      import("@/lib/mocks/live").then((m) => {
        if (!cancelled) stop = m.subscribeLiveMetrics(orgId, push);
      });
      return () => {
        cancelled = true;
        stop?.();
      };
    }
    if (!wsFailed && typeof WebSocket !== "undefined") {
      const token = apiClient.tokens.getAccessToken();
      const url = `${API_BASE.replace(/^http/, "ws")}/api/v1/monitoring/stream${token ? `?token=${encodeURIComponent(token)}` : ""}`;
      let ws: WebSocket | null = null;
      try {
        ws = new WebSocket(url);
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(String(ev.data)) as MetricPoint | { point: MetricPoint };
            push("point" in msg ? msg.point : msg);
          } catch {
            /* ignore malformed frame */
          }
        };
        ws.onerror = () => setWsFailed(true);
      } catch {
        setWsFailed(true);
      }
      return () => ws?.close();
    }
    const id = setInterval(() => void refetch(), 5000);
    return () => clearInterval(id);
  }, [live, overview.data, orgId, push, wsFailed, refetch]);

  const transport: Transport = !live ? "paused" : USE_MOCKS ? "mock-stream" : wsFailed ? "polling" : "websocket";
  return { overview, points, transport };
}
