"use client";

import * as React from "react";
import { FileText, Search } from "lucide-react";
import { toast } from "sonner";
import { FileDrop } from "@/components/common/file-drop";
import { EmptyState, ErrorState, Field, TableSkeleton } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { useCan } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useRagDocuments, useRagIndexes, useRagQuery, useUploadRagDocument } from "@/lib/api/hooks";
import { formatBytes, timeAgo } from "@/lib/utils";

const ACCEPT = ".pdf,.docx,.md,.html,.txt,.csv";

export function DocumentsTab({ indexId, onIndexChange }: { indexId: string | null; onIndexChange: (id: string) => void }) {
  const indexes = useRagIndexes();
  const canUpload = useCan("manage_ml");
  const selected = indexId ?? indexes.data?.find((i) => i.status === "ready")?.id ?? indexes.data?.[0]?.id ?? null;
  const docs = useRagDocuments(selected);
  const upload = useUploadRagDocument();
  const query = useRagQuery();
  const [q, setQ] = React.useState("");
  const [uploading, setUploading] = React.useState(0);

  async function onFiles(files: File[]) {
    if (!selected) return;
    setUploading((n) => n + files.length);
    for (const file of files) {
      try {
        await upload.mutateAsync({ indexId: selected, file });
        toast.success(`Uploaded ${file.name}`, { description: `${formatBytes(file.size)} · queued for chunking and embedding` });
      } catch (e) {
        toast.error(`${file.name}: ${e instanceof Error ? e.message : "upload failed"}`);
      } finally {
        setUploading((n) => n - 1);
      }
    }
  }

  if (indexes.isLoading) return <Card><TableSkeleton rows={4} cols={4} /></Card>;
  if (indexes.isError) return <ErrorState error={indexes.error} onRetry={() => indexes.refetch()} />;
  if (!indexes.data?.length || !selected) return <EmptyState icon={FileText} title="Create an index first" description="Documents are uploaded into an index. Build one in the pipeline builder." />;

  const index = indexes.data.find((i) => i.id === selected);

  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <div className="space-y-4 lg:col-span-3">
        <Card className="p-5">
          <div className="grid gap-4 sm:grid-cols-[240px_1fr]">
            <Field label="Index" htmlFor="doc-index">
              <Select value={selected} onValueChange={onIndexChange}>
                <SelectTrigger id="doc-index">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {indexes.data.map((i) => (
                    <SelectItem key={i.id} value={i.id}>
                      {i.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <div className="self-end text-xs text-muted-foreground">
              {index && (
                <p>
                  {index.embedding_model} · chunks of {index.chunk_size} tokens ({index.chunk_overlap} overlap) · {index.hybrid_search ? "hybrid" : "dense"} retrieval
                </p>
              )}
            </div>
          </div>
          <div className="mt-4">
            <FileDrop
              id="rag-files"
              multiple
              accept={ACCEPT}
              onFiles={onFiles}
              disabled={!canUpload || index?.status !== "ready"}
              title={canUpload ? "Drop documents here or click to browse" : "Your role cannot upload documents"}
              hint={index?.status !== "ready" ? "The index is still being created…" : "PDF, DOCX, Markdown, HTML, TXT or CSV"}
            />
            {uploading > 0 && <p className="mt-2 text-xs text-muted-foreground" role="status">Uploading {uploading} file{uploading > 1 ? "s" : ""}…</p>}
          </div>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Documents</CardTitle>
            <CardDescription>Ingestion status for {index?.name}</CardDescription>
          </CardHeader>
          <CardContent className="px-0 pb-2">
            {docs.isLoading ? (
              <TableSkeleton rows={4} cols={4} />
            ) : !docs.data?.length ? (
              <EmptyState icon={FileText} title="No documents" description="Upload documents above to populate this index." className="mx-5 mb-3" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>File</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Chunks</TableHead>
                    <TableHead>Added</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {docs.data.map((d) => (
                    <TableRow key={d.id}>
                      <TableCell className="font-medium">{d.filename}</TableCell>
                      <TableCell>
                        <StatusBadge status={d.status} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{d.status === "succeeded" ? d.chunk_count : "—"}</TableCell>
                      <TableCell className="text-muted-foreground">{timeAgo(d.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="h-fit lg:col-span-2">
        <CardHeader>
          <CardTitle>Test retrieval</CardTitle>
          <CardDescription>Run a query against this index and inspect the matched chunks.</CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex gap-2"
            onSubmit={async (e) => {
              e.preventDefault();
              if (!q.trim() || !selected) return;
              try {
                await query.mutateAsync({ indexId: selected, query: q, top_k: 4 });
              } catch (err) {
                toast.error(err instanceof Error ? err.message : "Query failed");
              }
            }}
          >
            <Input aria-label="Retrieval query" placeholder="e.g. How do rate limits work?" value={q} onChange={(e) => setQ(e.target.value)} />
            <Button type="submit" loading={query.isPending} aria-label="Run query">
              <Search />
            </Button>
          </form>
          <div className="mt-4 space-y-3" aria-live="polite">
            {query.data && (
              <>
                <p className="text-xs text-muted-foreground">
                  {query.data.matches.length} match{query.data.matches.length === 1 ? "" : "es"} in {query.data.latency_ms} ms
                </p>
                {query.data.matches.length === 0 && <p className="text-sm text-muted-foreground">Nothing above the score threshold.</p>}
                {query.data.matches.map((m) => (
                  <div key={m.chunk_id} className="rounded-lg border p-3">
                    <div className="mb-1 flex items-center justify-between gap-2 text-xs">
                      <span className="truncate font-medium">{m.document}</span>
                      <span className="shrink-0 rounded-full bg-success/15 px-2 py-0.5 font-medium tabular-nums text-success">{m.score.toFixed(2)}</span>
                    </div>
                    <p className="text-sm text-muted-foreground">{m.text}</p>
                  </div>
                ))}
              </>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
