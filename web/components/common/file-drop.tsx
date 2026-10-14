"use client";

import * as React from "react";
import { FileUp } from "lucide-react";
import { cn, formatBytes } from "@/lib/utils";

/** Accessible drag-and-drop file picker (the native input stays keyboard-operable). */
export function FileDrop({
  accept,
  multiple,
  onFiles,
  title = "Drop files here or click to browse",
  hint,
  disabled,
  id,
}: {
  accept?: string;
  multiple?: boolean;
  onFiles: (files: File[]) => void;
  title?: string;
  hint?: string;
  disabled?: boolean;
  id: string;
}) {
  const [over, setOver] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);
  return (
    <label
      htmlFor={id}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (disabled) return;
        const files = Array.from(e.dataTransfer.files);
        if (files.length) onFiles(multiple ? files : files.slice(0, 1));
      }}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-background",
        over ? "border-primary bg-accent" : "hover:border-primary/50 hover:bg-muted/40",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <FileUp className="h-6 w-6 text-muted-foreground" aria-hidden />
      <span className="text-sm font-medium">{title}</span>
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      <input
        ref={inputRef}
        id={id}
        type="file"
        className="sr-only"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) onFiles(files);
          e.target.value = "";
        }}
      />
    </label>
  );
}

export function FileChip({ file, onClear }: { file: File; onClear?: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border bg-muted/40 px-3 py-2 text-sm">
      <div className="min-w-0">
        <p className="truncate font-medium">{file.name}</p>
        <p className="text-xs text-muted-foreground">{formatBytes(file.size)}</p>
      </div>
      {onClear && (
        <button type="button" className="rounded text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline" onClick={onClear}>
          Remove
        </button>
      )}
    </div>
  );
}
