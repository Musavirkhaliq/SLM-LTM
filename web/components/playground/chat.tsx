"use client";

import * as React from "react";
import { AlertCircle, Bot, RefreshCw, SendHorizonal, Sparkles, Square, User, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import type { ToolCall } from "@/lib/api/types";
import type { PgMessage } from "@/lib/playground";
import { cn } from "@/lib/utils";
import { Markdown } from "./markdown";

const SUGGESTIONS = [
  "Summarize the benefits of small language models for enterprise use.",
  "Write a Python function that computes a moving average.",
  "Explain LoRA vs QLoRA in simple terms.",
  "What is the weather in Berlin today?",
];

function ToolCallCard({ call, answered, onSubmit, disabled }: { call: ToolCall; answered: boolean; onSubmit: (result: string) => void; disabled: boolean }) {
  const [result, setResult] = React.useState('{"temperature": 18, "unit": "celsius", "condition": "partly cloudy"}');
  let prettyArgs = call.function.arguments;
  try {
    prettyArgs = JSON.stringify(JSON.parse(call.function.arguments), null, 2);
  } catch {
    /* partial JSON while streaming */
  }
  return (
    <div className="mt-2 rounded-lg border bg-muted/40 p-3">
      <div className="flex items-center gap-2 text-xs font-medium">
        <Wrench className="h-3.5 w-3.5 text-primary" aria-hidden /> Tool call: <code className="font-mono">{call.function.name}</code>
      </div>
      <pre className="mt-2 overflow-x-auto rounded bg-background p-2 font-mono text-xs">{prettyArgs}</pre>
      {!answered && (
        <form
          className="mt-3 space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit(result);
          }}
        >
          <label htmlFor={`tool-result-${call.id}`} className="text-xs text-muted-foreground">
            Tool result (sent back to the model)
          </label>
          <Textarea id={`tool-result-${call.id}`} rows={3} className="font-mono text-xs" value={result} onChange={(e) => setResult(e.target.value)} />
          <Button type="submit" size="sm" disabled={disabled}>
            Send result
          </Button>
        </form>
      )}
    </div>
  );
}

