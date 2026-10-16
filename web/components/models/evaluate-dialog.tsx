"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Field } from "@/components/common/page";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useCreateEvaluation, useDatasets, useModels } from "@/lib/api/hooks";
import { titleCase } from "@/lib/utils";

export const EVAL_METRIC_OPTIONS = [
  "accuracy",
  "f1",
  "rouge_l",
  "faithfulness",
  "groundedness",
  "json_validity",
  "hallucination",
  "toxicity",
];

const NONE = "__none__";

export function EvaluateDialog({
  open,
  onOpenChange,
  modelId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  modelId: string | null;
}) {
  const router = useRouter();
  const models = useModels();
  const datasets = useDatasets();
  const create = useCreateEvaluation();
  const [selectedModel, setSelectedModel] = React.useState<string>("");
  const model = models.data?.find((m) => m.id === selectedModel);
  const [name, setName] = React.useState("");
  const [datasetId, setDatasetId] = React.useState("");
  const [compareWith, setCompareWith] = React.useState(NONE);
  const [metrics, setMetrics] = React.useState<string[]>(["accuracy", "f1", "faithfulness"]);
  const [judgeModel, setJudgeModel] = React.useState<string>("llama-3.1-8b");
  const [error, setError] = React.useState<string | null>(null);

  const hasJudgedMetrics = metrics.some((m) =>
    ["faithfulness", "groundedness", "hallucination", "toxicity"].includes(m)
  );

  React.useEffect(() => {
    if (open) {
      setSelectedModel(modelId ?? "");
      setCompareWith(NONE);
      setError(null);
    }
  }, [open, modelId]);

  React.useEffect(() => {
    if (open) setName(model ? `${model.name} evaluation` : "");
  }, [open, model]);

  React.useEffect(() => {
    if (open && !datasetId && datasets.data?.length) {
      setDatasetId((datasets.data.find((d) => d.purpose === "eval") ?? datasets.data[0]!).id);
    }
  }, [open, datasets.data, datasetId]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const baseVersion = model?.latest_version?.id;
    if (!model) return setError("Choose a model to evaluate.");
    if (!baseVersion) return setError("This model has no active version to evaluate.");
    if (!name.trim()) return setError("Give the evaluation a name.");
    if (!datasetId) return setError("Choose an evaluation dataset.");
    if (!metrics.length) return setError("Select at least one metric.");
    if (hasJudgedMetrics && !judgeModel.trim()) {
      return setError("Please specify a judge model for LLM-evaluated metrics.");
    }
    const other = models.data?.find((m) => m.id === compareWith)?.latest_version?.id;
    try {
      await create.mutateAsync({
        name: name.trim(),
        dataset_id: datasetId,
        model_version_ids: other ? [baseVersion, other] : [baseVersion],
        metrics,
        judge_model: hasJudgedMetrics ? judgeModel.trim() : undefined,
      });
      toast.success("Evaluation queued", {
        description: "Track progress on the Evaluation page.",
        action: { label: "Open", onClick: () => router.push("/evaluation") },
      });
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the evaluation");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{model ? `Evaluate ${model.name}` : "New evaluation"}</DialogTitle>
          <DialogDescription>Run a benchmark against a dataset. Add a second model for an A/B comparison.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-4" noValidate>
          <Field label="Model" htmlFor="ev-model">
            <Select value={selectedModel} onValueChange={setSelectedModel}>
              <SelectTrigger id="ev-model">
                <SelectValue placeholder="Select a model" />
              </SelectTrigger>
              <SelectContent>
                {models.data
                  ?.filter((m) => m.latest_version)
                  .map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.name}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Evaluation name" htmlFor="ev-name">
            <Input id="ev-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Dataset" htmlFor="ev-dataset" hint={datasets.data?.length === 0 ? undefined : "Golden / evaluation datasets are listed first."}>
            <Select value={datasetId} onValueChange={setDatasetId}>
              <SelectTrigger id="ev-dataset">
                <SelectValue placeholder="Select a dataset" />
              </SelectTrigger>
              <SelectContent>
                {[...(datasets.data ?? [])]
                  .sort((a, b) => (a.purpose === b.purpose ? 0 : a.purpose === "eval" ? -1 : 1))
                  .map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.name} · {d.purpose}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Compare against (optional)" htmlFor="ev-compare">
            <Select value={compareWith} onValueChange={setCompareWith}>
              <SelectTrigger id="ev-compare">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>No comparison</SelectItem>
                {models.data
                  ?.filter((m) => m.id !== selectedModel && m.latest_version)
                  .map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.name}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </Field>
          <fieldset>
            <legend className="mb-2 text-sm font-medium">Metrics</legend>
            <div className="grid grid-cols-2 gap-2">
              {EVAL_METRIC_OPTIONS.map((m) => (
                <label key={m} className="flex cursor-pointer items-center gap-2 text-sm">
                  <Checkbox
                    checked={metrics.includes(m)}
                    onCheckedChange={(c) => setMetrics((cur) => (c ? [...cur, m] : cur.filter((x) => x !== m)))}
                  />
                  {titleCase(m)}
                </label>
              ))}
            </div>
          </fieldset>
          {hasJudgedMetrics && (
            <Field
              label="Judge Model"
              htmlFor="ev-judge"
              hint="Required for LLM-judged metrics (faithfulness, groundedness, hallucination, toxicity)."
            >
              <Input
                id="ev-judge"
                placeholder="e.g. llama-3.1-8b, qwen2.5-7b"
                value={judgeModel}
                onChange={(e) => setJudgeModel(e.target.value)}
              />
            </Field>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              Start evaluation
            </Button>
          </DialogFooter>
        </form>
        <p className="text-xs text-muted-foreground">
          Results appear on the <Link href="/evaluation" className="text-primary underline-offset-2 hover:underline">Evaluation</Link> page.
        </p>
      </DialogContent>
    </Dialog>
  );
}
