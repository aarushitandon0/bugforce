import { describe, expect, it } from "vitest";
import { dedentSplit, parseUnifiedDiff, splitReveal } from "./reveal";

const OR_LINE = "            result = result or await _utils.wrap_to_async_func(r)(retry_state)";
const AND_LINE = "            result = result and await _utils.wrap_to_async_func(r)(retry_state)";

describe("splitReveal", () => {
  it("splits on the byte offsets", () => {
    const split = splitReveal({
      original_line: OR_LINE,
      mutated_line: AND_LINE,
      original_token: "or",
      mutated_token: "and",
      col_start: 28,
      col_end: 30,
    });
    expect(split.original).toEqual({ before: "            result = result ", token: "or", after: OR_LINE.slice(30) });
    expect(split.mutated.token).toBe("and");
    expect(split.mutated.before + split.mutated.token + split.mutated.after).toBe(AND_LINE);
  });

  it("keeps the whole token when the mutation extends it (< to <=)", () => {
    const split = splitReveal({
      original_line: "if x < 3:",
      mutated_line: "if x <= 3:",
      original_token: "<",
      mutated_token: "<=",
      col_start: 5,
      col_end: 6,
    });
    expect(split.original.token).toBe("<");
    expect(split.mutated).toEqual({ before: "if x ", token: "<=", after: " 3:" });
  });

  it("converts UTF-8 byte offsets, not UTF-16 indices", () => {
    const split = splitReveal({
      original_line: 'msg = "héllo" if a == b else ""',
      mutated_line: 'msg = "héllo" if a != b else ""',
      original_token: "==",
      mutated_token: "!=",
      col_start: 19, // "é" is two bytes, so the character index is 18
      col_end: 21,
    });
    expect(split.mutated).toEqual({ before: 'msg = "héllo" if a ', token: "!=", after: ' b else ""' });
  });

  it("locates the token by content when the offsets are wrong", () => {
    const split = splitReveal({
      original_line: "a + b + c",
      mutated_line: "a + b - c",
      original_token: "+",
      mutated_token: "-",
      col_start: 0,
      col_end: 1,
    });
    expect(split.mutated).toEqual({ before: "a + b ", token: "-", after: " c" });
  });

  it("falls back to the common prefix and suffix", () => {
    const split = splitReveal({
      original_line: "return True",
      mutated_line: "return False",
      original_token: "?",
      mutated_token: "?",
      col_start: 99,
      col_end: 100,
    });
    expect(split.original.token).toBe("Tru");
    expect(split.mutated.token).toBe("Fals");
  });

  it("dedents both sides together", () => {
    const split = dedentSplit(
      splitReveal({ original_line: OR_LINE, mutated_line: AND_LINE, original_token: "or", mutated_token: "and", col_start: 28, col_end: 30 }),
    );
    expect(split.original.before).toBe("result = result ");
    expect(split.mutated.before).toBe("result = result ");
  });
});

describe("parseUnifiedDiff", () => {
  it("numbers each side", () => {
    const rows = parseUnifiedDiff(
      "--- a/tenacity/asyncio/retry.py\n+++ b/tenacity/asyncio/retry.py\n@@ -110,4 +110,4 @@\n     async def f():\n         result = False\n-        a or b\n+        a and b\n         return result\n",
    );
    expect(rows.map((r) => r.kind)).toEqual(["file", "file", "hunk", "context", "context", "removed", "added", "context"]);
    expect(rows[5]).toEqual({ kind: "removed", text: "        a or b", oldLine: 112, newLine: null });
    expect(rows[6]).toEqual({ kind: "added", text: "        a and b", oldLine: null, newLine: 112 });
    expect(rows[7]).toMatchObject({ oldLine: 113, newLine: 113 });
  });
});
