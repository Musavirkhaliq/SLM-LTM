"use client";

import * as React from "react";
import {
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useTheme } from "next-themes";
import { AlertTriangle, Bot, CheckCircle2, Cpu, Database, FileUp, Scissors, Search, Wand2, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { Field } from "@/components/common/page";
import { Gate } from "@/components/rbac/gate";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, Textarea } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useCreateRagIndex, useDeployments } from "@/lib/api/hooks";
import type { VectorBackend } from "@/lib/api/types";
import {
  BACKENDS,
  DEFAULT_RAG_CONFIG,
  EMBEDDING_MODELS,
  NODE_ORDER,
  RERANKERS,
  summarize,
  toIndexInput,
  validatePipeline,
  type NodeKind,
  type RagConfig,
} from "@/lib/rag";
import { cn } from "@/lib/utils";

const META: Record<NodeKind, { title: string; icon: LucideIcon; blurb: string }> = {
  upload: { title: "Upload", icon: FileUp, blurb: "Ingest source documents" },
  chunking: { title: "Chunking", icon: Scissors, blurb: "Split text into passages" },
  embeddings: { title: "Embeddings", icon: Cpu, blurb: "Vectorise each chunk" },
  vectordb: { title: "Vector DB", icon: Database, blurb: "Store and index vectors" },
  retrieval: { title: "Retrieval", icon: Search, blurb: "Find relevant context" },
  model: { title: "Model", icon: Bot, blurb: "Generate grounded answers" },
};

interface StepData extends Record<string, unknown> {
  kind: NodeKind;
  summary: string;
  hasError: boolean;
}
type StepNode = Node<StepData, "step">;

function StepNodeView({ data, selected }: NodeProps<StepNode>) {
  const meta = META[data.kind];
  const Icon = meta.icon;
  return (
    <div
      className={cn(
        "w-[188px] rounded-xl border bg-card p-3 shadow-sm transition-shadow",
        selected ? "border-primary shadow-md ring-2 ring-primary/40" : "hover:shadow-md",
        data.hasError && "border-destructive",
      )}
    >
      <Handle type="target" position={HANDLES[data.kind].target} className={cn("!h-2.5 !w-2.5 !border-2 !border-background !bg-primary", data.kind === "upload" && "!opacity-0")} isConnectable={false} />
      <div className="flex items-center gap-2.5">
        <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", selected ? "bg-primary text-primary-foreground" : "bg-accent text-accent-foreground")}>
          <Icon className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold leading-tight">{meta.title}</p>
          <p className="truncate text-[11px] text-muted-foreground">{meta.blurb}</p>
        </div>
      </div>
      <p className="mt-2.5 truncate rounded-md bg-muted px-2 py-1 font-mono text-[11px] text-muted-foreground" title={data.summary}>
        {data.summary}
      </p>
      {data.hasError && <AlertTriangle className="absolute -right-1.5 -top-1.5 h-4 w-4 fill-background text-destructive" aria-label="Has configuration errors" />}
      <Handle type="source" position={HANDLES[data.kind].source} className={cn("!h-2.5 !w-2.5 !border-2 !border-background !bg-primary", data.kind === "model" && "!opacity-0")} isConnectable={false} />
    </div>
  );
}

/** Snake layout: row 1 flows left to right, row 2 flows right to left. */
const HANDLES: Record<NodeKind, { source: Position; target: Position }> = {
  upload: { target: Position.Left, source: Position.Right },
  chunking: { target: Position.Left, source: Position.Right },
  embeddings: { target: Position.Left, source: Position.Bottom },
  vectordb: { target: Position.Top, source: Position.Left },
  retrieval: { target: Position.Right, source: Position.Left },
  model: { target: Position.Right, source: Position.Left },
};

const nodeTypes = { step: StepNodeView };

const INITIAL_POS: Record<NodeKind, { x: number; y: number }> = {
  upload: { x: 0, y: 40 },
  chunking: { x: 250, y: 40 },
  embeddings: { x: 500, y: 40 },
  vectordb: { x: 500, y: 190 },
  retrieval: { x: 250, y: 190 },
  model: { x: 0, y: 190 },
};

