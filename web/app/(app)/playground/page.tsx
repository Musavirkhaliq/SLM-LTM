"use client";

import * as React from "react";
import { History, SlidersHorizontal, Terminal } from "lucide-react";
import { toast } from "sonner";
import { copyText } from "@/components/common/copy-button";
import { RestrictedState } from "@/components/common/page";
import { ChatView } from "@/components/playground/chat";
import { HistoryList, SettingsPanel } from "@/components/playground/panels";
import { usePlayground } from "@/components/playground/use-playground";
import { useAuth } from "@/components/providers/auth-provider";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useOpenAIModels } from "@/lib/api/hooks";
import { PUBLIC_GATEWAY_URL } from "@/lib/config";
import { buildRequest, DEFAULT_SETTINGS, toCurl } from "@/lib/playground";

export default function PlaygroundPage() {
  const { can } = useAuth();
  const allowed = can("inference");
  const models = useOpenAIModels(allowed);
  const modelIds = models.data?.map((m) => m.id) ?? [];
  const defaultModel = modelIds.find((m) => m === "phi-4-mini") ?? modelIds[0] ?? "";
  const pg = usePlayground(defaultModel);
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const [settingsOpen, setSettingsOpen] = React.useState(false);

  const { active, hydrated, newChat, setModel } = pg;
  // make sure there is always a chat to type into, and that its model exists
  React.useEffect(() => {
    if (hydrated && allowed && !active && defaultModel) newChat();
  }, [hydrated, allowed, active, defaultModel, newChat]);
  React.useEffect(() => {
    if (active && modelIds.length && !modelIds.includes(active.model)) setModel(defaultModel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id, active?.model, modelIds.join(","), defaultModel]);

  if (!allowed) {
    return (
      <div className="page-container">
        <RestrictedState title="Playground requires the Developer role" description="Viewers can browse models and deployments but cannot run inference. Ask an org admin to upgrade your role." />
      </div>
    );
  }

  const settings = active?.settings ?? DEFAULT_SETTINGS;
  const settingsPanel = active && (
    <SettingsPanel
      models={models.data ?? []}
      modelsLoading={models.isLoading}
      model={active.model}
      onModel={pg.setModel}
      settings={settings}
      onChange={pg.setSettings}
      apiKey={pg.apiKey}
      onApiKey={pg.setApiKey}
    />
  );
  const historyList = (
    <HistoryList
      conversations={pg.state.conversations}
      activeId={pg.state.activeId}
      onSelect={(id) => {
        pg.selectChat(id);
        setHistoryOpen(false);
      }}
      onNew={() => {
        pg.newChat();
        setHistoryOpen(false);
      }}
      onDelete={pg.deleteChat}
      onClear={() => {
        pg.clearAll();
        toast.success("History cleared");
      }}
    />
  );

  return (
    <div className="flex h-[calc(100vh-3.5rem)] animate-fade-in">
      <aside className="hidden w-64 shrink-0 border-r bg-muted/20 lg:block" aria-label="Chat history">
        {historyList}
      </aside>

      <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Chat">
        <div className="flex items-center gap-2 border-b px-4 py-2">
          <Button variant="ghost" size="sm" className="lg:hidden" onClick={() => setHistoryOpen(true)}>
            <History /> History
          </Button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-semibold">{active?.title ?? "Playground"}</h1>
            <p className="truncate text-xs text-muted-foreground">{active?.model || "No model selected"}</p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            disabled={!active}
            onClick={async () => {
              if (!active) return;
              const ok = await copyText(toCurl(PUBLIC_GATEWAY_URL, buildRequest(active.model, active.messages.filter((m) => m.role !== "assistant" || m.content), active.settings)));
              toast[ok ? "success" : "error"](ok ? "cURL command copied" : "Could not copy");
            }}
          >
            <Terminal /> <span className="hidden sm:inline">Copy as cURL</span>
          </Button>
          <Button variant="outline" size="sm" className="xl:hidden" onClick={() => setSettingsOpen(true)}>
            <SlidersHorizontal /> Settings
          </Button>
        </div>
        <ChatView
          messages={active?.messages ?? []}
          streaming={pg.streaming}
          disabled={!active || !active.model}
          disabledReason={models.isLoading ? "Loading models…" : models.isError ? "Could not load models" : "Select a model"}
          onSend={pg.send}
          onStop={pg.stop}
          onToolResult={pg.submitToolResult}
          onRegenerate={pg.regenerate}
        />
      </section>

      <aside className="hidden w-80 shrink-0 overflow-y-auto border-l bg-muted/20 xl:block" aria-label="Playground settings">
        <h2 className="sr-only">Settings</h2>
        {settingsPanel}
      </aside>

      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent side="left" className="p-0">
          <SheetTitle className="sr-only">Chat history</SheetTitle>
          <SheetDescription className="sr-only">Previous conversations</SheetDescription>
          <div className="h-full pt-8">{historyList}</div>
        </SheetContent>
      </Sheet>
      <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
        <SheetContent className="overflow-y-auto p-0 sm:max-w-sm">
          <SheetTitle className="px-4 pt-5 text-base">Settings</SheetTitle>
          <SheetDescription className="sr-only">Model and generation settings</SheetDescription>
          {settingsPanel}
        </SheetContent>
      </Sheet>
    </div>
  );
}
