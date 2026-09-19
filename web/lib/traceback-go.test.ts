import { describe, expect, it } from "vitest";

import { parseGoTraceback, shortFuncName } from "./traceback-go";
import { parseTraceback, resolveFramePath } from "./traceback";

/**
 * Captured verbatim from `go test -v` against golang-jwt/jwt with a real
 * mutation applied (parser.go:59, `!=` flipped to `==`). Keeping the real
 * output rather than a tidied-up version is the point: the absolute temp
 * paths, the Windows drive letters, the `[recovered, repanicked]` suffix and
 * the interleaved log line are all things the parser has to survive.
 */
const PANIC = String.raw`2026/09/18 19:43:01 Listening...
=== RUN   TestParser_Parse
=== RUN   TestParser_Parse/invalid_JWT
--- FAIL: TestParser_Parse (0.00s)
    --- FAIL: TestParser_Parse/invalid_JWT (0.00s)
panic: runtime error: index out of range [2] with length 0 [recovered, repanicked]

goroutine 35 [running]:
testing.tRunner.func1.2({0x7ff78fb1e0c0, 0x1a066059a018})
	C:/Program Files/Go/src/testing/testing.go:1974 +0x239
testing.tRunner.func1()
	C:/Program Files/Go/src/testing/testing.go:1977 +0x349
panic({0x7ff78fb1e0c0?, 0x1a066059a018?})
	C:/Program Files/Go/src/runtime/panic.go:860 +0x13a
github.com/golang-jwt/jwt/v5.(*Parser).ParseWithClaims(0x1a06605843c0, {0x7ff78fb4da83?, 0x2a?}, {0x0, 0x0}, 0x7ff78fb70ae8)
	C:/Users/Aarushi/AppData/Local/Temp/bugforge-mutation-wdme6jy7/tree/parser.go:80 +0x4a5
github.com/golang-jwt/jwt/v5_test.TestParser_Parse.func1(0x1a06605d4400)
	C:/Users/Aarushi/AppData/Local/Temp/bugforge-mutation-wdme6jy7/tree/parser_test.go:475 +0x1e8
testing.tRunner(0x1a06605d4400, 0x1a06605823e0)
	C:/Program Files/Go/src/testing/testing.go:2036 +0xc3
created by testing.(*T).Run in goroutine 34
	C:/Program Files/Go/src/testing/testing.go:2101 +0x4a9
FAIL	github.com/golang-jwt/jwt/v5	4.809s
FAIL
`;

/** The far more common shape: an assertion, and no stack whatsoever. */
const ASSERTION = `=== RUN   TestVerifyAud
    validator_test.go:123: [aud] Expected true, got false
    validator_test.go:131: [aud] Expected no error, got "invalid audience"
--- FAIL: TestVerifyAud (0.00s)
FAIL
FAIL	github.com/golang-jwt/jwt/v5	0.412s
`;

describe("shortFuncName", () => {
  it.each([
    ["github.com/golang-jwt/jwt/v5.(*Parser).ParseWithClaims(0x1, 0x2)", "(*Parser).ParseWithClaims"],
    ["github.com/golang-jwt/jwt/v5_test.TestParser_Parse.func1(0x1a06)", "TestParser_Parse.func1"],
    ["testing.tRunner(0x1, 0x2)", "tRunner"],
    ["main.main()", "main"],
  ])("%s -> %s", (input, expected) => {
    expect(shortFuncName(input)).toBe(expected);
  });
});

describe("a panic stack", () => {
  const frames = parseGoTraceback(PANIC);

  it("is reversed into outermost-first order", () => {
    // Go prints a goroutine stack innermost first, which is the opposite of
    // a Python traceback. Every consumer of Frame[] assumes the Python order.
    expect(frames[0].path.endsWith("testing.go")).toBe(true);
    expect(frames[frames.length - 1].path.endsWith("testing.go")).toBe(true);
    const repoFrames = frames.filter((f) => f.path.endsWith("/parser.go"));
    expect(repoFrames).toHaveLength(1);
    expect(repoFrames[0].line).toBe(80);
  });

  it("keeps the test frame deeper than the library frame it called", () => {
    const parser = frames.findIndex((f) => f.path.endsWith("/parser.go"));
    const test = frames.findIndex((f) => f.path.endsWith("/parser_test.go"));
    // The test called into the parser, so the test frame is the outer one.
    expect(test).toBeLessThan(parser);
  });

  it("puts the panic message on the deepest frame", () => {
    const deepest = frames[frames.length - 1];
    expect(deepest.exception).toBe("runtime error: index out of range [2] with length 0");
    // The "[recovered, repanicked]" bookkeeping is noise to a learner.
    expect(deepest.exception).not.toContain("recovered");
  });

  it("numbers frames consecutively from zero", () => {
    expect(frames.map((f) => f.index)).toEqual(frames.map((_, i) => i));
  });

  it("names the function each frame is in", () => {
    const parser = frames.find((f) => f.path.endsWith("/parser.go"));
    expect(parser?.func).toBe("(*Parser).ParseWithClaims");
  });

  it("stops at the created-by line rather than following the spawn site", () => {
    expect(frames.some((f) => f.line === 2101)).toBe(false);
  });

  it("does not mistake the interleaved log line for a frame", () => {
    expect(frames.some((f) => f.path.includes("Listening"))).toBe(false);
  });
});

describe("an assertion failure with no stack", () => {
  const frames = parseGoTraceback(ASSERTION);

  it("yields one frame per reported location, in order", () => {
    expect(frames).toHaveLength(2);
    expect(frames.map((f) => f.line)).toEqual([123, 131]);
    expect(frames[0].path).toBe("validator_test.go");
  });

  it("carries the assertion message", () => {
    expect(frames[0].errors[0]).toBe("[aud] Expected true, got false");
  });

  it("ignores the --- FAIL summary line", () => {
    expect(frames.some((f) => f.path.includes("FAIL"))).toBe(false);
  });
});

describe("resolving frames against the tree", () => {
  const tree = ["parser.go", "parser_test.go", "validator.go", "request/oauth2.go"];

  it("matches an absolute temp path onto the repo file", () => {
    const frames = parseGoTraceback(PANIC);
    const parser = frames.find((f) => f.path.endsWith("/parser.go"))!;
    expect(resolveFramePath(parser.path, tree)).toBe("parser.go");
  });

  it("leaves GOROOT frames unresolved so they stay dimmed on the rail", () => {
    const frames = parseGoTraceback(PANIC);
    const goroot = frames.filter((f) => f.path.includes("/Go/src/"));
    expect(goroot.length).toBeGreaterThan(0);
    for (const frame of goroot) expect(resolveFramePath(frame.path, tree)).toBeNull();
  });
});

describe("parseTraceback dispatch", () => {
  it("uses the Go parser for a Go challenge", () => {
    expect(parseTraceback(PANIC, "Go")).toEqual(parseGoTraceback(PANIC));
  });

  it("defaults to the pytest parser when no language is given", () => {
    // A Go panic run through the pytest parser finds nothing, which is the
    // proof that the dispatch is doing real work rather than both parsers
    // happening to cope.
    expect(parseTraceback(PANIC)).toHaveLength(0);
  });

  it("still parses a real pytest traceback", () => {
    const pytest = [
      "    def test_thing():",
      ">       assert stop(1) is True",
      "E       assert False is True",
      "",
      "tests/test_stop.py:12: AssertionError",
    ].join("\n");
    const frames = parseTraceback(pytest, "Python");
    expect(frames).toHaveLength(1);
    expect(frames[0].path).toBe("tests/test_stop.py");
    expect(frames[0].line).toBe(12);
  });
});
