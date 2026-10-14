import type { RagIndexCreateInput, VectorBackend } from "@/lib/api/types";

export const EMBEDDING_MODELS = [
  { name: "bge-m3", dim: 1024, note: "Multilingual, 8K context" },
  { name: "bge-small-en-v1.5", dim: 384, note: "Fast, English" },
  { name: "nomic-embed-text-v1.5", dim: 768, note: "Long context, Matryoshka" },
  { name: "e5-large-v2", dim: 1024, note: "Strong English retrieval" },
] as const;

export const RERANKERS = ["none", "bge-reranker-v2-m3", "ms-marco-MiniLM-L-12-v2"] as const;
export const BACKENDS: { id: VectorBackend; label: string }[] = [
  { id: "qdrant", label: "Qdrant" },
  { id: "weaviate", label: "Weaviate" },
  { id: "milvus", label: "Milvus" },
];

export type NodeKind = "upload" | "chunking" | "embeddings" | "vectordb" | "retrieval" | "model";
export const NODE_ORDER: NodeKind[] = ["upload", "chunking", "embeddings", "vectordb", "retrieval", "model"];

export interface RagConfig {
  upload: { formats: string[]; ocr: boolean; maxSizeMb: number };
  chunking: { strategy: "recursive" | "fixed" | "semantic" | "markdown"; size: number; overlap: number };
  embeddings: { model: string; dim: number; normalize: boolean };
  vectordb: { backend: VectorBackend; name: string; hybrid: boolean; metric: "cosine" | "dot" | "euclid" };
  retrieval: { topK: number; threshold: number; reranker: string; mmr: boolean };
  model: { alias: string; temperature: number; systemPrompt: string };
}

export const DEFAULT_RAG_CONFIG: RagConfig = {
  upload: { formats: ["pdf", "docx", "md", "html", "txt"], ocr: false, maxSizeMb: 50 },
  chunking: { strategy: "recursive", size: 512, overlap: 64 },
  embeddings: { model: "bge-m3", dim: 1024, normalize: true },
  vectordb: { backend: "qdrant", name: "knowledge-base", hybrid: true, metric: "cosine" },
  retrieval: { topK: 5, threshold: 0.3, reranker: "bge-reranker-v2-m3", mmr: false },
  model: {
    alias: "phi-4-mini",
    temperature: 0.2,
    systemPrompt: "Answer using only the provided context. If the answer is not in the context, say you don't know. Cite the source document.",
  },
};

export interface ValidationIssue {
  node: NodeKind;
  level: "error" | "warning";
  message: string;
}

export function validatePipeline(c: RagConfig): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  if (c.upload.formats.length === 0) issues.push({ node: "upload", level: "error", message: "Select at least one document format." });
  if (c.chunking.size < 64 || c.chunking.size > 8192) issues.push({ node: "chunking", level: "error", message: "Chunk size must be between 64 and 8192 tokens." });
  if (c.chunking.overlap < 0 || c.chunking.overlap >= c.chunking.size) issues.push({ node: "chunking", level: "error", message: "Overlap must be smaller than the chunk size." });
  else if (c.chunking.overlap > c.chunking.size * 0.5) issues.push({ node: "chunking", level: "warning", message: "Overlap above 50% of the chunk size inflates the index." });
  const emb = EMBEDDING_MODELS.find((m) => m.name === c.embeddings.model);
  if (!emb) issues.push({ node: "embeddings", level: "error", message: "Unknown embedding model." });
  else if (emb.dim !== c.embeddings.dim) issues.push({ node: "embeddings", level: "error", message: `${emb.name} produces ${emb.dim}-dimensional vectors.` });
  if (!/^[a-z0-9][a-z0-9-]{1,62}$/.test(c.vectordb.name)) issues.push({ node: "vectordb", level: "error", message: "Index name: 2-63 lowercase letters, numbers or dashes." });
  if (c.retrieval.topK < 1 || c.retrieval.topK > 50) issues.push({ node: "retrieval", level: "error", message: "Top-K must be between 1 and 50." });
  if (c.retrieval.reranker !== "none" && c.retrieval.topK < 3) issues.push({ node: "retrieval", level: "warning", message: "A reranker works best with a Top-K of 3 or more." });
  if (c.model.temperature < 0 || c.model.temperature > 2) issues.push({ node: "model", level: "error", message: "Temperature must be between 0 and 2." });
  if (!c.model.alias) issues.push({ node: "model", level: "error", message: "Choose a generation model." });
  return issues;
}

export function toIndexInput(c: RagConfig): RagIndexCreateInput {
  return {
    name: c.vectordb.name,
    backend: c.vectordb.backend,
    embedding_model: c.embeddings.model,
    embedding_dim: c.embeddings.dim,
    chunk_size: c.chunking.size,
    chunk_overlap: c.chunking.overlap,
    hybrid_search: c.vectordb.hybrid,
    reranker_model: c.retrieval.reranker === "none" ? null : c.retrieval.reranker,
  };
}

export function summarize(kind: NodeKind, c: RagConfig): string {
  switch (kind) {
    case "upload":
      return `${c.upload.formats.map((f) => f.toUpperCase()).join(", ") || "no formats"}`;
    case "chunking":
      return `${c.chunking.strategy} · ${c.chunking.size}/${c.chunking.overlap}`;
    case "embeddings":
      return `${c.embeddings.model} · ${c.embeddings.dim}d`;
    case "vectordb":
      return `${c.vectordb.backend}${c.vectordb.hybrid ? " · hybrid" : ""}`;
    case "retrieval":
      return `top-${c.retrieval.topK}${c.retrieval.reranker !== "none" ? " + rerank" : ""}`;
    case "model":
      return `${c.model.alias} · t=${c.model.temperature}`;
  }
}
