/**
 * Small pure helpers for the solve screen: locating a failing test in its
 * file, and naming things for tabs and the status bar.
 */

export interface NodeId {
  path: string;
  /** "TestContextManager::test_retry_with_async_result_or" -> ["TestContextManager", "test_retry_..."] */
  names: string[];
}

/** "tests/test_x.py::TestA::test_b[param]" -> { path: "tests/test_x.py", names: ["TestA", "test_b"] } */
export function parseNodeId(nodeId: string): NodeId {
  const [path, ...names] = nodeId.split("::");
  return { path: path.replace(/\\/g, "/"), names: names.map((n) => n.replace(/\[.*\]$/, "")) };
}

/**
 * 1-based line of the test's `def`, found by walking the class then the
 * function. Returns null if the source doesn't contain it.
 */
export function findTestLine(source: string, names: string[]): number | null {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let from = 0;
  let found: number | null = null;
  for (const name of names) {
    const pattern = new RegExp(`^\\s*(?:async\\s+def|def|class)\\s+${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`);
    found = null;
    for (let i = from; i < lines.length; i++) {
      if (pattern.test(lines[i])) {
        found = i + 1;
        from = i + 1;
        break;
      }
    }
    if (found === null) return null;
  }
  return found;
}

/** Tab labels: the file name, plus just enough of the directory to tell duplicates apart. */
export function tabLabels(paths: string[]): Map<string, { name: string; hint: string }> {
  const labels = new Map<string, { name: string; hint: string }>();
  for (const path of paths) {
    const parts = path.split("/");
    const name = parts[parts.length - 1];
    const clashes = paths.filter((p) => p !== path && p.split("/").pop() === name);
    let hint = "";
    if (clashes.length > 0) {
      const dirs = parts.slice(0, -1);
      for (let depth = 1; depth <= dirs.length; depth++) {
        hint = dirs.slice(-depth).join("/");
        const suffix = `${hint}/${name}`;
        if (!clashes.some((c) => c.endsWith(`/${suffix}`) || c === suffix)) break;
      }
    }
    labels.set(path, { name, hint });
  }
  return labels;
}

/** Every ancestor directory of the given file paths: "a/b/c.py" -> "a", "a/b". */
export function ancestorDirs(paths: Iterable<string>): Set<string> {
  const dirs = new Set<string>();
  for (const path of paths) {
    const parts = path.split("/");
    for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join("/"));
  }
  return dirs;
}

/**
 * The `def`/`class` enclosing a 1-based line, for the breadcrumb.
 *
 * Walks upward for the nearest header at strictly smaller indentation than the
 * line itself, then keeps walking for its own parents, so a method inside a
 * class comes back as ["Retrying", "_run_retry"]. Blank lines and comments are
 * skipped: their indentation says nothing about the block they sit in.
 */
export function enclosingScope(source: string, line: number): string[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  if (line < 1 || line > lines.length) return [];

  const indentOf = (text: string) => /^[ \t]*/.exec(text)![0].replace(/\t/g, "    ").length;
  const header = /^[ \t]*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)/;

  // Where the cursor sits. A blank or comment-only line takes the indentation
  // of the next real line, so a cursor parked on a blank line inside a body
  // still reports that body.
  let own: number | null = null;
  for (let i = line - 1; i < lines.length; i++) {
    if (lines[i].trim() !== "" && !lines[i].trim().startsWith("#")) {
      own = indentOf(lines[i]);
      break;
    }
  }
  if (own === null) return [];

  // A cursor on the header line itself belongs to that header, not its parent.
  const onHeader = header.exec(lines[line - 1] ?? "");
  const names: string[] = [];
  let limit = own;
  if (onHeader) {
    names.push(onHeader[1]);
    limit = indentOf(lines[line - 1]);
  }

  for (let i = line - (onHeader ? 2 : 1); i >= 0; i--) {
    const text = lines[i];
    if (text.trim() === "" || text.trim().startsWith("#")) continue;
    const indent = indentOf(text);
    if (indent >= limit) continue;
    const match = header.exec(text);
    if (match) {
      names.unshift(match[1]);
      limit = indent;
      if (indent === 0) break;
    } else {
      // A non-header at lower indentation closes the block we were in.
      limit = indent;
    }
  }
  return names;
}
