import { createTwoFilesPatch } from "diff";

/**
 * Builds the unified diff the grader applies with `git apply -p1`, and mirrors
 * the path half of cloud/anti_cheat.py so an obviously doomed submission is
 * caught before it is sent. The server re-checks everything regardless.
 */

export interface FileChange {
  path: string;
  /** exactly as it came out of the tarball */
  original: string;
  /** as CodeMirror returns it: always "\n" line endings */
  current: string;
}

export function editorText(original: string): string {
  return original.replace(/\r\n?/g, "\n");
}

/** CodeMirror normalises line endings; restore the file's own before diffing. */
export function restoreLineEndings(current: string, original: string): string {
  return original.includes("\r\n") ? current.replace(/\r?\n/g, "\r\n") : current;
}

export function isModified(change: FileChange): boolean {
  return restoreLineEndings(change.current, change.original) !== change.original;
}

export function buildPatch(changes: FileChange[]): string {
  return changes
    .filter(isModified)
    .sort((a, b) => a.path.localeCompare(b.path))
    .map((c) =>
      createTwoFilesPatch(
        `a/${c.path}`,
        `b/${c.path}`,
        c.original,
        restoreLineEndings(c.current, c.original),
        undefined,
        undefined,
        { context: 3 },
      ).replace(/^(?:Index: [^\n]*\n)?(?:=+\n)?/, ""),
    )
    .join("");
}

/** cloud/anti_cheat.is_test_path */
export function isTestPath(path: string): boolean {
  const parts = path.replace(/\\/g, "/").split("/");
  const name = parts[parts.length - 1];
  if (parts.some((p) => p === "tests" || p === "test" || p === "testing")) return true;
  return name.startsWith("test_") || name.endsWith("_test.py") || name === "conftest.py";
}

/** cloud/anti_cheat.check_paths: the reason a patch touching these paths is rejected, or null. */
export function pathProblem(paths: string[]): string | null {
  if (paths.length === 0) return "no files changed";
  for (const path of paths) {
    if (isTestPath(path)) return `patch modifies a test file: ${path}`;
    if (!path.endsWith(".py")) return `patch modifies a non-Python file: ${path}`;
  }
  return null;
}
