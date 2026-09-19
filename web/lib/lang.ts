/**
 * Per-language rules the solve screen needs, in one place.
 *
 * These mirror cloud/anti_cheat.py exactly, and they have to: the screen uses
 * them to decide what a learner may edit, and the server uses its copy to
 * decide what it will grade. If the two disagree the learner finds out by
 * having a patch rejected after they have already written it.
 *
 * Adding a language here is not enough on its own -- the editor also needs a
 * CodeMirror mode (lib/editor.ts) and the failure output needs a parser
 * (lib/traceback.ts).
 */

export type LanguageId = "python" | "go";

export interface LanguageRules {
  id: LanguageId;
  /** What a person reads on a card. */
  label: string;
  /** The only extension a patch may touch. */
  sourceSuffix: string;
  /** Files a patch may not touch, because the grader would reject it. */
  isTestPath: (path: string) => boolean;
  /** Build detritus that should never appear in the file tree. */
  isHiddenPath: (path: string) => boolean;
}

function parts(path: string): string[] {
  return path.replace(/\\/g, "/").split("/");
}

function basename(path: string): string {
  const p = parts(path);
  return p[p.length - 1];
}

const PYTHON: LanguageRules = {
  id: "python",
  label: "Python",
  sourceSuffix: ".py",
  isTestPath(path) {
    const p = parts(path);
    const name = basename(path);
    if (p.some((s) => s === "tests" || s === "test" || s === "testing")) return true;
    return name.startsWith("test_") || name.endsWith("_test.py") || name === "conftest.py";
  },
  isHiddenPath(path) {
    const p = parts(path);
    return p[0] === ".git" || p.includes("__pycache__") || path.endsWith(".pyc");
  },
};

const GO: LanguageRules = {
  id: "go",
  label: "Go",
  sourceSuffix: ".go",
  isTestPath(path) {
    const p = parts(path);
    const name = basename(path);
    if (p.some((s) => s === "tests" || s === "test" || s === "testing")) return true;
    // `_test.go` is the whole of Go's test convention, and `testdata` is a
    // directory the Go toolchain refuses to build -- editing either changes
    // nothing a test can observe.
    if (p.includes("testdata")) return true;
    return name.endsWith("_test.go");
  },
  isHiddenPath(path) {
    const p = parts(path);
    return (
      p[0] === ".git" ||
      p.includes("vendor") ||
      path.endsWith(".test") ||
      path.endsWith(".exe") ||
      path.endsWith(".out")
    );
  },
};

const BY_ID: Record<LanguageId, LanguageRules> = { python: PYTHON, go: GO };

/**
 * Rules for a language name as the API spells it ("Python", "Go").
 *
 * Falls back to Python rather than throwing: an unrecognised language means
 * an older challenge record or a newer backend, and the solve screen staying
 * usable matters more than being strict here. The server is the authority on
 * what it will actually accept.
 */
export function rulesFor(language: string | null | undefined): LanguageRules {
  const key = (language ?? "").toLowerCase();
  return BY_ID[key as LanguageId] ?? PYTHON;
}
