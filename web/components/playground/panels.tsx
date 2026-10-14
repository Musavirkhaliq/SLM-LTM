"use client";

import * as React from "react";
import { Braces, MessageSquare, Plus, Trash2, Wrench } from "lucide-react";
import { Field } from "@/components/common/page";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import type { OpenAIModel } from "@/lib/api/types";
import { EXAMPLE_TOOLS, parseTools, type Conversation, type PgSettings } from "@/lib/playground";
import { cn, timeAgo } from "@/lib/utils";

export function HistoryList({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onClear,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onClear: () => void;
}) {
  const sorted = [...conversations].sort((a, b) => b.updatedAt - a.updatedAt);
  return (
    <div className="flex h-full flex-col">
      <div className="p-3">
        <Button className="w-full justify-start" variant="outline" onClick={onNew}>
          <Plus /> New chat
        </Button>
      </div>
      <nav aria-label="Conversation history" className="flex-1 overflow-y-auto px-2 pb-2">
        {sorted.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">Your conversations are saved here, in this browser.</p>
        ) : (
          <ul className="space-y-0.5">
            {sorted.map((c) => (
              <li key={c.id} className="group relative">
                <button
                  type="button"
                  onClick={() => onSelect(c.id)}
                  aria-current={c.id === activeId ? "true" : undefined}
                  className={cn(
                    "flex w-full items-start gap-2 rounded-md px-3 py-2 pr-9 text-left text-sm transition-colors hover:bg-muted",
                    c.id === activeId && "bg-accent text-accent-foreground",
                  )}
                >
                  <MessageSquare className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{c.title}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {c.model} · {timeAgo(c.updatedAt)}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={`Delete conversation ${c.title}`}
                  onClick={() => onDelete(c.id)}
                  className="absolute right-1.5 top-2 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-background hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </nav>
      {sorted.length > 0 && (
        <div className="border-t p-2">
          <Button variant="ghost" size="sm" className="w-full text-muted-foreground" onClick={onClear}>
            Clear all history
          </Button>
        </div>
      )}
    </div>
  );
}

export function SettingsPanel({
  models,
  modelsLoading,
  model,
  onModel,
  settings,
  onChange,
  apiKey,
  onApiKey,
}: {
  models: OpenAIModel[];
  modelsLoading: boolean;
  model: string;
  onModel: (m: string) => void;
  settings: PgSettings;
  onChange: (patch: Partial<PgSettings>) => void;
  apiKey: string;
  onApiKey: (k: string) => void;
}) {
  const tools = parseTools(settings.toolsJson);
  const toolCount = tools.ok ? tools.tools.length : 0;
  return (
    <div className="space-y-5 p-4">
      <Field label="Model" htmlFor="pg-model">
        <Select value={model} onValueChange={onModel}>
          <SelectTrigger id="pg-model">
            <SelectValue placeholder={modelsLoading ? "Loading…" : "Select a model"} />
          </SelectTrigger>
          <SelectContent>
            {models.map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>

      <Field label="System prompt" htmlFor="pg-system">
        <Textarea id="pg-system" rows={4} value={settings.systemPrompt} onChange={(e) => onChange({ systemPrompt: e.target.value })} placeholder="You are a helpful assistant." />
      </Field>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label htmlFor="pg-temp" className="text-sm font-medium">
            Temperature
          </label>
          <span className="text-xs tabular-nums text-muted-foreground">{settings.temperature.toFixed(1)}</span>
        </div>
        <Slider id="pg-temp" aria-label="Temperature" min={0} max={2} step={0.1} value={[settings.temperature]} onValueChange={([v]) => onChange({ temperature: v! })} />
        <div className="flex justify-between text-[11px] text-muted-foreground">
          <span>Precise</span>
          <span>Creative</span>
        </div>
      </div>

      <Field label="Max tokens" htmlFor="pg-max">
        <Input id="pg-max" type="number" min={16} max={32768} value={settings.maxTokens} onChange={(e) => onChange({ maxTokens: Number(e.target.value) || 16 })} />
      </Field>

      <label className="flex items-center justify-between gap-3">
        <span className="flex items-start gap-2 text-sm">
          <Braces className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
          <span>
            JSON mode
            <span className="block text-xs text-muted-foreground">Force a valid JSON object response.</span>
          </span>
        </span>
        <Switch checked={settings.jsonMode} onCheckedChange={(v) => onChange({ jsonMode: v })} aria-label="JSON mode" />
      </label>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <label htmlFor="pg-tools" className="flex items-center gap-1.5 text-sm font-medium">
            <Wrench className="h-4 w-4 text-muted-foreground" aria-hidden /> Tools
            {toolCount > 0 && <span className="rounded-full bg-primary/10 px-1.5 text-[11px] text-primary">{toolCount}</span>}
          </label>
          <div className="flex gap-1">
            <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => onChange({ toolsJson: EXAMPLE_TOOLS })}>
              Example
            </Button>
            {settings.toolsJson && (
              <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => onChange({ toolsJson: "" })}>
                Clear
              </Button>
            )}
          </div>
        </div>
        <Textarea
          id="pg-tools"
          rows={8}
          spellCheck={false}
          className={cn("font-mono text-xs", !tools.ok && "border-destructive")}
          value={settings.toolsJson}
          aria-invalid={!tools.ok}
          aria-describedby="pg-tools-msg"
          placeholder={'[{"type":"function","function":{"name":"…","parameters":{…}}}]'}
          onChange={(e) => onChange({ toolsJson: e.target.value })}
        />
        <p id="pg-tools-msg" role={tools.ok ? undefined : "alert"} className={cn("text-xs", tools.ok ? "text-muted-foreground" : "text-destructive")}>
          {tools.ok ? "JSON schema of the functions the model may call." : tools.error}
        </p>
      </div>

      <details className="rounded-md border px-3 py-2 text-sm">
        <summary className="cursor-pointer text-xs font-medium text-muted-foreground">Authentication</summary>
        <div className="mt-2 space-y-1.5">
          <label htmlFor="pg-key" className="text-xs">
            API key override (optional)
          </label>
          <Input id="pg-key" type="password" autoComplete="off" placeholder="slm_…" value={apiKey} onChange={(e) => onApiKey(e.target.value)} />
          <p className="text-xs text-muted-foreground">By default requests use your signed-in session. A key is kept in memory only.</p>
        </div>
      </details>
    </div>
  );
}
