import type { ChatCompletionChunk, ChatCompletionRequest, ChatMessage, ToolDefinition } from "@/lib/api/types";
import { sleep } from "./rand";

export interface MockReply {
  content: string;
  toolCall?: { name: string; arguments: string };
}

function lastUser(messages: ChatMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]!;
    if (m.role === "user" && m.content) return m.content;
  }
  return "";
}

function sampleArgs(tool: ToolDefinition, prompt: string): string {
  const props = (tool.function.parameters?.properties ?? {}) as Record<string, { type?: string; enum?: unknown[] }>;
  const args: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(props)) {
    if (Array.isArray(spec.enum) && spec.enum.length) args[key] = spec.enum[0];
    else if (spec.type === "number" || spec.type === "integer") args[key] = 1;
    else if (spec.type === "boolean") args[key] = true;
    else {
      const m = prompt.match(/\bin ([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)?)/);
      args[key] = key.toLowerCase().includes("loc") || key.toLowerCase().includes("city") ? (m?.[1] ?? "Berlin") : prompt.slice(0, 40);
    }
  }
  return JSON.stringify(args);
}

/** Pure function producing the canned assistant reply for a request (used by the stream + tests). */
export function buildMockReply(req: ChatCompletionRequest): MockReply {
  const last = req.messages[req.messages.length - 1];
  const prompt = lastUser(req.messages);
  const lower = prompt.toLowerCase();

  if (last?.role === "tool") {
    return {
      content: `Thanks - the \`${last.name ?? "tool"}\` call returned:\n\n\`\`\`json\n${last.content ?? "{}"}\n\`\`\`\n\nBased on that result, here is a short summary for you: the data looks consistent and the request completed successfully.`,
    };
  }

  if (req.tools?.length) {
    const tool =
      req.tools.find((t) => lower.includes(t.function.name.toLowerCase().replace(/_/g, " "))) ??
      req.tools.find((t) => t.function.name.split("_").some((p) => p.length > 3 && lower.includes(p.toLowerCase()))) ??
      (/\b(weather|temperature|forecast|lookup|search|price|order|ticket|calculate)\b/.test(lower) ? req.tools[0] : undefined);
    if (tool) return { content: "", toolCall: { name: tool.function.name, arguments: sampleArgs(tool, prompt) } };
  }

  if (req.response_format?.type === "json_object") {
    return {
      content: JSON.stringify(
        {
          answer: prompt ? `Response to: ${prompt.slice(0, 80)}` : "Hello",
          model: req.model,
          confidence: 0.92,
          tags: ["demo", "json-mode"],
        },
        null,
        2,
      ),
    };
  }

  if (/\b(code|function|python|typescript|javascript|sql|script|regex|implement)\b/.test(lower)) {
    return {
      content: `Here is a concise implementation:\n\n\`\`\`python\ndef moving_average(values, window=3):\n    """Simple moving average over a list of numbers."""\n    if window <= 0:\n        raise ValueError("window must be positive")\n    out = []\n    for i in range(len(values)):\n        chunk = values[max(0, i - window + 1): i + 1]\n        out.append(sum(chunk) / len(chunk))\n    return out\n\`\`\`\n\nIt runs in O(n * window) time; use a running sum if you need O(n). Want me to add tests?`,
    };
  }

  if (/\b(hello|hi|hey)\b/.test(lower) && lower.length < 24) {
    return { content: `Hello! I'm **${req.model}**, running on SLM Studio. How can I help you today?` };
  }

  if (/\b(summar|tl;dr|shorten)/.test(lower)) {
    return {
      content: `**Summary**\n\n- The main point is stated up front and supported by two key facts.\n- Risks are limited and mostly operational.\n- Recommended next step: validate on a small sample, then roll out gradually.\n\nLet me know if you'd like a longer version.`,
    };
  }

  const temp = req.temperature ?? 1;
  const style = temp > 1.1 ? "I'll take a more exploratory angle here." : temp < 0.3 ? "I'll keep this precise and deterministic." : "Here's a balanced answer.";
  return {
    content: `${style}\n\nSmall language models like **${req.model}** are well suited to focused, high-volume tasks: they are cheaper to serve, respond with lower latency, and can be fine-tuned on your own data with LoRA or QLoRA.\n\nFor "${prompt.slice(0, 90)}${prompt.length > 90 ? "…" : ""}" a good approach is:\n\n1. Start from a strong base model and measure it on your evaluation set.\n2. Add retrieval (RAG) for facts that change frequently.\n3. Fine-tune only if the gap remains, then compare versions side by side.\n\nWould you like me to go deeper on any of these steps?`,
  };
}

function tokenize(text: string): string[] {
  return text.match(/\s*\S+|\s+/g) ?? [];
}

export function estimateTokens(text: string): number {
  return Math.max(1, Math.round(text.length / 4));
}

/**
 * Build an OpenAI-compatible SSE stream (byte stream) for a chat completion request, with
 * realistic pacing. It is parsed by the exact same `parseChatStream` used for the real gateway.
 */
export function createMockChatStream(req: ChatCompletionRequest, signal?: AbortSignal | null): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  const id = `chatcmpl-${Math.random().toString(36).slice(2, 12)}`;
  const created = Math.floor(Date.now() / 1000);
  const reply = buildMockReply(req);
  const promptTokens = estimateTokens(req.messages.map((m) => m.content ?? "").join("\n"));
  const send = (ctrl: ReadableStreamDefaultController<Uint8Array>, chunk: Partial<ChatCompletionChunk>) =>
    ctrl.enqueue(
      enc.encode(
        `data: ${JSON.stringify({ id, object: "chat.completion.chunk", created, model: req.model, choices: [], ...chunk })}\n\n`,
      ),
    );

  return new ReadableStream<Uint8Array>({
    async start(ctrl) {
      try {
        await sleep(280 + Math.random() * 220, signal ?? undefined); // time-to-first-token
        send(ctrl, { choices: [{ index: 0, delta: { role: "assistant", content: "" }, finish_reason: null }] });

        let completion = 0;
        if (reply.toolCall) {
          const callId = `call_${Math.random().toString(36).slice(2, 10)}`;
          send(ctrl, {
            choices: [
              {
                index: 0,
                delta: { tool_calls: [{ index: 0, id: callId, type: "function", function: { name: reply.toolCall.name, arguments: "" } }] },
                finish_reason: null,
              },
            ],
          });
          const parts = reply.toolCall.arguments.match(/.{1,8}/g) ?? [];
          for (const part of parts) {
            await sleep(18, signal ?? undefined);
            send(ctrl, { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: part } }] }, finish_reason: null }] });
            completion++;
          }
          send(ctrl, { choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }] });
        } else {
          for (const tok of tokenize(reply.content)) {
            await sleep(14 + Math.random() * 26, signal ?? undefined);
            send(ctrl, { choices: [{ index: 0, delta: { content: tok }, finish_reason: null }] });
            completion++;
          }
          send(ctrl, { choices: [{ index: 0, delta: {}, finish_reason: "stop" }] });
        }
        send(ctrl, { usage: { prompt_tokens: promptTokens, completion_tokens: completion, total_tokens: promptTokens + completion } });
        ctrl.enqueue(enc.encode("data: [DONE]\n\n"));
        ctrl.close();
      } catch (e) {
        try {
          ctrl.error(e);
        } catch {
          /* already closed */
        }
      }
    },
  });
}