const edges: Edge[] = NODE_ORDER.slice(0, -1).map((k, i) => ({
  id: `${k}-${NODE_ORDER[i + 1]}`,
  source: k,
  target: NODE_ORDER[i + 1]!,
  animated: true,
  markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18 },
  style: { strokeWidth: 1.75 },
}));

const FORMAT_OPTIONS = ["pdf", "docx", "md", "html", "txt", "csv"];

function ConfigPanel({ kind, config, onChange }: { kind: NodeKind; config: RagConfig; onChange: (c: RagConfig) => void }) {
  const deployments = useDeployments();
  const aliases = [...new Set([config.model.alias, ...(deployments.data?.filter((d) => d.status === "running").map((d) => d.endpoint_alias ?? d.name) ?? [])])];
  const set = <K extends NodeKind>(k: K, patch: Partial<RagConfig[K]>) => onChange({ ...config, [k]: { ...config[k], ...patch } });
  const num = (v: string) => (v === "" ? 0 : Number(v));

  switch (kind) {
    case "upload":
      return (
        <div className="space-y-4">
          <fieldset>
            <legend className="mb-2 text-sm font-medium">Accepted formats</legend>
            <div className="grid grid-cols-3 gap-2">
              {FORMAT_OPTIONS.map((f) => (
                <label key={f} className="flex cursor-pointer items-center gap-2 text-sm uppercase">
                  <Checkbox
                    checked={config.upload.formats.includes(f)}
                    onCheckedChange={(c) => set("upload", { formats: c ? [...config.upload.formats, f] : config.upload.formats.filter((x) => x !== f) })}
                    aria-label={f}
                  />
                  {f}
                </label>
              ))}
            </div>
          </fieldset>
          <Field label="Max file size (MB)" htmlFor="rg-max">
            <Input id="rg-max" type="number" min={1} max={500} value={config.upload.maxSizeMb} onChange={(e) => set("upload", { maxSizeMb: num(e.target.value) })} />
          </Field>
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>
              OCR scanned pages
              <span className="block text-xs text-muted-foreground">Extract text from images inside PDFs.</span>
            </span>
            <Switch checked={config.upload.ocr} onCheckedChange={(v) => set("upload", { ocr: v })} aria-label="OCR scanned pages" />
          </label>
        </div>
      );
    case "chunking":
      return (
        <div className="space-y-4">
          <Field label="Strategy" htmlFor="rg-strategy">
            <Select value={config.chunking.strategy} onValueChange={(v) => set("chunking", { strategy: v as RagConfig["chunking"]["strategy"] })}>
              <SelectTrigger id="rg-strategy">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="recursive">Recursive (paragraph → sentence)</SelectItem>
                <SelectItem value="fixed">Fixed size</SelectItem>
                <SelectItem value="semantic">Semantic boundaries</SelectItem>
                <SelectItem value="markdown">Markdown headings</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label={`Chunk size: ${config.chunking.size} tokens`} htmlFor="rg-size">
            <Slider id="rg-size" aria-label="Chunk size" min={64} max={2048} step={32} value={[config.chunking.size]} onValueChange={([v]) => set("chunking", { size: v! })} />
          </Field>
          <Field label={`Overlap: ${config.chunking.overlap} tokens`} htmlFor="rg-overlap">
            <Slider id="rg-overlap" aria-label="Chunk overlap" min={0} max={512} step={8} value={[config.chunking.overlap]} onValueChange={([v]) => set("chunking", { overlap: v! })} />
          </Field>
        </div>
      );
    case "embeddings":
      return (
        <div className="space-y-4">
          <Field label="Embedding model" htmlFor="rg-emb" hint={EMBEDDING_MODELS.find((m) => m.name === config.embeddings.model)?.note}>
            <Select
              value={config.embeddings.model}
              onValueChange={(v) => set("embeddings", { model: v, dim: EMBEDDING_MODELS.find((m) => m.name === v)?.dim ?? config.embeddings.dim })}
            >
              <SelectTrigger id="rg-emb">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {EMBEDDING_MODELS.map((m) => (
                  <SelectItem key={m.name} value={m.name}>
                    {m.name} · {m.dim}d
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Dimensions" htmlFor="rg-dim" hint="Determined by the model.">
            <Input id="rg-dim" type="number" value={config.embeddings.dim} readOnly />
          </Field>
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>Normalise vectors</span>
            <Switch checked={config.embeddings.normalize} onCheckedChange={(v) => set("embeddings", { normalize: v })} aria-label="Normalise vectors" />
          </label>
        </div>
      );
    case "vectordb":
      return (
        <div className="space-y-4">
          <Field label="Backend" htmlFor="rg-backend">
            <Select value={config.vectordb.backend} onValueChange={(v) => set("vectordb", { backend: v as VectorBackend })}>
              <SelectTrigger id="rg-backend">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {BACKENDS.map((b) => (
                  <SelectItem key={b.id} value={b.id}>
                    {b.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Index name" htmlFor="rg-name">
            <Input id="rg-name" value={config.vectordb.name} onChange={(e) => set("vectordb", { name: e.target.value })} />
          </Field>
          <Field label="Distance metric" htmlFor="rg-metric">
            <Select value={config.vectordb.metric} onValueChange={(v) => set("vectordb", { metric: v as RagConfig["vectordb"]["metric"] })}>
              <SelectTrigger id="rg-metric">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="cosine">Cosine</SelectItem>
                <SelectItem value="dot">Dot product</SelectItem>
                <SelectItem value="euclid">Euclidean</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>
              Hybrid search
              <span className="block text-xs text-muted-foreground">Combine dense vectors with BM25 keywords.</span>
            </span>
            <Switch checked={config.vectordb.hybrid} onCheckedChange={(v) => set("vectordb", { hybrid: v })} aria-label="Hybrid search" />
          </label>
        </div>
      );
    case "retrieval":
      return (
        <div className="space-y-4">
          <Field label={`Top-K: ${config.retrieval.topK}`} htmlFor="rg-topk">
            <Slider id="rg-topk" aria-label="Top K" min={1} max={20} step={1} value={[config.retrieval.topK]} onValueChange={([v]) => set("retrieval", { topK: v! })} />
          </Field>
          <Field label={`Score threshold: ${config.retrieval.threshold.toFixed(2)}`} htmlFor="rg-thr">
            <Slider id="rg-thr" aria-label="Score threshold" min={0} max={1} step={0.05} value={[config.retrieval.threshold]} onValueChange={([v]) => set("retrieval", { threshold: v! })} />
          </Field>
          <Field label="Reranker" htmlFor="rg-rerank">
            <Select value={config.retrieval.reranker} onValueChange={(v) => set("retrieval", { reranker: v })}>
              <SelectTrigger id="rg-rerank">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RERANKERS.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r === "none" ? "No reranker" : r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>
              MMR diversification
              <span className="block text-xs text-muted-foreground">Reduce near-duplicate passages.</span>
            </span>
            <Switch checked={config.retrieval.mmr} onCheckedChange={(v) => set("retrieval", { mmr: v })} aria-label="MMR diversification" />
          </label>
        </div>
      );
    case "model":
      return (
        <div className="space-y-4">
          <Field label="Generation model" htmlFor="rg-model" hint="Running deployments are listed.">
            <Select value={config.model.alias} onValueChange={(v) => set("model", { alias: v })}>
              <SelectTrigger id="rg-model">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {aliases.map((a) => (
                  <SelectItem key={a} value={a}>
                    {a}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={`Temperature: ${config.model.temperature.toFixed(1)}`} htmlFor="rg-temp">
            <Slider id="rg-temp" aria-label="Temperature" min={0} max={2} step={0.1} value={[config.model.temperature]} onValueChange={([v]) => set("model", { temperature: v! })} />
          </Field>
          <Field label="System prompt" htmlFor="rg-sys">
            <Textarea id="rg-sys" rows={5} value={config.model.systemPrompt} onChange={(e) => set("model", { systemPrompt: e.target.value })} />
          </Field>
        </div>
      );
  }
}

export function PipelineBuilder({ onCreated }: { onCreated?: () => void }) {
  const { resolvedTheme } = useTheme();
  const create = useCreateRagIndex();
  const [config, setConfig] = React.useState<RagConfig>(DEFAULT_RAG_CONFIG);
  const [selected, setSelected] = React.useState<NodeKind>("chunking");
  const issues = React.useMemo(() => validatePipeline(config), [config]);
  const errors = issues.filter((i) => i.level === "error");

  const [nodes, setNodes] = React.useState<StepNode[]>(() =>
    NODE_ORDER.map((kind) => ({
      id: kind,
      type: "step",
      position: INITIAL_POS[kind],
      data: { kind, summary: summarize(kind, DEFAULT_RAG_CONFIG), hasError: false },
      deletable: false,
      connectable: false,
    })),
  );

  React.useEffect(() => {
    setNodes((prev) =>
      prev.map((n) => ({
        ...n,
        selected: n.id === selected,
        data: { ...n.data, summary: summarize(n.data.kind, config), hasError: issues.some((i) => i.node === n.data.kind && i.level === "error") },
      })),
    );
  }, [config, issues, selected]);

  const onNodesChange = React.useCallback((changes: NodeChange<StepNode>[]) => setNodes((nds) => applyNodeChanges(changes, nds)), []);

  async function createIndex() {
    try {
      const idx = await create.mutateAsync(toIndexInput(config));
      toast.success(`Index “${idx.name}” is being created`, { description: "Upload documents on the Documents tab." });
      onCreated?.();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not create the index");
    }
  }

  const meta = META[selected];

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b px-4 py-2.5">
          <div>
            <p className="text-sm font-semibold">Pipeline</p>
            <p className="text-xs text-muted-foreground">Select a stage to configure it. Drag stages to rearrange the canvas.</p>
          </div>
          <div className="flex items-center gap-1.5 text-xs">
            {errors.length === 0 ? (
              <span className="flex items-center gap-1 text-success">
                <CheckCircle2 className="h-4 w-4" aria-hidden /> Valid
              </span>
            ) : (
              <span className="flex items-center gap-1 text-destructive">
                <AlertTriangle className="h-4 w-4" aria-hidden /> {errors.length} issue{errors.length > 1 ? "s" : ""}
              </span>
            )}
          </div>
        </div>
        <div className="h-[400px] bg-muted/30" data-testid="pipeline-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onNodeClick={(_, n) => setSelected(n.id as NodeKind)}
            nodesConnectable={false}
            deleteKeyCode={null}
            fitView
            fitViewOptions={{ padding: 0.1, maxZoom: 1.1 }}
            minZoom={0.4}
            maxZoom={1.5}
            colorMode={resolvedTheme === "dark" ? "dark" : "light"}
            proOptions={{ hideAttribution: true }}
            aria-label="RAG pipeline: Upload to Chunking to Embeddings to Vector DB to Retrieval to Model"
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} />
            <Controls showInteractive={false} position="top-left" orientation="horizontal" />
          </ReactFlow>
        </div>
        {issues.length > 0 && (
          <ul className="space-y-1 border-t px-4 py-3 text-xs" aria-label="Validation messages">
            {issues.map((i, idx) => (
              <li key={idx} className={cn("flex items-start gap-2", i.level === "error" ? "text-destructive" : "text-warning")}>
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                <button type="button" className="text-left underline-offset-2 hover:underline" onClick={() => setSelected(i.node)}>
                  <span className="font-medium">{META[i.node].title}:</span> {i.message}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="flex flex-col p-5" aria-live="polite">
        <div className="flex items-center gap-2.5 border-b pb-4">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <meta.icon className="h-4 w-4" aria-hidden />
          </span>
          <div>
            <h3 className="text-sm font-semibold">{meta.title} settings</h3>
            <p className="text-xs text-muted-foreground">{meta.blurb}</p>
          </div>
        </div>
        <div className="flex-1 py-4">
          <ConfigPanel kind={selected} config={config} onChange={setConfig} />
        </div>
        <div className="space-y-2 border-t pt-4">
          <div className="grid grid-cols-2 gap-2">
            <Button variant="outline" size="sm" onClick={() => setConfig(DEFAULT_RAG_CONFIG)}>
              <Wand2 /> Reset
            </Button>
            <Gate capability="manage_ml">
              <Button size="sm" onClick={createIndex} disabled={errors.length > 0} loading={create.isPending}>
                Create index
              </Button>
            </Gate>
          </div>
          <p className="text-[11px] text-muted-foreground">Creates a vector index with these chunking, embedding and retrieval settings.</p>
        </div>
      </Card>
    </div>
  );
}
