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
