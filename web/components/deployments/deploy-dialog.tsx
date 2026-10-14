"use client";

import * as React from "react";
import { toast } from "sonner";
import { Field } from "@/components/common/page";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useCreateDeployment, useModels, useModelVersions } from "@/lib/api/hooks";
import type { DeployStrategy, DeployTarget } from "@/lib/api/types";
import { formatParams } from "@/lib/utils";

export const TARGET_LABELS: Record<DeployTarget, string> = {
  dgx_spark: "NVIDIA DGX Spark",
  kubernetes: "Kubernetes cluster",
  azure: "Azure (AKS / AI)",
  aws: "AWS (EKS)",
  on_prem: "On-premises",
};

const STRATEGY_LABELS: Record<DeployStrategy, string> = {
  direct: "Direct (replace in place)",
  blue_green: "Blue / green",
  canary: "Canary (gradual traffic shift)",
};

const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

export function DeployDialog({
  open,
  onOpenChange,
  modelId: presetModelId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  modelId?: string | null;
}) {
  const models = useModels();
  const create = useCreateDeployment();
  const [modelId, setModelId] = React.useState<string>("");
  const [versionId, setVersionId] = React.useState<string>("");
  const [name, setName] = React.useState("");
  const [nameTouched, setNameTouched] = React.useState(false);
  const [target, setTarget] = React.useState<DeployTarget>("dgx_spark");
  const [strategy, setStrategy] = React.useState<DeployStrategy>("direct");
  const [gpu, setGpu] = React.useState(1);
  const [min, setMin] = React.useState(1);
  const [max, setMax] = React.useState(2);
  const [errors, setErrors] = React.useState<Record<string, string>>({});

  const versions = useModelVersions(modelId || null);
  const model = models.data?.find((m) => m.id === modelId);

  // reset when (re)opened
  React.useEffect(() => {
    if (open) {
      setModelId(presetModelId ?? "");
      setVersionId("");
      setName("");
      setNameTouched(false);
      setErrors({});
      setTarget("dgx_spark");
      setStrategy("direct");
      setGpu(1);
      setMin(1);
      setMax(2);
    }
  }, [open, presetModelId]);

  // default version once versions load
  React.useEffect(() => {
    if (versions.data?.length && !versions.data.some((v) => v.id === versionId)) {
      setVersionId((versions.data.find((v) => v.status === "active") ?? versions.data[0]!).id);
    }
  }, [versions.data, versionId]);

  React.useEffect(() => {
    if (!nameTouched && model) setName(`${slugify(model.name)}-prod`);
  }, [model, nameTouched]);

  function validate() {
    const e: Record<string, string> = {};
    if (!modelId) e.model = "Choose a model.";
    if (!versionId) e.version = "Choose a version.";
    if (!/^[a-z0-9][a-z0-9-]{1,62}$/.test(name)) e.name = "Use 2-63 lowercase letters, numbers or dashes.";
    if (min < 0 || max < 1 || max < min) e.replicas = "Max replicas must be at least min replicas (and ≥ 1).";
    if (gpu < 1) e.gpu = "At least 1 GPU is required.";
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    try {
      const d = await create.mutateAsync({
        name,
        model_version_id: versionId,
        target,
        strategy,
        gpu_count: gpu,
        replicas_min: min,
        replicas_max: max,
        vllm_args: { enable_prefix_caching: true, gpu_memory_utilization: 0.9 },
      });
      toast.success(`Deployment “${d.name}” is provisioning`);
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not create deployment");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New deployment</DialogTitle>
          <DialogDescription>Serve a model version behind an OpenAI-compatible endpoint powered by vLLM.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-4" noValidate>
          <Field label="Model" htmlFor="dep-model" error={errors.model}>
            <Select value={modelId} onValueChange={(v) => { setModelId(v); setVersionId(""); }}>
              <SelectTrigger id="dep-model" aria-invalid={!!errors.model}>
                <SelectValue placeholder={models.isLoading ? "Loading models…" : "Select a model"} />
              </SelectTrigger>
              <SelectContent>
                {models.data?.map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Version" htmlFor="dep-version" error={errors.version}>
              <Select value={versionId} onValueChange={setVersionId} disabled={!modelId || versions.isLoading}>
                <SelectTrigger id="dep-version">
                  <SelectValue placeholder="Select version" />
                </SelectTrigger>
                <SelectContent>
                  {versions.data?.map((v) => (
                    <SelectItem key={v.id} value={v.id}>
                      v{v.version} · {v.quantization && v.quantization !== "none" ? v.quantization.toUpperCase() : "BF16"} · {formatParams(v.param_count)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Deployment name" htmlFor="dep-name" error={errors.name}>
              <Input id="dep-name" value={name} onChange={(e) => { setName(e.target.value); setNameTouched(true); }} aria-invalid={!!errors.name} />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Target" htmlFor="dep-target">
              <Select value={target} onValueChange={(v) => setTarget(v as DeployTarget)}>
                <SelectTrigger id="dep-target">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(TARGET_LABELS) as DeployTarget[]).map((t) => (
                    <SelectItem key={t} value={t}>
                      {TARGET_LABELS[t]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Rollout strategy" htmlFor="dep-strategy">
              <Select value={strategy} onValueChange={(v) => setStrategy(v as DeployStrategy)}>
                <SelectTrigger id="dep-strategy">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(STRATEGY_LABELS) as DeployStrategy[]).map((s) => (
                    <SelectItem key={s} value={s}>
                      {STRATEGY_LABELS[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="GPUs per replica" htmlFor="dep-gpu" error={errors.gpu}>
              <Input id="dep-gpu" type="number" min={1} max={8} value={gpu} onChange={(e) => setGpu(Number(e.target.value))} />
            </Field>
            <Field label="Min replicas" htmlFor="dep-min">
              <Input id="dep-min" type="number" min={0} max={32} value={min} onChange={(e) => setMin(Number(e.target.value))} />
            </Field>
            <Field label="Max replicas" htmlFor="dep-max" error={errors.replicas}>
              <Input id="dep-max" type="number" min={1} max={32} value={max} onChange={(e) => setMax(Number(e.target.value))} />
            </Field>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              Create deployment
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
