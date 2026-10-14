import type { EvaluationResult } from "@/lib/api/types";

/** Metrics where a lower value is better. */
export const LOWER_IS_BETTER = new Set(["hallucination", "toxicity", "latency_p50_ms", "latency_p95_ms", "cost_per_1k_tokens_usd"]);

export type Winner = "a" | "b" | "tie";

export function pickWinner(metric: string, a: number | null | undefined, b: number | null | undefined, epsilon = 1e-9): Winner {
  if (a == null || b == null) return "tie";
  if (Math.abs(a - b) <= epsilon) return "tie";
  const aBetter = LOWER_IS_BETTER.has(metric) ? a < b : a > b;
  return aBetter ? "a" : "b";
}

/** Relative change from A to B in percent (positive = B larger). */
export function pctDelta(a: number, b: number): number {
  if (a === 0) return b === 0 ? 0 : 100;
  return ((b - a) / Math.abs(a)) * 100;
}

export interface RadarDatum {
  axis: string;
  a: number;
  b: number;
}

/**
 * Normalised (0-100) radar axes. Quality scores are used as-is; "lower is better" quantities are
 * inverted (1 - x), and latency / cost are scored relative to the better of the two models.
 */
export function buildRadar(a: EvaluationResult, b: EvaluationResult): RadarDatum[] {
  const s = (r: EvaluationResult, k: string) => r.scores[k] ?? 0;
  const rel = (x: number | null, y: number | null): [number, number] => {
    if (!x || !y) return [1, 1];
    const best = Math.min(x, y);
    return [best / x, best / y];
  };
  const [speedA, speedB] = rel(a.latency_p95_ms, b.latency_p95_ms);
  const [costA, costB] = rel(a.cost_per_1k_tokens_usd, b.cost_per_1k_tokens_usd);
  const pct = (n: number) => +(n * 100).toFixed(1);
  return [
    { axis: "Accuracy", a: pct(s(a, "accuracy")), b: pct(s(b, "accuracy")) },
    { axis: "Faithfulness", a: pct(s(a, "faithfulness")), b: pct(s(b, "faithfulness")) },
    { axis: "Groundedness", a: pct(s(a, "groundedness")), b: pct(s(b, "groundedness")) },
    { axis: "JSON validity", a: pct(s(a, "json_validity")), b: pct(s(b, "json_validity")) },
    { axis: "Low hallucination", a: pct(1 - s(a, "hallucination")), b: pct(1 - s(b, "hallucination")) },
    { axis: "Safety", a: pct(1 - s(a, "toxicity")), b: pct(1 - s(b, "toxicity")) },
    { axis: "Speed", a: pct(speedA), b: pct(speedB) },
    { axis: "Cost efficiency", a: pct(costA), b: pct(costB) },
  ];
}
