"use client";

import * as React from "react";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, Info, Play, Plus } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/common/page";
import { StepConfig, type TrainConfig } from "@/components/finetune/step-config";
import { StepDataset } from "@/components/finetune/step-dataset";
import { StepLaunch } from "@/components/finetune/step-launch";
import { StepModel } from "@/components/finetune/step-model";
import { StepMonitor } from "@/components/finetune/step-monitor";
import { STEPS, Stepper } from "@/components/finetune/stepper";
import { useCan } from "@/components/providers/auth-provider";
import { Gate } from "@/components/rbac/gate";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useCreateTrainingJob, useDatasets, useModels } from "@/lib/api/hooks";
import { DEFAULT_HYPERPARAMS, estimateTraining, validateHyperparams } from "@/lib/finetune";

function Wizard() {
  const params = useSearchParams();
  const presetModel = params.get("model");
  const canTrain = useCan("manage_ml");
  const models = useModels();
  const datasets = useDatasets();
  const createJob = useCreateTrainingJob();

  const [step, setStep] = React.useState(0);
  const [maxReached, setMaxReached] = React.useState(0);
  const [datasetId, setDatasetId] = React.useState<string | null>(null);
  const [config, setConfig] = React.useState<TrainConfig>({ method: "qlora", hp: DEFAULT_HYPERPARAMS, gpu: 1 });
  const [versionId, setVersionId] = React.useState<string | null>(null);
  const [jobName, setJobName] = React.useState("");
  const [nameTouched, setNameTouched] = React.useState(false);
  const [jobId, setJobId] = React.useState<string | null>(null);
  const [nameError, setNameError] = React.useState<string | null>(null);

  // preselect a base model from ?model=<id>
  React.useEffect(() => {
    if (presetModel && !versionId) {
      const m = models.data?.find((x) => x.id === presetModel);
      if (m?.latest_version) setVersionId(m.latest_version.id);
    }
  }, [presetModel, models.data, versionId]);

  const model = models.data?.find((m) => m.latest_version?.id === versionId);
  const dataset = datasets.data?.find((d) => d.id === datasetId);
  const errors = validateHyperparams(config.hp, config.gpu);
  const configValid = Object.keys(errors).length === 0;

  React.useEffect(() => {
    if (!nameTouched && model) setJobName(`${model.name}-ft-${String(Math.floor(100 + Math.random() * 900))}`);
  }, [model, nameTouched]);

  const estimate = estimateTraining({
    datasetTokens: dataset?.token_count ?? 1_000_000,
    paramsBillions: (model?.latest_version?.param_count ?? 7e9) / 1e9,
    method: config.method,
    epochs: Number.isFinite(config.hp.epochs) ? config.hp.epochs : 1,
    gpuCount: config.gpu,
  });

  const canNext = step === 0 ? !!datasetId : step === 1 ? configValid : step === 2 ? !!versionId : true;

  function goTo(n: number) {
    setStep(n);
    setMaxReached((m) => Math.max(m, n));
  }

  async function launch() {
    if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{2,62}$/.test(jobName)) {
      setNameError("Use 3-63 characters: letters, numbers, dots, dashes or underscores.");
      return;
    }
    setNameError(null);
    try {
      const job = await createJob.mutateAsync({
        name: jobName,
        base_version_id: versionId!,
        dataset_id: datasetId!,
        method: config.method,
        hyperparams: config.hp,
        gpu_count: config.gpu,
      });
      setJobId(job.id);
      toast.success(`Launched ${job.name}`, { description: "Queued - training starts as soon as a GPU is free." });
      goTo(4);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not launch the job");
    }
  }

  function reset() {
    setDatasetId(null);
    setConfig({ method: "qlora", hp: DEFAULT_HYPERPARAMS, gpu: 1 });
    setVersionId(null);
    setNameTouched(false);
    setJobName("");
    setMaxReached(0);
    setStep(0);
  }

  return (
    <div className="page-container animate-fade-in">
      <PageHeader
        title="Fine-tuning"
        description="Adapt a base model to your data with LoRA or QLoRA. Five steps from dataset to a monitored training run."
        actions={
          step === 4 && (
            <Gate capability="manage_ml">
              <Button variant="outline" onClick={reset}>
                <Plus /> New fine-tune
              </Button>
            </Gate>
          )
        }
      />

      {!canTrain && (
        <div role="note" className="mb-4 flex items-start gap-2 rounded-lg border border-info/30 bg-info/10 px-4 py-3 text-sm">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-info" aria-hidden />
          <p>Your role can monitor training jobs but cannot upload datasets or launch jobs. Ask an ML Engineer or Org Admin to start a run.</p>
        </div>
      )}

      <Card className="p-5 sm:p-6">
        <Stepper current={step} maxReached={canTrain ? maxReached : 4} onSelect={goTo} />
        <div className="mt-6 border-t pt-6">
          <h2 className="sr-only">
            Step {step + 1}: {STEPS[step]}
          </h2>
          {step === 0 && <StepDataset datasetId={datasetId} onSelect={setDatasetId} />}
          {step === 1 && <StepConfig config={config} onChange={setConfig} errors={errors} />}
          {step === 2 && <StepModel versionId={versionId} onSelect={(id) => setVersionId(id)} />}
          {step === 3 && (
            <StepLaunch
              jobName={jobName}
              onJobName={(n) => {
                setJobName(n);
                setNameTouched(true);
              }}
              nameError={nameError}
              dataset={dataset}
              modelName={model ? `${model.name} v${model.latest_version?.version}` : "—"}
              method={config.method}
              hp={config.hp}
              gpu={config.gpu}
              estimate={estimate}
            />
          )}
          {step === 4 && <StepMonitor jobId={jobId} onSelect={setJobId} />}
        </div>

        {step < 4 && (
          <div className="mt-8 flex items-center justify-between border-t pt-5">
            <Button variant="ghost" onClick={() => goTo(step - 1)} disabled={step === 0}>
              <ArrowLeft /> Back
            </Button>
            <div className="flex items-center gap-3">
              {!canNext && <span className="hidden text-xs text-muted-foreground sm:inline">{step === 0 ? "Select or upload a dataset to continue" : step === 1 ? "Fix the highlighted fields" : "Select a base model"}</span>}
              {step < 3 ? (
                <Button onClick={() => goTo(step + 1)} disabled={!canNext}>
                  Next <ArrowRight />
                </Button>
              ) : (
                <>
                  <Button variant="outline" onClick={() => goTo(4)}>
                    Skip to monitor
                  </Button>
                  <Gate capability="manage_ml">
                    <Button onClick={launch} loading={createJob.isPending}>
                      <Play /> Launch training
                    </Button>
                  </Gate>
                </>
              )}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

export default function FineTuningPage() {
  return (
    <Suspense fallback={null}>
      <Wizard />
    </Suspense>
  );
}
