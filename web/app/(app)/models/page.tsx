"use client";

import * as React from "react";
import Link from "next/link";
import { Boxes, FlaskConical, History, Plus, Rocket, Search, SlidersHorizontal } from "lucide-react";
import { DeployDialog } from "@/components/deployments/deploy-dialog";
import { CardGridSkeleton, EmptyState, ErrorState, PageHeader } from "@/components/common/page";
import { EvaluateDialog } from "@/components/models/evaluate-dialog";
import { RegisterModelDialog } from "@/components/models/register-dialog";
import { VersionsSheet } from "@/components/models/versions-sheet";
import { Gate } from "@/components/rbac/gate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useModels } from "@/lib/api/hooks";
import type { Model } from "@/lib/api/types";
import { cn, formatParams, titleCase } from "@/lib/utils";

const BENCHMARKS: { key: string; label: string }[] = [
  { key: "mmlu", label: "MMLU" },
  { key: "gsm8k", label: "GSM8K" },
  { key: "humaneval", label: "HumanEval" },
];

const FAMILY_TONE: Record<string, string> = {
  phi: "from-sky-500 to-blue-600",
  llama: "from-amber-500 to-orange-600",
  gemma: "from-emerald-500 to-teal-600",
  qwen: "from-violet-500 to-purple-600",
  mistral: "from-rose-500 to-pink-600",
};

