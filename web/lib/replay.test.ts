import { describe, expect, it } from "vitest";
import { causalPath, describeReplay, dwell, ELSEWHERE, lanes, planLanes, timeSpent, toSegments, type Visit } from "./replay";

const T0 = 1_700_000_000_000;
const min = (n: number) => T0 + n * 60_000;

const VISITS: Visit[] = [
  { path: "tests/test_api.py", at: min(0) },
  { path: "handlers.py", at: min(1) },
  { path: "handlers.py", at: min(3) }, // re-opened the same tab: still one stay
  { path: "cache/store.py", at: min(10) },
  { path: "handlers.py", at: min(12) },
];

describe("toSegments", () => {
  it("turns opens into stays that run until the next open", () => {
    expect(toSegments(VISITS, min(14))).toEqual([
      { path: "tests/test_api.py", from: min(0), to: min(1) },
      { path: "handlers.py", from: min(1), to: min(10) },
      { path: "cache/store.py", from: min(10), to: min(12) },
      { path: "handlers.py", from: min(12), to: min(14) },
    ]);
  });

  it("sorts by time and ignores opens after the submission", () => {
    const segments = toSegments([{ path: "b.py", at: min(2) }, { path: "a.py", at: min(1) }, { path: "c.py", at: min(9) }], min(3));
    expect(segments.map((s) => s.path)).toEqual(["a.py", "b.py"]);
  });

  it("keeps a single stay that has no successor", () => {
    expect(toSegments([{ path: "a.py", at: min(0) }], min(0))).toEqual([{ path: "a.py", from: min(0), to: min(0) }]);
  });

  it("has nothing to draw for an empty log", () => {
    expect(toSegments([], min(1))).toEqual([]);
  });
});

describe("dwell", () => {
  it("adds up revisits and ranks by time", () => {
    expect(dwell(toSegments(VISITS, min(14)))).toEqual([
      { path: "handlers.py", ms: 11 * 60_000 },
      { path: "cache/store.py", ms: 2 * 60_000 },
      { path: "tests/test_api.py", ms: 1 * 60_000 },
    ]);
  });
});

describe("causalPath", () => {
  const frames = ["tests/test_api.py", "app.py", "handlers.py", "cache/store.py", "handlers.py"];

  it("walks upward from the frame that raised to the mutated file", () => {
    // frames are outermost-first, so the walk starts at the end
    expect(causalPath(frames, "cache/store.py")).toEqual(["handlers.py", "cache/store.py"]);
  });

  it("appends the mutated file when the trace never reaches it", () => {
    expect(causalPath(["tests/test_api.py", "handlers.py"], "cache/store.py")).toEqual([
      "handlers.py",
      "tests/test_api.py",
      "cache/store.py",
    ]);
  });

  it("collapses repeated frames in the same file", () => {
    expect(causalPath(["a.py", "b.py", "b.py", "b.py"], "a.py")).toEqual(["b.py", "a.py"]);
  });
});

describe("lanes", () => {
  it("puts the causal path first, then anything else that was opened", () => {
    expect(lanes(toSegments(VISITS, min(14)), ["handlers.py", "cache/store.py"])).toEqual([
      "handlers.py",
      "cache/store.py",
      "tests/test_api.py",
    ]);
  });
});

describe("timeSpent", () => {
  it("uses minutes for a long attempt", () => {
    expect(timeSpent(9 * 60_000, 14 * 60_000)).toBe("9 of 14 minutes");
  });

  it("uses seconds when the whole attempt was short", () => {
    expect(timeSpent(30_000, 50_000)).toBe("30 of 50 seconds");
  });

  it("makes the unit singular for one", () => {
    expect(timeSpent(1000, 1000)).toBe("1 of 1 second");
  });
});

describe("describeReplay", () => {
  const base = {
    segments: toSegments(VISITS, min(14)),
    causal: ["handlers.py", "cache/store.py"],
    mutatedPath: "cache/store.py",
    displacement: 3,
    totalMs: 14 * 60_000,
  };

  it("states where the time went and where the bug was", () => {
    expect(describeReplay(base)).toBe(
      "You spent 11 of 14 minutes in handlers.py, where the error surfaced. The bug was in cache/store.py, three frames upstream.",
    );
  });

  it("says so when the learner never opened the broken file", () => {
    const segments = toSegments([{ path: "handlers.py", at: min(0) }], min(14));
    expect(describeReplay({ ...base, segments })).toBe(
      "You spent 14 of 14 minutes in handlers.py, where the error surfaced. The bug was in cache/store.py, three frames upstream, which you never opened.",
    );
  });

  it("credits reading the right file", () => {
    const segments = toSegments([{ path: "cache/store.py", at: min(0) }], min(14));
    expect(describeReplay({ ...base, segments })).toBe(
      "You spent 14 of 14 minutes in cache/store.py, the file that was actually broken, three frames upstream. You were reading the right file.",
    );
  });

  it("drops the upstream clause when the bug was in the failing frame", () => {
    const segments = toSegments([{ path: "handlers.py", at: min(0) }], min(14));
    expect(describeReplay({ ...base, segments, mutatedPath: "handlers.py", displacement: 0 })).toBe(
      "You spent 14 of 14 minutes in handlers.py, the file that was actually broken. You were reading the right file.",
    );
  });

  it("omits the surfaced clause for a file that is not where it failed", () => {
    const segments = toSegments([{ path: "tests/test_api.py", at: min(0) }], min(14));
    expect(describeReplay({ ...base, segments, displacement: 1 })).toBe(
      "You spent 14 of 14 minutes in tests/test_api.py. The bug was in cache/store.py, one frame upstream, which you never opened.",
    );
  });

  it("handles a submission with no files opened at all", () => {
    expect(describeReplay({ ...base, segments: [] })).toBe(
      "You submitted without opening a file. The bug was in cache/store.py, three frames upstream.",
    );
  });
});

describe("planLanes", () => {
  const segmentsFor = (paths: string[]) =>
    toSegments(
      paths.map((path, i) => ({ path, at: min(i) })),
      min(paths.length),
    );

  it("gives the causal path its own lanes, then the longest stays", () => {
    const plan = planLanes(segmentsFor(["a.py", "b.py", "c.py"]), ["c.py", "z.py"], 12);
    expect(plan.lanes).toEqual(["c.py", "z.py", "a.py", "b.py"]);
    expect(plan.othersCount).toBe(0);
    expect(plan.laneOf("b.py")).toBe(3);
  });

  it("collapses the overflow into one shared lane", () => {
    const paths = Array.from({ length: 10 }, (_, i) => `f${i}.py`);
    const plan = planLanes(segmentsFor(paths), ["c.py"], 5);
    expect(plan.lanes).toHaveLength(5);
    expect(plan.lanes[4]).toBe(ELSEWHERE);
    expect(plan.othersCount).toBe(7);
    // an unlisted file still draws, in the shared lane
    expect(plan.laneOf("f9.py")).toBe(4);
  });
});