function Message({
  m,
  isLast,
  streaming,
  answeredCalls,
  onToolResult,
  onRegenerate,
}: {
  m: PgMessage;
  isLast: boolean;
  streaming: boolean;
  answeredCalls: Set<string>;
  onToolResult: (call: ToolCall, result: string) => void;
  onRegenerate: () => void;
}) {
  const isUser = m.role === "user";
  const isTool = m.role === "tool";
  const live = isLast && streaming && m.role === "assistant";
  return (
    <article className={cn("flex gap-3", isUser && "flex-row-reverse")} aria-label={`${m.role} message`}>
      <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-full", isUser ? "bg-primary text-primary-foreground" : isTool ? "bg-secondary text-secondary-foreground" : "bg-accent text-accent-foreground")}>
        {isUser ? <User className="h-4 w-4" aria-hidden /> : isTool ? <Wrench className="h-4 w-4" aria-hidden /> : <Bot className="h-4 w-4" aria-hidden />}
      </div>
      <div className={cn("min-w-0 max-w-[85%] flex-1", isUser && "text-right")}>
        <div className={cn("inline-block rounded-2xl px-4 py-2.5 text-left", isUser ? "bg-primary text-primary-foreground" : "bg-card border shadow-sm")}>
          {isTool && <p className="mb-1 text-[11px] font-medium text-muted-foreground">Result of {m.toolName}</p>}
          {m.content ? (
            isUser ? <p className="whitespace-pre-wrap text-sm leading-relaxed">{m.content}</p> : isTool ? <pre className="whitespace-pre-wrap font-mono text-xs">{m.content}</pre> : <Markdown text={m.content} />
          ) : live && !m.toolCalls?.length ? (
            <span className="flex items-center gap-1 py-1" aria-label="Assistant is typing">
              {[0, 1, 2].map((i) => (
                <span key={i} className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-muted-foreground" style={{ animationDelay: `${i * 0.2}s` }} />
              ))}
            </span>
          ) : null}
          {live && m.content && <span className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse-dot bg-foreground/60" aria-hidden />}
          {m.toolCalls?.map((tc) => (
            <ToolCallCard key={tc.id || tc.function.name} call={tc} answered={answeredCalls.has(tc.id) || live} disabled={streaming} onSubmit={(r) => onToolResult(tc, r)} />
          ))}
        </div>
        {m.error && (
          <p role="alert" className="mt-1.5 flex items-center gap-1.5 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5" aria-hidden /> {m.error}
          </p>
        )}
        {m.stopped && <p className="mt-1.5 text-xs text-muted-foreground">Generation stopped.</p>}
        {m.role === "assistant" && m.stats && !live && (
          <p className="mt-1 flex flex-wrap items-center gap-x-3 text-[11px] text-muted-foreground">
            {m.stats.ttftMs !== undefined && <span>TTFT {m.stats.ttftMs} ms</span>}
            {m.stats.completionTokens !== undefined && <span>{m.stats.completionTokens} tokens</span>}
            {m.stats.tokensPerSecond !== undefined && <span>{m.stats.tokensPerSecond} tok/s</span>}
            {isLast && !streaming && (
              <button type="button" onClick={onRegenerate} className="inline-flex items-center gap-1 rounded hover:text-foreground">
                <RefreshCw className="h-3 w-3" aria-hidden /> Regenerate
              </button>
            )}
          </p>
        )}
      </div>
    </article>
  );
}

export function ChatView({
  messages,
  streaming,
  disabled,
  disabledReason,
  onSend,
  onStop,
  onToolResult,
  onRegenerate,
}: {
  messages: PgMessage[];
  streaming: boolean;
  disabled: boolean;
  disabledReason?: string;
  onSend: (text: string) => void;
  onStop: () => void;
  onToolResult: (call: ToolCall, result: string) => void;
  onRegenerate: () => void;
}) {
  const [text, setText] = React.useState("");
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const stickRef = React.useRef(true);
  const last = messages[messages.length - 1];

  React.useEffect(() => {
    const el = scrollRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages.length, last?.content, last?.toolCalls?.length]);

  const answeredCalls = React.useMemo(() => new Set(messages.filter((m) => m.role === "tool").map((m) => m.toolCallId ?? "")), [messages]);

  function submit() {
    const t = text.trim();
    if (!t || streaming || disabled) return;
    stickRef.current = true;
    setText("");
    onSend(t);
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        role="log"
        aria-live="polite"
        aria-label="Conversation"
        onScroll={(e) => {
          const el = e.currentTarget;
          stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
        className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4 py-6"
      >
        {messages.length === 0 ? (
          <div className="mx-auto flex max-w-xl flex-col items-center pt-10 text-center">
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-600 text-white">
              <Sparkles className="h-6 w-6" aria-hidden />
            </div>
            <h2 className="text-xl font-semibold tracking-tight">How can I help?</h2>
            <p className="mt-1 text-sm text-muted-foreground">Chat with any of your deployed models. Tune temperature, add tools or switch to JSON mode in the settings.</p>
            <div className="mt-6 grid w-full gap-2 sm:grid-cols-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  disabled={disabled}
                  onClick={() => onSend(s)}
                  className="rounded-xl border bg-card p-3 text-left text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-6">
            {messages.map((m, i) => (
              <Message key={m.id} m={m} isLast={i === messages.length - 1} streaming={streaming} answeredCalls={answeredCalls} onToolResult={onToolResult} onRegenerate={onRegenerate} />
            ))}
          </div>
        )}
      </div>

      <div className="border-t bg-background px-4 py-3">
        <form
          className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <label htmlFor="pg-input" className="sr-only">
            Message
          </label>
          <textarea
            id="pg-input"
            rows={1}
            value={text}
            disabled={disabled}
            placeholder={disabled ? (disabledReason ?? "Chat is unavailable") : "Send a message…  (Enter to send, Shift+Enter for a new line)"}
            onChange={(e) => {
              setText(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
            className="max-h-[200px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-0 focus-visible:ring-offset-0 disabled:cursor-not-allowed"
          />
          {streaming ? (
            <Button type="button" size="icon" variant="outline" onClick={onStop} aria-label="Stop generating">
              <Square className="fill-current" />
            </Button>
          ) : (
            <Button type="submit" size="icon" disabled={!text.trim() || disabled} aria-label="Send message">
              <SendHorizonal />
            </Button>
          )}
        </form>
      </div>
    </div>
  );
}
