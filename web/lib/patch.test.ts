import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";
import { buildPatch, editorText, isModified, isTestPath, pathProblem } from "./patch";

/** Applies a patch exactly the way cloud/handlers/fn_grade.py does. */
function gitApply(files: Record<string, string>, patch: string): Record<string, string> {
  const dir = mkdtempSync(join(tmpdir(), "bugforge-patch-"));
  try {
    for (const [path, content] of Object.entries(files)) {
      mkdirSync(dirname(join(dir, path)), { recursive: true });
      writeFileSync(join(dir, path), Buffer.from(content, "utf8"));
    }
    // The grader runs on Linux with stock git. A developer machine's global
    // core.autocrlf (the Windows default) would rewrite line endings on apply
    // and make these assertions about the machine instead of the patch.
    const git = ["-c", "core.autocrlf=false", "-c", "core.eol=lf"];
    execFileSync("git", [...git, "init", "-q"], { cwd: dir });
    writeFileSync(join(dir, "submission.patch"), patch.endsWith("\n") ? patch : patch + "\n");
    execFileSync("git", [...git, "apply", "--whitespace=nowarn", "-p1", "submission.patch"], {
      cwd: dir,
      stdio: "pipe",
    });
    return Object.fromEntries(
      Object.keys(files).map((path) => [path, readFileSync(join(dir, path)).toString("utf8")]),
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const RETRY = Array.from({ length: 30 }, (_, i) => `line_${i} = ${i}\n`).join("") +
  "def stop(state):\n    return state.attempt < 3\n";

describe("buildPatch applies cleanly with git apply -p1", () => {
  it("fixes a single token mid-file", () => {
    const current = RETRY.replace("attempt < 3", "attempt <= 3");
    const patch = buildPatch([{ path: "tenacity/stop.py", original: RETRY, current }]);
    expect(patch.startsWith("--- a/tenacity/stop.py\n+++ b/tenacity/stop.py\n")).toBe(true);
    expect(gitApply({ "tenacity/stop.py": RETRY }, patch)["tenacity/stop.py"]).toBe(current);
  });

  it("handles a last line with no trailing newline", () => {
    const original = "a = 1\nb = a < 2";
    const current = "a = 1\nb = a <= 2";
    const patch = buildPatch([{ path: "pkg/m.py", original, current }]);
    expect(gitApply({ "pkg/m.py": original }, patch)["pkg/m.py"]).toBe(current);
  });

  it("combines several files, skipping unmodified ones", () => {
    const files = { "pkg/b.py": "x = 1\n", "pkg/a.py": "y = 2\n", "pkg/c.py": "z = 3\n" };
    const patch = buildPatch([
      { path: "pkg/b.py", original: files["pkg/b.py"], current: "x = 10\n" },
      { path: "pkg/c.py", original: files["pkg/c.py"], current: files["pkg/c.py"] },
      { path: "pkg/a.py", original: files["pkg/a.py"], current: "y = 20\n" },
    ]);
    const targets = [...patch.matchAll(/^\+\+\+ (?:b\/)?([^\t\n]+)/gm)].map((m) => m[1]);
    expect(targets).toEqual(["pkg/a.py", "pkg/b.py"]);
    expect(gitApply(files, patch)).toEqual({ "pkg/b.py": "x = 10\n", "pkg/a.py": "y = 20\n", "pkg/c.py": "z = 3\n" });
  });

  it("preserves CRLF line endings the editor normalised away", () => {
    const original = "def f(n):\r\n    return n < 3\r\n";
    const current = editorText(original).replace("n < 3", "n <= 3");
    const patch = buildPatch([{ path: "pkg/w.py", original, current }]);
    expect(gitApply({ "pkg/w.py": original }, patch)["pkg/w.py"]).toBe("def f(n):\r\n    return n <= 3\r\n");
  });

  it("is empty when nothing changed, including CRLF round-trips", () => {
    const original = "a = 1\r\nb = 2\r\n";
    expect(isModified({ path: "x.py", original, current: editorText(original) })).toBe(false);
    expect(buildPatch([{ path: "x.py", original, current: editorText(original) }])).toBe("");
  });
});

describe("path rules mirror cloud/anti_cheat.py", () => {
  it.each([
    ["tests/test_stop.py", true],
    ["tenacity/tests/helpers.py", true],
    ["test_things.py", true],
    ["pkg/stop_test.py", true],
    ["conftest.py", true],
    ["tenacity/stop.py", false],
    ["tenacity/testament.py", false],
  ])("isTestPath(%s) = %s", (path, expected) => {
    expect(isTestPath(path)).toBe(expected);
  });

  it("explains the first problem it finds", () => {
    expect(pathProblem([])).toBe("no files changed");
    expect(pathProblem(["tenacity/stop.py", "tests/test_stop.py"])).toBe("patch modifies a test file: tests/test_stop.py");
    expect(pathProblem(["pyproject.toml"])).toBe("patch modifies a file that is not Python source: pyproject.toml");
    expect(pathProblem(["tenacity/stop.py"])).toBeNull();
  });

  it("judges a Go challenge by Go's rules", () => {
    expect(pathProblem(["parser.go"], "Go")).toBeNull();
    expect(pathProblem(["request/oauth2.go"], "Go")).toBeNull();
    expect(pathProblem(["parser_test.go"], "Go")).toBe("patch modifies a test file: parser_test.go");
    expect(pathProblem(["testdata/fixture.go"], "Go")).toBe("patch modifies a test file: testdata/fixture.go");
    expect(pathProblem(["go.mod"], "Go")).toBe("patch modifies a file that is not Go source: go.mod");
    // ...and a .py file is wrong in a Go challenge, not merely unusual.
    expect(pathProblem(["setup.py"], "Go")).toBe("patch modifies a file that is not Go source: setup.py");
  });

  it("keeps Python's rules unchanged when no language is given", () => {
    // Every challenge minted before the language field existed comes through
    // this path, so the default has to stay Python.
    expect(pathProblem(["tenacity/stop.py"])).toBeNull();
    expect(pathProblem(["parser.go"])).toBe("patch modifies a file that is not Python source: parser.go");
    expect(isTestPath("pkg/stop_test.go", "Go")).toBe(true);
    expect(isTestPath("pkg/stop_test.go")).toBe(false);
  });
});
