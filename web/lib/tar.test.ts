import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { gunzip, parsePax, untar } from "./tar";
import { buildTree, toNodes } from "./tree";

const fixture = (name: string) => new URL(`./__fixtures__/${name}`, import.meta.url);

async function load(format: string) {
  return untar(await gunzip(readFileSync(fixture(`tree-${format}.tar.gz`))));
}

describe.each(["pax", "gnu", "ustar"])("untar of a Python tarfile %s archive", (format) => {
  it("lists exactly what tarfile itself reads from the same bytes", async () => {
    const expected = JSON.parse(readFileSync(fixture(`tree-${format}.expected.json`), "utf8"));
    const actual = (await load(format)).map((f) => ({
      path: f.path,
      size: f.data.length,
      sha256: createHash("sha256").update(f.data).digest("hex"),
    }));
    expect(actual).toEqual(expected);
  });
});

function paxRecord(key: string, value: string): string {
  const body = ` ${key}=${value}\n`;
  const bodyBytes = Buffer.byteLength(body);
  let total = bodyBytes + String(bodyBytes).length;
  if (String(total).length !== String(bodyBytes).length) total = bodyBytes + String(total).length;
  return `${total}${body}`;
}

describe("parsePax", () => {
  it("measures record lengths in bytes, not characters", () => {
    const data = new TextEncoder().encode(paxRecord("path", "naïve/ünï.py") + paxRecord("mtime", "1.5"));
    expect(parsePax(data)).toEqual({ path: "naïve/ünï.py", mtime: "1.5" });
  });
});

describe("buildTree", () => {
  it("strips the root, hides git internals, and extracts traceback.txt", async () => {
    const tree = buildTree(await load("pax"));
    expect(tree.traceback).toBe("tests/test_x.py:3: AssertionError\n");
    expect(tree.files.has("traceback.txt")).toBe(false);
    expect(tree.files.has(".git/HEAD")).toBe(false);
    expect(tree.files.get("pkg/retry.py")?.text).toBe("def retry(n):\n    return n < 3\n");
    expect(tree.files.get("pkg/windows.py")?.text).toBe("x = 1\r\ny = 2\r\n");
    expect(tree.files.get("docs/naïve-ünicøde.txt")?.text).toBe("café\n");
    expect(tree.files.get("exact_block.bin")?.text).toBeNull();
    expect([...tree.files.keys()].some((p) => p.startsWith("challenge/"))).toBe(false);
  });

  it("orders directories before files", () => {
    const nodes = toNodes(["setup.py", "pkg/b.py", "pkg/a.py", "README"]);
    expect(nodes.map((n) => n.name)).toEqual(["pkg", "README", "setup.py"]);
    expect(nodes[0].children!.map((n) => n.path)).toEqual(["pkg/a.py", "pkg/b.py"]);
  });
});
