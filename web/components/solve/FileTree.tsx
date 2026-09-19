"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useRef } from "react";
import type { TreeNode } from "@/lib/tree";
import { FileIcon, FolderIcon } from "./FileIcon";

export interface TreeMarks {
  active: string | null;
  open: ReadonlySet<string>;
  modified: ReadonlySet<string>;
  /** files that appear in the trace */
  traced: ReadonlySet<string>;
  /** files that can't be shown as text */
  binary: ReadonlySet<string>;
}

export function FileTree({
  nodes,
  expanded,
  onToggle,
  onOpen,
  marks,
  reveal,
}: {
  nodes: TreeNode[];
  expanded: ReadonlySet<string>;
  onToggle: (dir: string) => void;
  onOpen: (path: string) => void;
  marks: TreeMarks;
  /** bumped by the breadcrumb to scroll a node into view and flash it */
  reveal?: { path: string; n: number };
}) {
  const rootRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (!reveal) return;
    const el = rootRef.current?.querySelector<HTMLElement>(`[data-node="${CSS.escape(reveal.path)}"]`);
    el?.scrollIntoView({ block: "nearest" });
    el?.focus({ preventScroll: true });
  }, [reveal]);

  return (
    <ul ref={rootRef} role="tree" aria-label="files" className="py-1 text-[12px] leading-[22px]">
      {nodes.map((node) => (
        <Node key={node.path} node={node} depth={0} expanded={expanded} onToggle={onToggle} onOpen={onOpen} marks={marks} />
      ))}
    </ul>
  );
}

function Node({
  node,
  depth,
  expanded,
  onToggle,
  onOpen,
  marks,
}: {
  node: TreeNode;
  depth: number;
  expanded: ReadonlySet<string>;
  onToggle: (dir: string) => void;
  onOpen: (path: string) => void;
  marks: TreeMarks;
}) {
  const indent = { paddingLeft: `${12 + depth * 12}px` };

  if (node.children) {
    const isOpen = expanded.has(node.path);
    return (
      <li role="treeitem" aria-expanded={isOpen}>
        <button
          type="button"
          data-node={node.path}
          onClick={() => onToggle(node.path)}
          style={indent}
          className="flex w-full items-center gap-1 pr-3 text-left text-muted outline-none transition-colors duration-[120ms] hover:bg-surface-3 hover:text-text focus-visible:bg-surface-3 focus-visible:text-text"
        >
          <span aria-hidden className="flex shrink-0 items-center opacity-70">
            {isOpen ? <ChevronDown size={12} strokeWidth={1.5} /> : <ChevronRight size={12} strokeWidth={1.5} />}
          </span>
          <FolderIcon open={isOpen} />
          <span className="truncate pl-0.5">{node.name}</span>
        </button>
        {isOpen && (
          <ul role="group">
            {node.children.map((child) => (
              <Node key={child.path} node={child} depth={depth + 1} expanded={expanded} onToggle={onToggle} onOpen={onOpen} marks={marks} />
            ))}
          </ul>
        )}
      </li>
    );
  }

  const active = marks.active === node.path;
  const binary = marks.binary.has(node.path);
  const modified = marks.modified.has(node.path);
  const traced = marks.traced.has(node.path);

  return (
    <li role="treeitem" aria-selected={active}>
      <button
        type="button"
        data-node={node.path}
        disabled={binary}
        onClick={() => onOpen(node.path)}
        style={indent}
        title={binary ? "binary or too large to open" : node.path}
        className={`flex w-full items-center gap-1 border-l pr-3 text-left outline-none transition-colors duration-[120ms] hover:bg-surface-3 focus-visible:bg-surface-3 disabled:cursor-default disabled:hover:bg-transparent ${
          active ? "border-text bg-surface-2 text-text" : `border-transparent ${marks.open.has(node.path) ? "text-text" : "text-muted"}`
        } ${binary ? "opacity-50" : ""}`}
      >
        <span aria-hidden className="flex w-[7px] shrink-0 justify-center">
          {traced && <span className="block h-[5px] w-[5px] bg-frame" />}
        </span>
        <FileIcon name={node.name} />
        <span className="min-w-0 flex-1 truncate pl-0.5">{node.name}</span>
        {modified && (
          <span className="shrink-0 text-[10px] text-gap" aria-label="modified">
            M
          </span>
        )}
      </button>
    </li>
  );
}
