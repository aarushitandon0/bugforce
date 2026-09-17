"use client";

import type { TreeNode } from "@/lib/tree";

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
}: {
  nodes: TreeNode[];
  expanded: ReadonlySet<string>;
  onToggle: (dir: string) => void;
  onOpen: (path: string) => void;
  marks: TreeMarks;
}) {
  return (
    <ul role="tree" aria-label="files" className="py-1 text-[12px] leading-[22px]">
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
          onClick={() => onToggle(node.path)}
          style={indent}
          className="flex w-full items-center gap-1.5 pr-3 text-left text-dim outline-none transition-colors duration-[120ms] hover:bg-panel hover:text-text focus-visible:bg-panel focus-visible:text-text"
        >
          <span aria-hidden className="w-[1ch] shrink-0">{isOpen ? "▾" : "▸"}</span>
          <span className="truncate">{node.name}/</span>
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
        disabled={binary}
        onClick={() => onOpen(node.path)}
        style={indent}
        title={binary ? "binary or too large to open" : node.path}
        className={`flex w-full items-center gap-1.5 border-l pr-3 text-left outline-none transition-colors duration-[120ms] hover:bg-panel focus-visible:bg-panel disabled:cursor-default disabled:hover:bg-transparent ${
          active ? "border-text bg-panel text-text" : `border-transparent ${marks.open.has(node.path) ? "text-text" : "text-dim"}`
        } ${binary ? "opacity-50" : ""}`}
      >
        <span aria-hidden className="flex w-[1ch] shrink-0 justify-center">
          {traced && <span className="block h-[5px] w-[5px] bg-causal" />}
        </span>
        <span className="min-w-0 flex-1 truncate">{node.name}</span>
        {modified && (
          <span className="shrink-0 text-[10px] text-error" aria-label="modified">
            M
          </span>
        )}
      </button>
    </li>
  );
}