function ScoreBar({ label, value }: { label: string; value?: number }) {
  const pct = value === undefined ? null : Math.round(value * 1000) / 10;
  return (
    <div className="grid grid-cols-[72px_1fr_44px] items-center gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <div className="h-1.5 overflow-hidden rounded-full bg-secondary" role="meter" aria-label={`${label} score`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? 0}>
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct ?? 0}%` }} />
      </div>
      <span className="text-right font-medium tabular-nums">{pct === null ? "—" : pct.toFixed(1)}</span>
    </div>
  );
}

function ModelCard({
  model,
  onDeploy,
  onEvaluate,
  onVersions,
}: {
  model: Model;
  onDeploy: () => void;
  onEvaluate: () => void;
  onVersions: () => void;
}) {
  const v = model.latest_version;
  const tone = FAMILY_TONE[model.family ?? ""] ?? "from-slate-500 to-slate-700";
  return (
    <Card className="flex flex-col p-5 transition-shadow hover:shadow-md">
      <div className="flex items-start gap-3">
        <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br text-sm font-bold uppercase text-white", tone)} aria-hidden>
          {(model.family ?? model.name).slice(0, 2)}
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold">{model.name}</h3>
          <p className="truncate text-xs text-muted-foreground">{model.hf_repo ?? "Private registry"}</p>
        </div>
        {model.source === "finetune" ? <Badge variant="default">Fine-tuned</Badge> : model.org_id === null ? <Badge variant="secondary">Catalogue</Badge> : <Badge variant="outline">Custom</Badge>}
      </div>
      <p className="mt-3 line-clamp-2 min-h-[2.5rem] text-sm text-muted-foreground">{model.description}</p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        <Badge variant="secondary">{formatParams(v?.param_count)} params</Badge>
        {v?.context_length ? <Badge variant="secondary">{Math.round(v.context_length / 1024)}K ctx</Badge> : null}
        {v?.quantization && v.quantization !== "none" ? <Badge variant="secondary">{v.quantization.toUpperCase()}</Badge> : null}
        {model.license && <Badge variant="outline">{model.license}</Badge>}
      </div>
      <div className="mt-4 flex items-center justify-between text-[11px] text-muted-foreground">
        <span className="font-medium uppercase tracking-wide">Benchmarks</span>
        <button type="button" onClick={onVersions} className="inline-flex items-center gap-1 rounded text-xs underline-offset-2 hover:text-foreground hover:underline" aria-label={`View ${model.version_count ?? 1} versions of ${model.name}`}>
          <History className="h-3 w-3" aria-hidden /> {model.version_count ?? 1} version{(model.version_count ?? 1) === 1 ? "" : "s"}
        </button>
      </div>
      <div className="mb-5 mt-2 space-y-1.5" aria-label="Benchmark scores">
        {BENCHMARKS.map((b) => (
          <ScoreBar key={b.key} label={b.label} value={v?.benchmark_scores[b.key]} />
        ))}
      </div>
      <div className="mt-auto flex flex-wrap items-center gap-2 border-t pt-4">
        <Gate capability="manage_deploy">
          <Button size="sm" onClick={onDeploy}>
            <Rocket /> Deploy
          </Button>
        </Gate>
        <Gate capability="manage_ml">
          <Button size="sm" variant="outline" onClick={onEvaluate}>
            <FlaskConical /> Evaluate
          </Button>
        </Gate>
        <Gate capability="manage_ml">
          <Button size="sm" variant="outline" asChild>
            <Link href={`/fine-tuning?model=${model.id}`}>
              <SlidersHorizontal /> Fine-tune
            </Link>
          </Button>
        </Gate>
      </div>
    </Card>
  );
}

export default function ModelsPage() {
  const models = useModels();
  const [query, setQuery] = React.useState("");
  const [family, setFamily] = React.useState("all");
  const [source, setSource] = React.useState("all");
  const [deployFor, setDeployFor] = React.useState<string | null>(null);
  const [evalFor, setEvalFor] = React.useState<string | null>(null);
  const [versionsFor, setVersionsFor] = React.useState<Model | null>(null);
  const [registerOpen, setRegisterOpen] = React.useState(false);

  const families = React.useMemo(() => [...new Set((models.data ?? []).map((m) => m.family).filter(Boolean))] as string[], [models.data]);
  const filtered = (models.data ?? []).filter(
    (m) =>
      (family === "all" || m.family === family) &&
      (source === "all" || (source === "finetune" ? m.source === "finetune" : m.source !== "finetune")) &&
      (!query || `${m.name} ${m.description ?? ""} ${m.hf_repo ?? ""}`.toLowerCase().includes(query.toLowerCase())),
  );

  return (
    <div className="page-container animate-fade-in">
      <PageHeader
        title="Models"
        description="Your model registry: curated base models and your fine-tuned versions, with benchmark scores."
        actions={
          <Gate capability="manage_ml">
            <Button onClick={() => setRegisterOpen(true)}>
              <Plus /> Register model
            </Button>
          </Gate>
        }
      />

      <div className="mb-5 flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1 sm:max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input aria-label="Search models" placeholder="Search models…" className="pl-9" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Select value={family} onValueChange={setFamily}>
          <SelectTrigger aria-label="Filter by family" className="sm:w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All families</SelectItem>
            {families.map((f) => (
              <SelectItem key={f} value={f}>
                {titleCase(f)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={source} onValueChange={setSource}>
          <SelectTrigger aria-label="Filter by source" className="sm:w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All sources</SelectItem>
            <SelectItem value="base">Base models</SelectItem>
            <SelectItem value="finetune">Fine-tuned</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {models.isLoading ? (
        <CardGridSkeleton count={6} />
      ) : models.isError ? (
        <ErrorState error={models.error} onRetry={() => models.refetch()} title="Could not load models" />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={Boxes}
          title={models.data?.length ? "No models match your filters" : "No models yet"}
          description={models.data?.length ? "Try a different search or clear the filters." : "Register a model from Hugging Face to get started."}
          action={
            models.data?.length ? (
              <Button variant="outline" onClick={() => { setQuery(""); setFamily("all"); setSource("all"); }}>
                Clear filters
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((m) => (
            <ModelCard key={m.id} model={m} onDeploy={() => setDeployFor(m.id)} onEvaluate={() => setEvalFor(m.id)} onVersions={() => setVersionsFor(m)} />
          ))}
        </div>
      )}

      <DeployDialog open={!!deployFor} onOpenChange={(o) => !o && setDeployFor(null)} modelId={deployFor} />
      <EvaluateDialog open={!!evalFor} onOpenChange={(o) => !o && setEvalFor(null)} modelId={evalFor} />
      <VersionsSheet model={versionsFor} onOpenChange={(o) => !o && setVersionsFor(null)} />
      <RegisterModelDialog open={registerOpen} onOpenChange={setRegisterOpen} />
    </div>
  );
}
