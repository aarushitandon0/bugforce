import { describe, expect, it } from "vitest";
import type { ChallengeCard, ProgressResponse } from "./api";
import { ago, byRepo, calendarWeeks, dayKey, DAY_MS, mergeSolves, streaks, tally } from "./profile";

function card(id: string, band: "easy" | "medium" | "hard", repo = "jd__tenacity"): ChallengeCard {
  return {
    challenge_id: id,
    repo,
    repo_url: `https://github.com/${repo}`,
    license: "Apache-2.0",
    language: "Python",
    title: `title ${id}`,
    description: "",
    difficulty_score: 5,
    difficulty_label: band,
    breakdown: null,
    failing_test_count: 1,
    total_tests: 183,
  };
}

const NOW = new Date(2026, 8, 20, 12, 0, 0).getTime();

describe("ago", () => {
  it("counts in the largest unit that fits", () => {
    expect(ago(NOW - 30_000, NOW)).toBe("just now");
    expect(ago(NOW - 5 * 60_000, NOW)).toBe("5 minutes ago");
    expect(ago(NOW - 3 * 3_600_000, NOW)).toBe("3 hours ago");
    expect(ago(NOW - 6 * DAY_MS, NOW)).toBe("6 days ago");
    expect(ago(NOW - 21 * DAY_MS, NOW)).toBe("3 weeks ago");
  });

  it("singularises", () => {
    expect(ago(NOW - DAY_MS, NOW)).toBe("1 day ago");
  });

  it("never reports a negative age for a clock that is slightly ahead", () => {
    expect(ago(NOW + 5_000, NOW)).toBe("just now");
  });
});

describe("streaks", () => {
  it("is zero for no activity", () => {
    expect(streaks([], NOW)).toEqual({ longest: 0, current: 0 });
  });

  it("counts a consecutive run", () => {
    const today = dayKey(NOW);
    const days = [today - 2 * DAY_MS, today - DAY_MS, today];
    expect(streaks(days, NOW)).toEqual({ longest: 3, current: 3 });
  });

  it("treats two solves on one day as one day", () => {
    const today = dayKey(NOW);
    expect(streaks([today, today], NOW)).toEqual({ longest: 1, current: 1 });
  });

  it("keeps a streak current through yesterday, so an unfinished day does not break it", () => {
    const today = dayKey(NOW);
    expect(streaks([today - DAY_MS], NOW).current).toBe(1);
  });

  it("reports an old run as longest but not current", () => {
    const today = dayKey(NOW);
    const days = [today - 30 * DAY_MS, today - 29 * DAY_MS, today - 28 * DAY_MS];
    expect(streaks(days, NOW)).toEqual({ longest: 3, current: 0 });
  });
});

describe("mergeSolves", () => {
  const byId = new Map([
    ["a", card("a", "easy")],
    ["b", card("b", "hard")],
  ]);

  it("keeps a local-only solve, with no timestamp", () => {
    const merged = mergeSolves(["a"], null, byId);
    expect(merged).toHaveLength(1);
    expect(merged[0].at).toBeNull();
    expect(merged[0].band).toBe("easy");
    expect(merged[0].title).toBe("title a");
  });

  it("lets the server's row win, because only it has a time", () => {
    const progress: ProgressResponse = {
      solved: [{ challenge_id: "a", repo: "jd__tenacity", solved_at: 1_700_000_000, seconds: 42 }],
      count: 1,
      signed_in: true,
    };
    const merged = mergeSolves(["a"], progress, byId);
    expect(merged).toHaveLength(1);
    expect(merged[0].at).toBe(1_700_000_000_000);
    expect(merged[0].seconds).toBe(42);
  });

  it("unions the two records rather than choosing one", () => {
    const progress: ProgressResponse = {
      solved: [{ challenge_id: "b", repo: "jd__tenacity", solved_at: 1_700_000_000 }],
      count: 1,
      signed_in: true,
    };
    expect(mergeSolves(["a"], progress, byId).map((s) => s.challengeId).sort()).toEqual(["a", "b"]);
  });

  it("sorts newest first, with undated solves last", () => {
    const progress: ProgressResponse = {
      solved: [{ challenge_id: "b", repo: "jd__tenacity", solved_at: 1_700_000_000 }],
      count: 1,
      signed_in: true,
    };
    expect(mergeSolves(["a"], progress, byId)[0].challengeId).toBe("b");
  });

  it("falls back to the id when the challenge is not in the catalogue", () => {
    const merged = mergeSolves(["gone"], null, byId);
    expect(merged[0].title).toBe("gone");
    expect(merged[0].band).toBeNull();
  });
});

describe("tally", () => {
  it("counts totals per band and how many are solved", () => {
    const catalogue = [card("a", "easy"), card("b", "easy"), card("c", "hard")];
    const solves = mergeSolves(["a", "c"], null, new Map(catalogue.map((c) => [c.challenge_id, c])));
    expect(tally(catalogue, solves)).toEqual({
      total: { easy: 2, medium: 0, hard: 1 },
      solved: { easy: 1, medium: 0, hard: 1 },
    });
  });
});

describe("byRepo", () => {
  it("ranks repos by solve count and drops unknown repos", () => {
    const catalogue = [card("a", "easy", "one"), card("b", "easy", "two"), card("c", "easy", "two")];
    const solves = mergeSolves(
      ["a", "b", "c", "orphan"],
      null,
      new Map(catalogue.map((c) => [c.challenge_id, c])),
    );
    expect(byRepo(solves)).toEqual([
      ["two", 2],
      ["one", 1],
    ]);
  });
});

describe("calendarWeeks", () => {
  it("is a year of seven-day columns starting on Sunday", () => {
    const weeks = calendarWeeks(NOW);
    expect(weeks.length).toBeGreaterThanOrEqual(52);
    expect(weeks.every((w) => w.length === 7)).toBe(true);
    expect(weeks.every((w) => new Date(w[0]).getDay() === 0)).toBe(true);
  });

  it("includes today", () => {
    expect(calendarWeeks(NOW).flat()).toContain(dayKey(NOW));
  });
});
