import type { TarFile } from "./tar";

/** A file in the challenge tree. `text` is null for binary or oversized files. */
export interface RepoFile {
  path: string;
  text: string | null;
  bytes: number;
}

export interface ChallengeTree {
  files: Map<string, RepoFile>;
  traceback: string | null;
}

export const MAX_TEXT_BYTES = 1_000_000;
const TRACEBACK_FILE = "traceback.txt";

function isHidden(path: string): boolean {
  const parts = path.split("/");
  // The union across languages, not a per-language lookup: a challenge tree
  // contains one language's build detritus and the other's patterns match
  // nothing, so there is no reason to thread a language in just for this.
  if (parts[0] === ".git" || parts.includes("__pycache__") || path.endsWith(".pyc")) return true;
  return parts.includes("vendor") || /\.(test|exe|out)$/.test(path);
}

function decodeText(data: Uint8Array): string | null {
  if (data.length > MAX_TEXT_BYTES) return null;
  if (data.subarray(0, 8000).includes(0)) return null;
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(data);
  } catch {
    return null;
  }
}

/**
 * Strips the bundle's single root directory, drops git internals, and pulls
 * out traceback.txt (it ships at the tree root but isn't part of the repo).
 */
export function buildTree(entries: TarFile[]): ChallengeTree {
  const roots = new Set(entries.map((e) => e.path.split("/")[0]));
  const strip = roots.size === 1 && entries.every((e) => e.path.includes("/"));

  const files = new Map<string, RepoFile>();
  let traceback: string | null = null;
  for (const entry of entries) {
    const path = strip ? entry.path.slice(entry.path.indexOf("/") + 1) : entry.path;
    if (!path || isHidden(path)) continue;
    if (path === TRACEBACK_FILE) {
      traceback = decodeText(entry.data);
      continue;
    }
    files.set(path, { path, text: decodeText(entry.data), bytes: entry.data.length });
  }
  return { files, traceback };
}

export interface TreeNode {
  name: string;
  path: string;
  children: TreeNode[] | null; // null for a file
}

/** Directories first, then files, each alphabetical. */
export function toNodes(paths: Iterable<string>): TreeNode[] {
  const root: TreeNode = { name: "", path: "", children: [] };
  for (const path of paths) {
    const parts = path.split("/");
    let node = root;
    parts.forEach((part, i) => {
      const isFile = i === parts.length - 1;
      const childPath = parts.slice(0, i + 1).join("/");
      let child = node.children!.find((c) => c.name === part && (c.children === null) === isFile);
      if (!child) {
        child = { name: part, path: childPath, children: isFile ? null : [] };
        node.children!.push(child);
      }
      node = child;
    });
  }
  const sort = (nodes: TreeNode[]) => {
    nodes.sort((a, b) =>
      (a.children === null) === (b.children === null)
        ? a.name.localeCompare(b.name)
        : a.children === null
          ? 1
          : -1,
    );
    nodes.forEach((n) => n.children && sort(n.children));
  };
  sort(root.children!);
  return root.children!;
}
