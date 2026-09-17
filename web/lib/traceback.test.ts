import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parseTraceback, resolveFramePath } from "./traceback";

const fixture = (name: string) => readFileSync(new URL(`./__fixtures__/${name}`, import.meta.url), "utf8");

describe("parseTraceback on real tenacity challenge tracebacks", () => {
  it("walks an async traceback frame by frame, outermost first", () => {
    const frames = parseTraceback(fixture("traceback-async-exc.txt"));

    expect(frames.length).toBeGreaterThan(3);
    expect(frames[0]).toMatchObject({
      path: "tests/test_asyncio.py",
      line: 450,
      func: "test_retry_with_async_exc",
      failingSource: "result = await test()",
    });
    expect(frames).toContainEqual(
      expect.objectContaining({ path: "tenacity/asyncio/__init__.py", line: 204, func: "__anext__" }),
    );
    for (const frame of frames) expect(frame.path).not.toContain("\\");

    const deepest = frames[frames.length - 1];
    expect(deepest.exception).toBe("CustomException");
    expect(deepest.errors.at(-1)).toMatch(/CustomException$/);
    expect(deepest.failingSource).toBe("raise CustomException");
  });

  it("keeps the assertion message on the deepest frame", () => {
    const frames = parseTraceback(fixture("traceback-assert-instance.txt"));
    const deepest = frames[frames.length - 1];
    expect(deepest.path).toBe("tests/test_tenacity.py");
    expect(deepest.exception).toBe("AssertionError");
    expect(deepest.errors[0]).toMatch(/^AssertionError: None is not an instance of/);
  });
});

describe("parseTraceback on Linux-style output", () => {
  const text = [
    "    def test_stop():",
    ">       assert stop(3)",
    "",
    "tests/test_stop.py:12: ",
    "_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _",
    "",
    "self = <Retrying object>",
    "",
    "    def __call__(self, state):",
    ">       return wrapped(state)",
    "",
    "/var/lang/lib/python3.12/site-packages/wrapt/wrappers.py:88: in __call__",
    "_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _",
    "",
    "    def stop(n):",
    ">       return n > LIMIT",
    "E       TypeError: '>' not supported",
    "",
    "tenacity/stop.py:40: TypeError",
  ].join("\n");

  it("accepts both location-line forms and marks local variable lines", () => {
    const frames = parseTraceback(text);
    expect(frames.map((f) => [f.path, f.line, f.func, f.exception])).toEqual([
      ["tests/test_stop.py", 12, "test_stop", null],
      ["/var/lang/lib/python3.12/site-packages/wrapt/wrappers.py", 88, "__call__", null],
      ["tenacity/stop.py", 40, "stop", "TypeError"],
    ]);
    expect(frames[1].excerpt[0]).toEqual({ text: "self = <Retrying object>", kind: "local" });
    expect(frames[2].errors).toEqual(["TypeError: '>' not supported"]);
  });

  it("resolves frames onto the tree, and leaves site-packages unresolved", () => {
    const tree = ["tenacity/stop.py", "stop.py", "tests/test_stop.py"];
    expect(resolveFramePath("tenacity/stop.py", tree)).toBe("tenacity/stop.py");
    expect(resolveFramePath("/tmp/bugforge-mutation-x/tree/tenacity/stop.py", tree)).toBe("tenacity/stop.py");
    expect(resolveFramePath("/var/lang/lib/python3.12/site-packages/wrapt/wrappers.py", tree)).toBeNull();
  });
});
