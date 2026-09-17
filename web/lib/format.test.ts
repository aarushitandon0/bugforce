import { describe, expect, it } from "vitest";
import {
  byteRangeToIndices,
  clock,
  normalizeRepoUrl,
  parseRepoInput,
  plural,
  repoDisplay,
  repoShort,
  slug,
  thousands,
  truncateLeft,
} from "./format";

describe("parseRepoInput", () => {
  it.each([
    ["jd/tenacity", "jd/tenacity"],
    ["  github.com/jd/tenacity  ", "jd/tenacity"],
    ["https://github.com/jd/tenacity", "jd/tenacity"],
    ["https://www.github.com/jd/tenacity.git/", "jd/tenacity"],
    ["git@github.com:jd/tenacity.git", "jd/tenacity"],
    ["python-jsonschema/jsonschema", "python-jsonschema/jsonschema"],
    ["theskumar/python-dotenv", "theskumar/python-dotenv"],
    ["https://gitlab.com/jd/tenacity", null],
    ["jd", null],
    ["jd/tenacity/tree/main", null],
    ["", null],
  ])("%s -> %s", (input, expected) => {
    expect(parseRepoInput(input)).toBe(expected);
  });

  it("normalises URLs for comparison with the vetted list", () => {
    expect(normalizeRepoUrl("https://github.com/JD/Tenacity.git")).toBe("jd/tenacity");
  });
});

describe("byteRangeToIndices", () => {
  it("is the identity for ASCII", () => {
    expect(byteRangeToIndices("    return a < b", 13, 14)).toEqual([13, 14]);
  });

  it("converts UTF-8 byte offsets past multi-byte characters", () => {
    // "é" is 2 bytes, "💥" is 4 bytes and 2 UTF-16 units
    const line = "x = 'é💥' < y";
    const byteStart = new TextEncoder().encode("x = 'é💥' ").length;
    const [start, end] = byteRangeToIndices(line, byteStart, byteStart + 1);
    expect(line.slice(start, end)).toBe("<");
  });
});

describe("formatting", () => {
  it("clock", () => {
    expect(clock(0)).toBe("00:00");
    expect(clock(271_900)).toBe("04:31");
    expect(clock(3_723_000)).toBe("1:02:03");
  });

  it("names", () => {
    expect(repoDisplay("jd__tenacity")).toBe("jd/tenacity");
    expect(repoShort("jd__tenacity")).toBe("tenacity");
    expect(slug("Retry Storm!")).toBe("retry-storm");
    expect(slug("Return in __init__")).toBe("return-in-init");
  });

  it("numbers and text", () => {
    expect(thousands(2847)).toBe("2,847");
    expect(plural(1, "test")).toBe("1 test");
    expect(plural(41, "test")).toBe("41 tests");
    expect(truncateLeft("tenacity/asyncio/__init__.py", 12)).toBe("…/__init__.py");
  });
});
