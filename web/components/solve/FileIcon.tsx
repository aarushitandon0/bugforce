"use client";

import {
  Braces,
  File,
  FileCode2,
  FileText,
  FolderClosed,
  FolderOpen,
  Settings2,
} from "lucide-react";
import type { ComponentType } from "react";

/**
 * A small coloured glyph per file type.
 *
 * The hues are the editor's own syntax tokens rather than new colours, so the
 * tree and the code agree and the light palette gets its versions for free.
 */
const BY_EXTENSION: Record<string, { Icon: ComponentType<{ size?: number; strokeWidth?: number }>; color: string }> = {
  py: { Icon: FileCode2, color: "var(--bf-syn-function)" },
  pyi: { Icon: FileCode2, color: "var(--bf-syn-function)" },
  json: { Icon: Braces, color: "var(--bf-syn-number)" },
  toml: { Icon: Settings2, color: "var(--bf-syn-class)" },
  cfg: { Icon: Settings2, color: "var(--bf-syn-class)" },
  ini: { Icon: Settings2, color: "var(--bf-syn-class)" },
  yml: { Icon: Settings2, color: "var(--bf-syn-class)" },
  yaml: { Icon: Settings2, color: "var(--bf-syn-class)" },
  md: { Icon: FileText, color: "var(--bf-syn-string)" },
  rst: { Icon: FileText, color: "var(--bf-syn-string)" },
  txt: { Icon: FileText, color: "var(--bf-dim)" },
};

export function FileIcon({ name, size = 13 }: { name: string; size?: number }) {
  const extension = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  const entry = BY_EXTENSION[extension];
  const Icon = entry?.Icon ?? File;
  return (
    <span aria-hidden className="flex shrink-0 items-center" style={{ color: entry?.color ?? "var(--bf-dim)" }}>
      <Icon size={size} strokeWidth={1.5} />
    </span>
  );
}

export function FolderIcon({ open, size = 13 }: { open: boolean; size?: number }) {
  const Icon = open ? FolderOpen : FolderClosed;
  return (
    <span aria-hidden className="flex shrink-0 items-center text-dim">
      <Icon size={size} strokeWidth={1.5} />
    </span>
  );
}
