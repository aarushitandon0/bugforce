"use client";

import { ChevronRight } from "lucide-react";
import { FileIcon } from "./FileIcon";

/**
 * `tenacity › asyncio › __init__.py › _run_retry`
 *
 * Directory and file segments reveal that node in the file tree. The trailing
 * segments are the `def`/`class` around the cursor, so the crumb answers
 * "where am I" the way an editor's does -- those aren't clickable, they just
 * follow the caret.
 */
export function Breadcrumbs({
  path,
  scope,
  onReveal,
}: {
  path: string | null;
  /** enclosing class/def names, outermost first */
  scope: string[];
  onReveal: (path: string) => void;
}) {
  if (!path) {
    return <span className="truncate text-dim">no file open</span>;
  }
  const parts = path.split("/");
  const dirs = parts.slice(0, -1);
  const name = parts[parts.length - 1];

  return (
    <nav aria-label="breadcrumb" className="flex min-w-0 items-center gap-0.5 overflow-hidden">
      {dirs.map((dir, i) => (
        <span key={i} className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={() => onReveal(parts.slice(0, i + 1).join("/"))}
            className="text-dim outline-none transition-colors duration-[120ms] hover:text-text focus-visible:text-text"
          >
            {dir}
          </button>
          <Separator />
        </span>
      ))}
      <button
        type="button"
        onClick={() => onReveal(path)}
        title={`reveal ${path} in the tree`}
        className="flex min-w-0 shrink items-center gap-1.5 text-text outline-none transition-colors duration-[120ms] hover:text-causal focus-visible:text-causal"
      >
        <FileIcon name={name} size={12} />
        <span className="truncate">{name}</span>
      </button>
      {scope.map((segment, i) => (
        <span key={i} className="flex shrink-0 items-center gap-0.5">
          <Separator />
          <span className={i === scope.length - 1 ? "text-text" : "text-dim"}>{segment}</span>
        </span>
      ))}
    </nav>
  );
}

function Separator() {
  return (
    <span aria-hidden className="flex shrink-0 items-center text-dim opacity-60">
      <ChevronRight size={12} strokeWidth={1.5} />
    </span>
  );
}
