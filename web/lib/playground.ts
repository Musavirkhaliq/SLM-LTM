import type { ChatCompletionRequest, ChatMessage, ToolCall, ToolDefinition } from "@/lib/api/types";
import { uid } from "@/lib/utils";

export interface PgSettings {
  temperature: number;
  maxTokens: number;
  systemPrompt: string;
  jsonMode: boolean;
  toolsJson: string;
}

export interface PgStats {
  ttftMs?: number;
  completionTokens?: number;
  tokensPerSecond?: number;
  totalMs?: number;
}

export interface PgMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  toolCalls?: ToolCall[];
  toolCallId?: string;
  toolName?: string;
  stats?: PgStats;
  error?: string;
  stopped?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  model: string;
  createdAt: number;
  updatedAt: number;
  messages: PgMessage[];
  settings: PgSettings;
}

export const DEFAULT_SETTINGS: PgSettings = {
  temperature: 0.7,
  maxTokens: 1024,
  systemPrompt: "You are a helpful assistant.",
  jsonMode: false,
  toolsJson: "",
};

export const EXAMPLE_TOOLS = JSON.stringify(
  [
    {
      type: "function",
      function: {
        name: "get_weather",
        description: "Get the current weather for a city",
        parameters: {
          type: "object",
          properties: {
            location: { type: "string", description: "City name, e.g. Berlin" },
            unit: { type: "string", enum: ["celsius", "fahrenheit"] },
          },
          required: ["location"],
        },
      },
    },
  ],
  null,
  2,
);

export function newConversation(model: string, settings: PgSettings = DEFAULT_SETTINGS): Conversation {
  const now = Date.now();
  return { id: uid("conv"), title: "New chat", model, createdAt: now, updatedAt: now, messages: [], settings: { ...settings } };
}

export function titleFrom(text: string): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > 44 ? `${t.slice(0, 44)}…` : t || "New chat";
}

/* ------------------------------------------------------------- tools JSON --- */
export type ToolsParse = { ok: true; tools: ToolDefinition[] } | { ok: false; error: string };

export function parseTools(src: string): ToolsParse {
  if (!src.trim()) return { ok: true, tools: [] };
  let parsed: unknown;
  try {
    parsed = JSON.parse(src);
  } catch (e) {
    return { ok: false, error: `Invalid JSON: ${e instanceof Error ? e.message : "parse error"}` };
  }
  if (!Array.isArray(parsed)) return { ok: false, error: "Tools must be a JSON array of tool definitions." };
  for (let i = 0; i < parsed.length; i++) {
    const t = parsed[i] as Partial<ToolDefinition> | null;
    if (!t || t.type !== "function") return { ok: false, error: `Tool ${i + 1}: "type" must be "function".` };
    if (!t.function || typeof t.function.name !== "string" || !/^[a-zA-Z0-9_-]{1,64}$/.test(t.function.name)) {
      return { ok: false, error: `Tool ${i + 1}: "function.name" must be 1-64 letters, numbers, "_" or "-".` };
    }
    if (t.function.parameters !== undefined && (typeof t.function.parameters !== "object" || t.function.parameters === null)) {
      return { ok: false, error: `Tool ${i + 1}: "function.parameters" must be a JSON Schema object.` };
    }
  }
  return { ok: true, tools: parsed as ToolDefinition[] };
}

/* ------------------------------------------------------- request builder --- */
export function toWireMessages(history: PgMessage[], systemPrompt: string): ChatMessage[] {
  const out: ChatMessage[] = [];
  if (systemPrompt.trim()) out.push({ role: "system", content: systemPrompt.trim() });
  for (const m of history) {
    if (m.role === "user") out.push({ role: "user", content: m.content });
    else if (m.role === "assistant") {
      if (!m.content && !m.toolCalls?.length) continue; // skip empty / failed turns
      out.push({ role: "assistant", content: m.content || null, ...(m.toolCalls?.length ? { tool_calls: m.toolCalls } : {}) });
    } else out.push({ role: "tool", content: m.content, tool_call_id: m.toolCallId, name: m.toolName });
  }
  return out;
}

export function buildRequest(model: string, history: PgMessage[], s: PgSettings): ChatCompletionRequest {
  const tools = parseTools(s.toolsJson);
  return {
    model,
    messages: toWireMessages(history, s.systemPrompt),
    temperature: s.temperature,
    max_tokens: s.maxTokens,
    stream: true,
    ...(s.jsonMode ? { response_format: { type: "json_object" as const } } : {}),
    ...(tools.ok && tools.tools.length ? { tools: tools.tools } : {}),
  };
}

export function toCurl(baseUrl: string, req: ChatCompletionRequest): string {
  const body = JSON.stringify(req, null, 2).replace(/'/g, "'\\''");
  return `curl ${baseUrl}/v1/chat/completions \\\n  -H "Authorization: Bearer $SLM_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${body}'`;
}

/* ---------------------------------------------------------- persistence --- */
const KEY = "slm.playground.v1";

export interface PersistedState {
  conversations: Conversation[];
  activeId: string | null;
}

export function loadState(storage: Pick<Storage, "getItem"> | null): PersistedState {
  try {
    const raw = storage?.getItem(KEY);
    if (!raw) return { conversations: [], activeId: null };
    const parsed = JSON.parse(raw) as PersistedState;
    if (!Array.isArray(parsed.conversations)) return { conversations: [], activeId: null };
    return {
      conversations: parsed.conversations.filter((c) => c && typeof c.id === "string" && Array.isArray(c.messages)),
      activeId: parsed.activeId ?? null,
    };
  } catch {
    return { conversations: [], activeId: null };
  }
}

export function saveState(storage: Pick<Storage, "setItem"> | null, state: PersistedState): boolean {
  try {
    // keep the most recent 50 conversations; drop transient flags
    const trimmed = [...state.conversations].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 50);
    storage?.setItem(KEY, JSON.stringify({ conversations: trimmed, activeId: state.activeId }));
    return true;
  } catch {
    return false;
  }
}

export const STORAGE_KEY = KEY;
