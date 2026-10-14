import type { GpuStat, MetricPoint } from "@/lib/api/types";
import { hash01 } from "./rand";

/** Window label -> [point count, step in ms] */
export const WINDOWS: Record<string, [number, number]> = {
  "15m": [90, 10_000],
  "1h": [60, 60_000],
  "6h": [72, 300_000],
  "24h": [96, 900_000],
};

/**
 * Deterministic synthetic fleet metrics as a pure function of time, so historical series and
 * the "live" stream line up. `load` scales traffic (used for per-org variation).
 */
export function metricAt(t: number, load = 1): MetricPoint {
  const slow = Math.sin(t / 1_800_000) * 0.25 + Math.sin(t / 420_000) * 0.15;
  const noise = (k: number) => (hash01(Math.floor(t / 1000), k) - 0.5) * 2;
  const base = (1 + slow) * load;
  const requests = Math.max(5, 620 * base + noise(1) * 45);
  const tokens = requests * (410 + noise(2) * 40);
  const gpu = Math.min(98, Math.max(12, 54 + slow * 70 + noise(3) * 6));
  const p50 = 118 + slow * 40 + noise(4) * 10 + (gpu > 80 ? (gpu - 80) * 3 : 0);
  const p95 = p50 * (2.35 + noise(5) * 0.18);
  const err = Math.max(0.02, 0.35 + Math.max(0, gpu - 85) * 0.12 + noise(6) * 0.12);
  return {
    t,
    requests_per_min: Math.round(requests),
    tokens_per_min: Math.round(tokens),
    latency_p50_ms: Math.round(p50),
    latency_p95_ms: Math.round(p95),
    gpu_util: +gpu.toFixed(1),
    gpu_memory_util: +Math.min(96, 61 + slow * 25 + noise(7) * 2.5).toFixed(1),
    throughput_tps: Math.round(tokens / 60),
    error_rate: +err.toFixed(2),
  };
}

export function seriesFor(window: string, now = Date.now(), load = 1): MetricPoint[] {
  const [count, step] = WINDOWS[window] ?? WINDOWS["1h"]!;
  const end = Math.floor(now / step) * step;
  const out: MetricPoint[] = [];
  for (let i = count - 1; i >= 0; i--) out.push(metricAt(end - i * step, load));
  return out;
}

export function gpuStats(now = Date.now()): GpuStat[] {
  const defs = [
    { id: "gpu-0", name: "NVIDIA GB10 (DGX Spark)", node: "dgx-spark-01", total: 128 },
    { id: "gpu-1", name: "NVIDIA A100 80GB", node: "k8s-gpu-a", total: 80 },
    { id: "gpu-2", name: "NVIDIA A100 80GB", node: "k8s-gpu-a", total: 80 },
    { id: "gpu-3", name: "NVIDIA L40S 48GB", node: "k8s-gpu-b", total: 48 },
    { id: "gpu-4", name: "NVIDIA L40S 48GB", node: "k8s-gpu-b", total: 48 },
  ];
  return defs.map((d, i) => {
    const m = metricAt(now + i * 90_000);
    const util = Math.max(4, Math.min(99, m.gpu_util + (i - 2) * 9 + (hash01(Math.floor(now / 5000), 20 + i) - 0.5) * 6));
    const memFrac = Math.min(0.97, Math.max(0.2, (m.gpu_memory_util + (i - 2) * 6) / 100));
    return {
      id: d.id,
      name: d.name,
      node: d.node,
      util: +util.toFixed(1),
      memory_total_gb: d.total,
      memory_used_gb: +(d.total * memFrac).toFixed(1),
      temperature_c: Math.round(48 + util * 0.38),
    };
  });
}

/** Subscribe to simulated live metrics (stand-in for WS /api/v1/monitoring/stream). */
export function subscribeMockMetrics(cb: (p: MetricPoint) => void, intervalMs = 2000, load = 1): () => void {
  const id = setInterval(() => cb(metricAt(Date.now(), load)), intervalMs);
  return () => clearInterval(id);
}
