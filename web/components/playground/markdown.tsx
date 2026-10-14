"use client";

import * as React from "react";
import { Check, Copy } from "lucide-react";
import { copyText } from "@/components/common/copy-button";

function Inline({ text }: { text: string }) {
  // **bold**, `code`
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**")) return <strong key={i}>{p.slice(2, -2)}</strong>;
        if (p.startsWith("`") && p.endsWith("`")) return <code key={i} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{p.slice(1, -1)}</code>;
        return <React.Fragment key={i}>{p}</React.Fragment>;
      })}
    </>
  );
}

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <div className="my-2 overflow-hidden rounded-lg border bg-muted/60">
      <div className="flex items-center justify-between border-b bg-muted px-3 py-1 text-[11px] text-muted-foreground">
        <span className="font-mono">{lang || "text"}</span>
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:text-foreground"
          onClick={async () => {
            if (await copyText(code)) {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }
          }}
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />} {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-[13px] leading-relaxed">
        <code className="font-mono">{code}</code>
      </pre>
    </div>
  );
}

function TextBlock({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: React.ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    blocks.push(
      <Tag key={blocks.length} className={list.ordered ? "my-1 list-decimal space-y-0.5 pl-5" : "my-1 list-disc space-y-0.5 pl-5"}>
        {list.items.map((it, i) => (
          <li key={i}>
            <Inline text={it} />
          </li>
        ))}
      </Tag>,
    );
    list = null;
  };
  lines.forEach((line) => {
    const ul = /^\s*[-*]\s+(.*)/.exec(line);
    const ol = /^\s*\d+\.\s+(.*)/.exec(line);
    if (ul || ol) {
      const ordered = !!ol;
      if (list && list.ordered !== ordered) flush();
      list ??= { ordered, items: [] };
      list.items.push((ul ?? ol)![1]!);
      return;
    }
    flush();
    if (!line.trim()) return;
    blocks.push(
      <p key={blocks.length} className="my-1.5 first:mt-0 last:mb-0">
        <Inline text={line} />
      </p>,
    );
  });
  flush();
  return <>{blocks}</>;
}

/** Tiny safe markdown renderer: fenced code, lists, bold and inline code (no raw HTML). */
export function Markdown({ text }: { text: string }) {
  const segments = text.split(/(```[\s\S]*?(?:```|$))/g).filter((s) => s !== "");
  return (
    <div className="text-sm leading-relaxed">
      {segments.map((seg, i) => {
        if (seg.startsWith("```")) {
          const body = seg.replace(/^```/, "").replace(/```$/, "");
          const nl = body.indexOf("\n");
          const lang = nl === -1 ? "" : body.slice(0, nl).trim();
          const code = nl === -1 ? body : body.slice(nl + 1).replace(/\n$/, "");
          return <CodeBlock key={i} code={code} lang={lang} />;
        }
        return <TextBlock key={i} text={seg} />;
      })}
    </div>
  );
}
