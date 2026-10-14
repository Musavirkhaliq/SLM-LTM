import type { ChatCompletionChunk } from "./types";

/**
 * Parse a Server-Sent-Events byte stream and yield the `data:` payload of each event.
 * Handles CRLF/LF, multi-line data fields, comment lines, events split across chunk
 * boundaries and the OpenAI `[DONE]` sentinel (which terminates the iteration).
 */
export async function* parseSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<string, void, void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushEvent = (raw: string): string | null => {
    const dataLines: string[] = [];
    for (const line of raw.split("\n")) {
      if (line === "" || line.startsWith(":")) continue;
      const idx = line.indexOf(":");
      const field = idx === -1 ? line : line.slice(0, idx);
      let value = idx === -1 ? "" : line.slice(idx + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "data") dataLines.push(value);
    }
    return dataLines.length ? dataLines.join("\n") : null;
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const data = flushEvent(raw);
        if (data !== null) {
          if (data.trim() === "[DONE]") return;
          yield data;
        }
        sep = buffer.indexOf("\n\n");
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) {
      const data = flushEvent(buffer.replace(/\r\n/g, "\n"));
      if (data !== null && data.trim() !== "[DONE]") yield data;
    }
  } finally {
    reader.releaseLock();
  }
}

/** Parse an OpenAI-style streaming chat response into typed chunks. */
export async function* parseChatStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<ChatCompletionChunk, void, void> {
  for await (const data of parseSSE(stream)) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      continue; // ignore keep-alive / malformed frames rather than aborting the whole stream
    }
    if (parsed && typeof parsed === "object" && "error" in parsed) {
      const err = (parsed as { error: { message?: string } }).error;
      throw new Error(err?.message ?? "Upstream stream error");
    }
    yield parsed as ChatCompletionChunk;
  }
}

export interface ChatAccumulator {
  content: string;
  toolCalls: { id: string; name: string; arguments: string }[];
  finishReason: string | null;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number } | null;
}

export function newAccumulator(): ChatAccumulator {
  return { content: "", toolCalls: [], finishReason: null, usage: null };
}

/** Fold a streamed chunk into the running accumulator (content, tool-call fragments, usage). */
export function applyChunk(acc: ChatAccumulator, chunk: ChatCompletionChunk): ChatAccumulator {
  const choice = chunk.choices?.[0];
  if (choice) {
    const delta = choice.delta ?? {};
    if (typeof delta.content === "string") acc.content += delta.content;
    for (const tc of delta.tool_calls ?? []) {
      const slot = (acc.toolCalls[tc.index] ??= { id: "", name: "", arguments: "" });
      if (tc.id) slot.id = tc.id;
      if (tc.function?.name) slot.name += tc.function.name;
      if (tc.function?.arguments) slot.arguments += tc.function.arguments;
    }
    if (choice.finish_reason) acc.finishReason = choice.finish_reason;
  }
  if (chunk.usage) acc.usage = chunk.usage;
  return acc;
}
