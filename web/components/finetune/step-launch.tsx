"use client";

import { Clock, Coins, Cpu, Layers } from "lucide-react";
import { Field } from "@/components/common/page";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { Dataset, Hyperparams, TuneMethod } from "@/lib/api/types";
import { GPU_HOUR_USD, type TrainingEstimate } from "@/lib/finetune";
import { formatDuration, formatNumber, formatUsd } from "@/lib/utils";

export function StepLaunch({
  jobName,
  onJobName,
  nameError,
  dataset,
  modelName,
  method,
  hp,
  gpu,
  estimate,
}: {
  jobName: string;
  onJobName: (n: string) => void;
  nameError?: string | null;
  dataset: Dataset | undefined;
  modelName: string;
  method: TuneMethod;
  hp: Hyperparams;
  gpu: number;
  estimate: TrainingEstimate;
}) {
  const rows: [string, string][] = [
    ["Base model", modelName],
    ["Dataset", dataset ? `${dataset.name} (${formatNumber(dataset.row_count ?? 0)} rows)` : "—"],
    ["Method", method.toUpperCase()],
    ["Learning rate", String(hp.learning_rate)],
    ["Epochs · batch size", `${hp.epochs} · ${hp.batch_size}`],
    ["LoRA rank / alpha", `${hp.lora_r} / ${hp.lora_alpha}`],
    ["Max sequence length", String(hp.max_seq_len)],
    ["GPUs", String(gpu)],
  ];
  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <section className="space-y-4 lg:col-span-3" aria-labelledby="launch-summary">
        <Field label="Job name" htmlFor="job-name" error={nameError} hint="Used to name the resulting model version.">
          <Input id="job-name" value={jobName} onChange={(e) => onJobName(e.target.value)} aria-invalid={!!nameError} />
        </Field>
        <h3 id="launch-summary" className="pt-2 text-sm font-semibold">
          Summary
        </h3>
        <dl className="divide-y rounded-lg border">
          {rows.map(([k, v]) => (
            <div key={k} className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="truncate text-right font-medium">{v}</dd>
            </div>
          ))}
        </dl>
      </section>

      <Card className="h-fit p-5 lg:col-span-2" aria-labelledby="cost-est">
        <h3 id="cost-est" className="text-sm font-semibold">
          Cost estimate
        </h3>
        <p className="mt-2 flex items-baseline gap-2">
          <span className="text-3xl font-semibold tracking-tight tabular-nums" data-testid="cost-estimate">
            {formatUsd(estimate.costUsd)}
          </span>
          <span className="text-xs text-muted-foreground">at {formatUsd(GPU_HOUR_USD)} / GPU-hour</span>
        </p>
        <dl className="mt-4 space-y-3 text-sm">
          {[
            [Clock, "Estimated duration", formatDuration(estimate.durationSeconds)],
            [Cpu, "GPU-hours", estimate.gpuHours.toFixed(2)],
            [Layers, "Tokens to process", formatNumber(estimate.totalTokens)],
            [Coins, "Throughput", `${formatNumber(estimate.tokensPerSecond, 1)} tok/s`],
          ].map(([Icon, label, value]) => {
            const I = Icon as typeof Clock;
            return (
              <div key={label as string} className="flex items-center gap-3">
                <I className="h-4 w-4 text-muted-foreground" aria-hidden />
                <dt className="text-muted-foreground">{label as string}</dt>
                <dd className="ml-auto font-medium tabular-nums">{value as string}</dd>
              </div>
            );
          })}
        </dl>
        <p className="mt-4 text-xs text-muted-foreground">Estimates are indicative; actual cost depends on queue time, sequence packing and the GPU type assigned.</p>
      </Card>
    </div>
  );
}
