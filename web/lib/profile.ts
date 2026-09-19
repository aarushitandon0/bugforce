/**
 * The arithmetic behind the profile screen, kept out of the component so it
 * can be tested without a DOM.
 *
 * Nothing here fetches or writes. It folds two records that already exist --
 * the server's solved list and the browser's -- into the counts, streaks and
 * calendar the screen draws.
 */

import type { ChallengeCard, DifficultyLabel, ProgressResponse } from "./api";
import { plural } from "./format";

export const DAY_MS = 86_400_000;

export const BANDS: DifficultyLabel[] = ["easy", "medium", "hard"];

export interface Solve {
  challengeId: string;
  repo: string;
  band: DifficultyLabel | null;
  title: string;
  /** epoch ms, or null when the solve is only in this browser's localStorage */
  at: number | null;
  seconds?: number;
}

/** "6 days ago", "3 hours ago", "just now" -- the recent list's only time format. */
export function ago(ms: number, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - ms) / 1000));
  const units: [number, string][] = [
    [60, "minute"],
    [3600, "hour"],
    [86400, "day"],
    [604800, "week"],
    [2629800, "month"],
  ];
  for (let i = units.length - 1; i >= 0; i--) {
    const [size, name] = units[i];
    if (seconds >= size) return `${plural(Math.floor(seconds / size), name)} ago`;
  }
  return "just now";
}

/** Midnight local time, so two solves on the same calendar day share a cell. */
export function dayKey(ms: number): number {
  const d = new Date(ms);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/**
 * The longest run of consecutive active days, and whether it is still running.
 *
 * Counted on calendar days rather than on 24-hour spans, so two solves either
 * side of midnight are two days. A streak stays "current" through today and
 * yesterday: a day that is not over yet must not read as a broken streak.
 */
export function streaks(days: number[], now: number = Date.now()): { longest: number; current: number } {
  if (days.length === 0) return { longest: 0, current: 0 };
  const sorted = [...new Set(days)].sort((a, b) => a - b);
  let longest = 1;
  let run = 1;
  for (let i = 1; i < sorted.length; i++) {
    run = sorted[i] - sorted[i - 1] === DAY_MS ? run + 1 : 1;
    longest = Math.max(longest, run);
  }
  const today = dayKey(now);
  const last = sorted[sorted.length - 1];
  return { longest, current: last === today || last === today - DAY_MS ? run : 0 };
}

/**
 * The server's solved record merged with the browser's, newest first.
 *
 * Merged, never chosen between. The common path is solving a few signed out
 * and then signing in, and dropping the local set at that moment would look
 * like losing your work; lib/progress.ts makes the same choice for the tick
 * marks. The server's row wins on conflict because only it carries a
 * timestamp and a duration.
 */
export function mergeSolves(
  localSolved: Iterable<string>,
  progress: ProgressResponse | null,
  byId: Map<string, ChallengeCard>,
): Solve[] {
  const seen = new Map<string, Solve>();
  for (const id of localSolved) {
    const c = byId.get(id);
    seen.set(id, {
      challengeId: id,
      repo: c?.repo ?? "",
      band: c?.difficulty_label ?? null,
      title: c?.title ?? id,
      at: null,
    });
  }
  for (const row of progress?.solved ?? []) {
    const c = byId.get(row.challenge_id);
    seen.set(row.challenge_id, {
      challengeId: row.challenge_id,
      repo: row.repo || (c?.repo ?? ""),
      band: c?.difficulty_label ?? null,
      title: c?.title ?? row.challenge_id,
      // The API sends epoch seconds; everything here is milliseconds.
      at: row.solved_at ? row.solved_at * 1000 : null,
      seconds: row.seconds,
    });
  }
  return [...seen.values()].sort((a, b) => (b.at ?? 0) - (a.at ?? 0));
}

export interface Tally {
  total: Record<DifficultyLabel, number>;
  solved: Record<DifficultyLabel, number>;
}

/** How many of each band exist, and how many of them are solved. */
export function tally(catalogue: ChallengeCard[], solves: Solve[]): Tally {
  const total: Record<DifficultyLabel, number> = { easy: 0, medium: 0, hard: 0 };
  for (const c of catalogue) total[c.difficulty_label] += 1;
  const solved: Record<DifficultyLabel, number> = { easy: 0, medium: 0, hard: 0 };
  for (const s of solves) if (s.band) solved[s.band] += 1;
  return { total, solved };
}

/** Solves per repo, most solved first. */
export function byRepo(solves: Solve[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const s of solves) if (s.repo) counts.set(s.repo, (counts.get(s.repo) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

/**
 * A year of calendar columns, one per week, each seven days starting Sunday.
 * Days past today are included so the last column keeps its shape; the caller
 * renders those blank.
 */
export function calendarWeeks(now: number = Date.now()): number[][] {
  const today = dayKey(now);
  const start = today - 363 * DAY_MS;
  const firstSunday = start - new Date(start).getDay() * DAY_MS;
  const weeks: number[][] = [];
  for (let w = 0; firstSunday + w * 7 * DAY_MS <= today; w++) {
    const column: number[] = [];
    for (let d = 0; d < 7; d++) column.push(firstSunday + (w * 7 + d) * DAY_MS);
    weeks.push(column);
  }
  return weeks;
}
