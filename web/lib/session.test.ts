/**
 * The two pure pieces of the sign-in flow that run in the browser: cleaning
 * the callback's query parameters off the URL, and merging the server's solved
 * set into the local one.
 *
 * `environment: "node"`, so `window` is stubbed per test rather than assumed.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { consumeAuthParams } from "./session";

interface FakeWindow {
  location: { href: string };
  history: { replaceState: (a: unknown, b: string, url: string) => void };
}

function stubWindow(href: string): { replaced: string[]; win: FakeWindow } {
  const replaced: string[] = [];
  const win: FakeWindow = {
    location: { href },
    history: { replaceState: (_a, _b, url) => void replaced.push(url) },
  };
  vi.stubGlobal("window", win);
  return { replaced, win };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("consumeAuthParams", () => {
  it("reports a successful sign-in and strips the marker", () => {
    const { replaced } = stubWindow("https://app.example/repo/?name=tenacity&signed_in=1");

    expect(consumeAuthParams()).toEqual({ signedIn: true, error: null });
    expect(replaced).toEqual(["/repo/?name=tenacity"]);
  });

  it("reports an error and strips it", () => {
    const { replaced } = stubWindow("https://app.example/?auth_error=sign-in%20expired");

    expect(consumeAuthParams()).toEqual({ signedIn: false, error: "sign-in expired" });
    expect(replaced).toEqual(["/"]);
  });

  it("leaves a URL with neither marker alone", () => {
    const { replaced } = stubWindow("https://app.example/solve/?c=abc");

    expect(consumeAuthParams()).toEqual({ signedIn: false, error: null });
    expect(replaced).toEqual([]);
  });

  it("keeps every other query parameter and the hash", () => {
    const { replaced } = stubWindow("https://app.example/solve/?c=abc&signed_in=1#L42");

    consumeAuthParams();
    expect(replaced).toEqual(["/solve/?c=abc#L42"]);
  });

  it("is inert during server rendering, where there is no window", () => {
    vi.stubGlobal("window", undefined);
    expect(consumeAuthParams()).toEqual({ signedIn: false, error: null });
  });
});

describe("loadSolved", () => {
  function stubStorage(initial: string | null) {
    const store: Record<string, string | null> = { "bugforge:solved": initial };
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (k: string) => store[k] ?? null,
        setItem: (k: string, v: string) => void (store[k] = v),
        removeItem: (k: string) => void (store[k] = null),
      },
    });
    return store;
  }

  async function withProgress(impl: () => Promise<unknown>) {
    vi.resetModules();
    vi.doMock("./api", () => ({ getProgress: impl }));
    return (await import("./progress")).loadSolved;
  }

  it("merges the server's solves into the local set", async () => {
    stubStorage(JSON.stringify(["local-1"]));
    const loadSolved = await withProgress(async () => ({
      solved: [],
      solved_ids: ["remote-1", "remote-2"],
      count: 2,
      signed_in: true,
    }));

    expect([...(await loadSolved("tenacity"))].sort()).toEqual(["local-1", "remote-1", "remote-2"]);
  });

  it("writes the merged set back so the marks survive the next load", async () => {
    const store = stubStorage(JSON.stringify(["local-1"]));
    const loadSolved = await withProgress(async () => ({
      solved: [{ challenge_id: "remote-1", repo: "r", solved_at: 1 }],
      count: 1,
      signed_in: true,
    }));

    await loadSolved();
    expect(JSON.parse(store["bugforge:solved"] as string).sort()).toEqual(["local-1", "remote-1"]);
  });

  it("falls back to the local set when the API fails", async () => {
    // Auth not deployed, API down, or simply signed out: a course page must
    // still show what this browser has solved.
    stubStorage(JSON.stringify(["local-1"]));
    const loadSolved = await withProgress(async () => {
      throw new Error("network error");
    });

    expect([...(await loadSolved())]).toEqual(["local-1"]);
  });

  it("leaves storage untouched for a signed-out reader", async () => {
    const store = stubStorage(null);
    const loadSolved = await withProgress(async () => ({
      solved: [],
      solved_ids: [],
      count: 0,
      signed_in: false,
    }));

    expect([...(await loadSolved())]).toEqual([]);
    expect(store["bugforge:solved"]).toBeNull();
  });
});
