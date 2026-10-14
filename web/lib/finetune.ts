import type { Hyperparams, TuneMethod } from "@/lib/api/types";

export interface TrainingEstimate {
  totalTokens: number;
  tokensPerSecond: number;
  durationSeconds: number;
  gpuHours: number;
  costUsd: number;
}

/** Indicative on-demand price per GPU-hour used for the pre-launch estimate. */
export const GPU_HOUR_USD = 2.9;

/**
 * Rough pre-flight estimate (no backend call): throughput scales inversely with model size,
 * QLoRA pays ~30% for de-quantisation, and multi-GPU scaling is sub-linear (efficiency 0.9^n).
 */
export function estimateTraining(input: {
  datasetTokens: number;
  paramsBillions: number;
  method: TuneMethod;
  epochs: number;
  gpuCount: number;
  hourlyUsd?: number;
}): TrainingEstimate {
  const { datasetTokens, paramsBillions, method, epochs, gpuCount, hourlyUsd = GPU_HOUR_USD } = input;
  const methodFactor = method === "qlora" ? 0.7 : method === "full" ? 0.45 : 1;
  const perGpu = (38_000 / Math.max(0.5, paramsBillions)) * methodFactor;
  const scaling = Math.pow(gpuCount, 0.9);
  const tokensPerSecond = perGpu * scaling;
  const totalTokens = datasetTokens * epochs;
  const durationSeconds = totalTokens / tokensPerSecond + 90; // + model load / checkpoint overhead
  const gpuHours = (durationSeconds / 3600) * gpuCount;
  return { totalTokens, tokensPerSecond, durationSeconds, gpuHours, costUsd: gpuHours * hourlyUsd };
}

export const DEFAULT_HYPERPARAMS: Hyperparams = {
  learning_rate: 0.0002,
  epochs: 3,
  batch_size: 16,
  lora_r: 16,
  lora_alpha: 32,
  max_seq_len: 4096,
};

export interface HyperparamErrors {
  learning_rate?: string;
  epochs?: string;
  batch_size?: string;
  lora_r?: string;
  lora_alpha?: string;
  max_seq_len?: string;
  gpu_count?: string;
}

export function validateHyperparams(h: Hyperparams, gpuCount: number): HyperparamErrors {
  const e: HyperparamErrors = {};
  if (!(h.learning_rate >= 1e-6 && h.learning_rate <= 1e-2)) e.learning_rate = "Between 1e-6 and 1e-2.";
  if (!Number.isInteger(h.epochs) || h.epochs < 1 || h.epochs > 20) e.epochs = "Whole number from 1 to 20.";
  if (!Number.isInteger(h.batch_size) || h.batch_size < 1 || h.batch_size > 512) e.batch_size = "Whole number from 1 to 512.";
  if (!Number.isInteger(h.lora_r) || h.lora_r < 1 || h.lora_r > 256) e.lora_r = "Whole number from 1 to 256.";
  if (!Number.isInteger(h.lora_alpha) || h.lora_alpha < 1) e.lora_alpha = "Must be a positive whole number.";
  if (!Number.isInteger(h.max_seq_len) || h.max_seq_len < 128 || h.max_seq_len > 131072) e.max_seq_len = "Between 128 and 131072.";
  if (![1, 2, 4, 8].includes(gpuCount)) e.gpu_count = "Choose 1, 2, 4 or 8 GPUs.";
  return e;
}
