"use client";

import * as React from "react";
import { Field } from "@/components/common/page";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Hyperparams, TuneMethod } from "@/lib/api/types";
import type { HyperparamErrors } from "@/lib/finetune";
import { cn } from "@/lib/utils";

export interface TrainConfig {
  method: TuneMethod;
  hp: Hyperparams;
  gpu: number;
}

const METHODS: { id: TuneMethod; title: string; body: string; badge: string }[] = [
  { id: "lora", title: "LoRA", body: "Trains small low-rank adapters on a frozen 16-bit base model. Fastest, highest fidelity.", badge: "Recommended for ≤ 8B" },
  { id: "qlora", title: "QLoRA", body: "Same adapters on a 4-bit quantised base. Uses ~60% less GPU memory so larger models fit on one GPU.", badge: "Lowest memory" },
];

function NumberField({
  id,
  label,
  value,
  onChange,
  error,
  hint,
  step,
  min,
  max,
}: {
  id: string;
  label: string;
  value: number;
  onChange: (n: number) => void;
  error?: string;
  hint?: string;
  step?: number | string;
  min?: number;
  max?: number;
}) {
  return (
    <Field label={label} htmlFor={id} error={error} hint={hint}>
      <Input
        id={id}
        type="number"
        inputMode="decimal"
        step={step}
        min={min}
        max={max}
        value={Number.isFinite(value) ? value : ""}
        aria-invalid={!!error}
        onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
      />
    </Field>
  );
}

export function StepConfig({ config, onChange, errors }: { config: TrainConfig; onChange: (c: TrainConfig) => void; errors: HyperparamErrors }) {
  const setHp = (patch: Partial<Hyperparams>) => onChange({ ...config, hp: { ...config.hp, ...patch } });
  return (
    <div className="space-y-8">
      <fieldset>
        <legend className="mb-3 text-sm font-semibold">Training method</legend>
        <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="Training method">
          {METHODS.map((m) => {
            const selected = config.method === m.id;
            return (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => onChange({ ...config, method: m.id })}
                className={cn("rounded-lg border p-4 text-left transition-colors hover:bg-muted/40", selected && "border-primary bg-accent/60 ring-1 ring-primary")}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold">{m.title}</span>
                  <span className="rounded-full bg-secondary px-2 py-0.5 text-[11px] text-muted-foreground">{m.badge}</span>
                </div>
                <p className="mt-1.5 text-xs text-muted-foreground">{m.body}</p>
              </button>
            );
          })}
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-3 text-sm font-semibold">Hyperparameters</legend>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <NumberField id="hp-lr" label="Learning rate" value={config.hp.learning_rate} step="0.00005" min={0} error={errors.learning_rate} hint="2e-4 is a solid default for LoRA." onChange={(n) => setHp({ learning_rate: n })} />
          <NumberField id="hp-epochs" label="Epochs" value={config.hp.epochs} step={1} min={1} max={20} error={errors.epochs} onChange={(n) => setHp({ epochs: n })} />
          <NumberField id="hp-batch" label="Batch size" value={config.hp.batch_size} step={1} min={1} error={errors.batch_size} hint="Per-step global batch size." onChange={(n) => setHp({ batch_size: n })} />
          <Field label="LoRA rank (r)" htmlFor="hp-rank" error={errors.lora_r} hint="Higher rank = more capacity, more memory.">
            <Select value={String(config.hp.lora_r)} onValueChange={(v) => setHp({ lora_r: Number(v), lora_alpha: Number(v) * 2 })}>
              <SelectTrigger id="hp-rank">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[4, 8, 16, 32, 64, 128].map((r) => (
                  <SelectItem key={r} value={String(r)}>
                    {r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <NumberField id="hp-alpha" label="LoRA alpha" value={config.hp.lora_alpha} step={1} min={1} error={errors.lora_alpha} hint="Usually 2× rank." onChange={(n) => setHp({ lora_alpha: n })} />
          <NumberField id="hp-seq" label="Max sequence length" value={config.hp.max_seq_len} step={128} min={128} error={errors.max_seq_len} onChange={(n) => setHp({ max_seq_len: n })} />
          <Field label="GPU count" htmlFor="hp-gpu" error={errors.gpu_count} hint="Data-parallel across GPUs on one node.">
            <Select value={String(config.gpu)} onValueChange={(v) => onChange({ ...config, gpu: Number(v) })}>
              <SelectTrigger id="hp-gpu">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[1, 2, 4, 8].map((g) => (
                  <SelectItem key={g} value={String(g)}>
                    {g} GPU{g > 1 ? "s" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>
      </fieldset>
    </div>
  );
}
