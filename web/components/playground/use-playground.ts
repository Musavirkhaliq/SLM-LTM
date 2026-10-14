"use client";

import * as React from "react";
import { apiClient } from "@/lib/api";
import { applyChunk, newAccumulator } from "@/lib/api/sse";
import type { ToolCall } from "@/lib/api/types";
import {
  buildRequest,
  DEFAULT_SETTINGS,
  loadState,
  newConversation,
  saveState,
  titleFrom,
  type Conversation,
  type PersistedState,
  type PgMessage,
  type PgSettings,
} from "@/lib/playground";
import { uid } from "@/lib/utils";

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function usePlayground(defaultModel: string) {
  const [state, setState] = React.useState<PersistedState>({ conversations: [], activeId: null });
  const [hydrated, setHydrated] = React.useState(false);
  const [streaming, setStreaming] = React.useState(false);
  const [apiKey, setApiKey] = React.useState("");
  const abortRef = React.useRef<AbortController | null>(null);
  const stateRef = React.useRef(state);
  stateRef.current = state;
  const apiKeyRef = React.useRef(apiKey);
  apiKeyRef.current = apiKey;

  // hydrate from localStorage after mount (avoids SSR mismatch)
  React.useEffect(() => {
    const loaded = loadState(storage());
    setState(loaded);
    setHydrated(true);
  }, []);

  // debounced persistence
  React.useEffect(() => {
    if (!hydrated) return;
    const t = setTimeout(() => saveState(storage(), state), 350);
    return () => clearTimeout(t);
  }, [state, hydrated]);

  // abort any in-flight stream on unmount
  React.useEffect(() => () => abortRef.current?.abort(), []);

  const active = state.conversations.find((c) => c.id === state.activeId) ?? null;

  const patchConversation = React.useCallback((id: string, fn: (c: Conversation) => Conversation) => {
    setState((s) => ({ ...s, conversations: s.conversations.map((c) => (c.id === id ? fn(c) : c)) }));
  }, []);

  const patchMessage = React.useCallback(
    (convId: string, msgId: string, patch: Partial<PgMessage>) =>
      patchConversation(convId, (c) => ({ ...c, updatedAt: Date.now(), messages: c.messages.map((m) => (m.id === msgId ? { ...m, ...patch } : m)) })),
    [patchConversation],
  );

  const newChat = React.useCallback(() => {
    const prev = stateRef.current.conversations.find((c) => c.id === stateRef.current.activeId);
    const conv = newConversation(prev?.model ?? defaultModel, prev?.settings ?? DEFAULT_SETTINGS);
    setState((s) => ({ conversations: [conv, ...s.conversations], activeId: conv.id }));
    return conv;
  }, [defaultModel]);

  const selectChat = React.useCallback((id: string) => setState((s) => ({ ...s, activeId: id })), []);

  const deleteChat = React.useCallback((id: string) => {
    setState((s) => {
      const rest = s.conversations.filter((c) => c.id !== id);
      return { conversations: rest, activeId: s.activeId === id ? (rest[0]?.id ?? null) : s.activeId };
    });
  }, []);

  const clearAll = React.useCallback(() => setState({ conversations: [], activeId: null }), []);

  const setSettings = React.useCallback(
    (patch: Partial<PgSettings>) => {
      const id = stateRef.current.activeId;
      if (!id) return;
      patchConversation(id, (c) => ({ ...c, settings: { ...c.settings, ...patch } }));
    },
    [patchConversation],
  );

  const setModel = React.useCallback(
    (model: string) => {
      const id = stateRef.current.activeId;
      if (id) patchConversation(id, (c) => ({ ...c, model }));
    },
    [patchConversation],
  );

  const generate = React.useCallback(
    async (convId: string, history: PgMessage[]) => {
      const conv = stateRef.current.conversations.find((c) => c.id === convId);
      if (!conv) return;
      const assistantId = uid("msg");
      patchConversation(convId, (c) => ({ ...c, messages: [...history, { id: assistantId, role: "assistant", content: "" }], updatedAt: Date.now() }));
      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);
      const t0 = performance.now();
      let firstAt: number | null = null;
      const acc = newAccumulator();
      try {
        const req = buildRequest(conv.model, history, conv.settings);
        for await (const chunk of apiClient.chatStream(req, { signal: controller.signal, apiKey: apiKeyRef.current.trim() || undefined })) {
          const delta = chunk.choices?.[0]?.delta;
          if (firstAt === null && (delta?.content || delta?.tool_calls?.length)) firstAt = performance.now();
          applyChunk(acc, chunk);
          const toolCalls: ToolCall[] = acc.toolCalls.map((t) => ({ id: t.id, type: "function", function: { name: t.name, arguments: t.arguments } }));
          patchMessage(convId, assistantId, { content: acc.content, toolCalls: toolCalls.length ? toolCalls : undefined });
        }
        const end = performance.now();
        const tokens = acc.usage?.completion_tokens;
        patchMessage(convId, assistantId, {
          stats: {
            ttftMs: firstAt ? Math.round(firstAt - t0) : undefined,
            completionTokens: tokens,
            totalMs: Math.round(end - t0),
            tokensPerSecond: tokens && firstAt && end > firstAt ? +(tokens / ((end - firstAt) / 1000)).toFixed(1) : undefined,
          },
        });
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") patchMessage(convId, assistantId, { stopped: true });
        else patchMessage(convId, assistantId, { error: e instanceof Error ? e.message : "The request failed" });
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [patchConversation, patchMessage],
  );

  const send = React.useCallback(
    async (text: string) => {
      let conv = stateRef.current.conversations.find((c) => c.id === stateRef.current.activeId);
      if (!conv) conv = newChat();
      const convId = conv.id;
      const user: PgMessage = { id: uid("msg"), role: "user", content: text };
      const history = [...conv.messages, user];
      patchConversation(convId, (c) => ({ ...c, title: c.messages.length === 0 ? titleFrom(text) : c.title, messages: history }));
      // stateRef is only refreshed on render; make sure generate() sees the new conversation
      stateRef.current = {
        ...stateRef.current,
        conversations: stateRef.current.conversations.some((c) => c.id === convId)
          ? stateRef.current.conversations.map((c) => (c.id === convId ? { ...c, messages: history } : c))
          : [{ ...conv, messages: history }, ...stateRef.current.conversations],
        activeId: convId,
      };
      await generate(convId, history);
    },
    [generate, newChat, patchConversation],
  );

  const submitToolResult = React.useCallback(
    async (call: ToolCall, result: string) => {
      const conv = stateRef.current.conversations.find((c) => c.id === stateRef.current.activeId);
      if (!conv) return;
      const toolMsg: PgMessage = { id: uid("msg"), role: "tool", content: result, toolCallId: call.id, toolName: call.function.name };
      await generate(conv.id, [...conv.messages, toolMsg]);
    },
    [generate],
  );

  const regenerate = React.useCallback(async () => {
    const conv = stateRef.current.conversations.find((c) => c.id === stateRef.current.activeId);
    if (!conv) return;
    const msgs = [...conv.messages];
    while (msgs.length && msgs[msgs.length - 1]!.role === "assistant") msgs.pop();
    if (!msgs.length) return;
    await generate(conv.id, msgs);
  }, [generate]);

  const stop = React.useCallback(() => abortRef.current?.abort(), []);

  return { state, hydrated, active, streaming, apiKey, setApiKey, newChat, selectChat, deleteChat, clearAll, setSettings, setModel, send, submitToolResult, regenerate, stop };
}
