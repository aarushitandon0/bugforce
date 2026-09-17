import { describe, expect, it } from "vitest";
import { ancestorDirs, findTestLine, parseNodeId, tabLabels } from "./solve";

const SOURCE = `import tenacity

class TestOther:
    def test_retry_with_async_result_or(self):
        pass


class TestContextManager:
    @asynctest
    async def test_retry_with_async_result_or(self) -> None:
        pass
`;

describe("parseNodeId", () => {
  it("splits path and names, dropping parametrisation", () => {
    expect(parseNodeId("tests/test_asyncio.py::TestContextManager::test_x[3-foo]")).toEqual({
      path: "tests/test_asyncio.py",
      names: ["TestContextManager", "test_x"],
    });
  });
});

describe("findTestLine", () => {
  it("finds the method inside the right class", () => {
    expect(findTestLine(SOURCE, ["TestContextManager", "test_retry_with_async_result_or"])).toBe(10);
    expect(findTestLine(SOURCE, ["TestOther", "test_retry_with_async_result_or"])).toBe(4);
  });

  it("does not match a prefix of a longer name", () => {
    expect(findTestLine("def test_ab():\n    pass\ndef test_a():\n", ["test_a"])).toBe(3);
  });

  it("returns null when absent", () => {
    expect(findTestLine(SOURCE, ["TestMissing", "test_retry_with_async_result_or"])).toBeNull();
  });
});

describe("tabLabels", () => {
  it("adds the shortest directory hint that disambiguates", () => {
    const labels = tabLabels(["tenacity/__init__.py", "tenacity/asyncio/__init__.py", "tenacity/retry.py"]);
    expect(labels.get("tenacity/retry.py")).toEqual({ name: "retry.py", hint: "" });
    expect(labels.get("tenacity/asyncio/__init__.py")).toEqual({ name: "__init__.py", hint: "asyncio" });
    expect(labels.get("tenacity/__init__.py")).toEqual({ name: "__init__.py", hint: "tenacity" });
  });
});

describe("ancestorDirs", () => {
  it("lists every parent", () => {
    expect([...ancestorDirs(["a/b/c.py", "d.py"])]).toEqual(["a", "a/b"]);
  });
});
