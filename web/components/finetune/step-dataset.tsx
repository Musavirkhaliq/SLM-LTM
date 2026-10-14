"use client";

import * as React from "react";
import { Database } from "lucide-react";
import { toast } from "sonner";
import { EmptyState, ErrorState } from "@/components/common/page";
import { FileChip, FileDrop } from "@/components/common/file-drop";
import { Gate } from "@/components/rbac/gate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDatasets, useUploadDataset } from "@/lib/api/hooks";
import { cn, formatBytes, formatDate, formatNumber } from "@/lib/utils";

const ACCEPTED = [".jsonl", ".csv", ".parquet"];

export function StepDataset({ datasetId, onSelect }: { datasetId: string | null; onSelect: (id: string) => void }) {
  const datasets = useDatasets();
  const upload = useUploadDataset();
  const [file, setFile] = React.useState<File | null>(null);
  const [fileError, setFileError] = React.useState<string | null>(null);

  function pick(files: File[]) {
    const f = files[0]!;
    const ext = `.${f.name.split(".").pop()?.toLowerCase()}`;
    if (!ACCEPTED.includes(ext)) {
      setFile(null);
      setFileError(`Unsupported file type “${ext}”. Use JSONL, CSV or Parquet.`);
      return;
    }
    if (f.size > 2 * 1024 * 1024 * 1024) {
      setFile(null);
      setFileError("Files larger than 2 GB must be uploaded through object storage.");
      return;
    }
    setFileError(null);
    setFile(f);
  }

  async function doUpload() {
    if (!file) return;
    try {
      const ds = await upload.mutateAsync({ file, purpose: "finetune" });
      toast.success(`Uploaded ${ds.name}`, { description: `${formatNumber(ds.row_count ?? 0)} rows detected` });
      setFile(null);
      onSelect(ds.id);
    } catch (e) {
      setFileError(e instanceof Error ? e.message : "Upload failed");
    }
  }

  const finetuneSets = (datasets.data ?? []).filter((d) => d.purpose === "finetune");

  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <section className="lg:col-span-2" aria-labelledby="upload-h">
        <h3 id="upload-h" className="text-sm font-semibold">
          Upload a new dataset
        </h3>
        <p className="mb-3 mt-1 text-xs text-muted-foreground">
          Chat-format JSONL (<code className="font-mono">{"{\"messages\": [...]}"}</code>), or CSV/Parquet with prompt and completion columns.
        </p>
        <FileDrop id="dataset-file" accept={ACCEPTED.join(",")} onFiles={pick} hint="JSONL, CSV or Parquet · up to 2 GB" title="Drop a dataset or click to browse" />
        {file && (
          <div className="mt-3 space-y-3">
            <FileChip file={file} onClear={() => setFile(null)} />
            <Gate capability="manage_ml">
              <Button onClick={doUpload} loading={upload.isPending} className="w-full">
                Upload dataset
              </Button>
            </Gate>
          </div>
        )}
        {fileError && (
          <p role="alert" className="mt-3 text-sm text-destructive">
            {fileError}
          </p>
        )}
      </section>

      <section className="lg:col-span-3" aria-labelledby="existing-h">
        <h3 id="existing-h" className="text-sm font-semibold">
          Or choose an existing dataset
        </h3>
        <div className="mt-3 space-y-2" role="radiogroup" aria-labelledby="existing-h">
          {datasets.isLoading ? (
            [0, 1, 2].map((i) => <Skeleton key={i} className="h-16 w-full" />)
          ) : datasets.isError ? (
            <ErrorState error={datasets.error} onRetry={() => datasets.refetch()} />
          ) : finetuneSets.length === 0 ? (
            <EmptyState icon={Database} title="No datasets yet" description="Upload your first dataset to continue." />
          ) : (
            finetuneSets.map((d) => {
              const selected = d.id === datasetId;
              return (
                <button
                  key={d.id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => onSelect(d.id)}
                  className={cn(
                    "flex w-full items-center gap-4 rounded-lg border p-3 text-left transition-colors hover:bg-muted/40",
                    selected && "border-primary bg-accent/60 ring-1 ring-primary",
                  )}
                >
                  <span className={cn("flex h-4 w-4 shrink-0 items-center justify-center rounded-full border", selected ? "border-primary" : "border-input")}>
                    {selected && <span className="h-2 w-2 rounded-full bg-primary" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{d.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {formatNumber(d.row_count ?? 0)} rows · {formatNumber(d.token_count ?? 0)} tokens · {formatBytes(d.size_bytes ?? 0)} · {formatDate(d.created_at)}
                    </p>
                  </div>
                  <Badge variant="secondary" className="uppercase">
                    {d.format}
                  </Badge>
                </button>
              );
            })
          )}
        </div>
      </section>
    </div>
  );
}
