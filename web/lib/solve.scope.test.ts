import { describe, expect, it } from "vitest";
import { enclosingScope } from "./solve";

const SOURCE = `import time


class Retrying:
    """doc"""

    def __init__(self, stop):
        self.stop = stop

    def _run_retry(self, fn):
        # a comment

        for attempt in range(3):
            if self.stop(attempt):
                return None
        return fn()


def wait_fixed(seconds):
    return seconds
`;

describe("enclosingScope", () => {
  const at = (line: number) => enclosingScope(SOURCE, line);

  it("names the method and its class", () => {
    expect(at(15)).toEqual(["Retrying", "_run_retry"]); // inside the for body
  });

  it("treats a header line as its own scope", () => {
    expect(at(10)).toEqual(["Retrying", "_run_retry"]);
    expect(at(4)).toEqual(["Retrying"]);
  });

  it("handles a module-level function", () => {
    expect(at(20)).toEqual(["wait_fixed"]);
  });

  it("is empty at module level", () => {
    expect(at(1)).toEqual([]);
  });

  it("ignores blank lines and comments when judging indentation", () => {
    expect(at(11)).toEqual(["Retrying", "_run_retry"]); // the comment
    expect(at(12)).toEqual(["Retrying", "_run_retry"]); // the blank line after it
  });

  it("returns nothing for a line outside the file", () => {
    expect(at(0)).toEqual([]);
    expect(at(9999)).toEqual([]);
  });
});
