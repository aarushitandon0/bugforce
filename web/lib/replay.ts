/**
 * Investigation replay: what the learner actually read, against the path the
 * defect actually took.
 *
 * The learner's line comes from the file-open log kept while solving; the
 * causal line is the traceback walked upward from the frame that raised to the
 * file that was mutated. The gap between them is the whole point of the
 * picture, so nothing here smooths it over.
 */

export interface Visit {
  path: string;
  /** epoch ms */
  at: number;
}

export interface Segment {
  path: string;
  from: number;
  to: number;
}

/** Consecutive visits to the same file are one stay; zero-length stays are dropped. */
export function toSegments(visits: Visit[], endedAt: number): Segment[] {
  const ordered = [...visits].sort((a, b) => a.at - b.at).filter((v) => v.at <= endedAt);
  const segments: Segment[] = [];
  for (const visit of ordered) {
    const last = segments[segments.length - 1];
    if (last && last.path === visit.path) continue;
    if (last) last.to = visit.at;
    segments.push({ path: visit.path, from: visit.at, to: endedAt });
  }
  return segments.filter((s) => s.to > s.from || segments.length === 1);
}

/** Total time per file, longest first. */
export function dwell(segments: Segment[]): { path: string; ms: number }[] {
  const totals = new Map<string, number>();
  for (const s of segments) totals.set(s.path, (totals.get(s.path) ?? 0) + (s.to - s.from));
  return [...totals].map(([path, ms]) => ({ path, ms })).sort((a, b) => b.ms - a.ms || a.path.localeCompare(b.path));
}

/**
 * The causal path as files: from the frame that raised, up the trace, ending
 * at the mutated file. `frames` is outermost-first, as pytest prints it.
 */
export function causalPath(frames: string[], mutatedPath: string): string[] {
  const upward: string[] = [];
  for (const path of [...frames].reverse()) {
    if (upward[upward.length - 1] !== path) upward.push(path);
    if (path === mutatedPath) break;
  }
  if (upward[upward.length - 1] !== mutatedPath) upward.push(mutatedPath);
  return upward;
}

/** Lane order: the causal path first (top to bottom), then everything else the learner opened. */
export function lanes(segments: Segment[], causal: string[]): string[] {
  const order = [...causal];
  for (const s of segments) if (!order.includes(s.path)) order.push(s.path);
  return order;
}

const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

export function frameWord(n: number): string {
  if (n <= 0) return "no";
  return n <= 10 ? WORDS[n] : String(n);
}

/** "9 of 14 minutes", or seconds when the whole attempt was under two minutes. */
export function timeSpent(partMs: number, totalMs: number): string {
  const unit = totalMs < 120_000 ? "second" : "minute";
  const div = unit === "second" ? 1000 : 60_000;
  const part = Math.round(partMs / div);
  const total = Math.round(totalMs / div);
  return `${part} of ${total} ${total === 1 ? unit : `${unit}s`}`;
}

export interface Verdict {
  segments: Segment[];
  causal: string[];
  mutatedPath: string;
  /** frames between the failure and the defect, from the challenge's own score */
  displacement: number;
  totalMs: number;
}

/**
 * The one generated line under the chart. It states where the time went and
 * where the bug was, and says so plainly when those are the same place.
 */
export function describeReplay(v: Verdict): string {
  const ranking = dwell(v.segments);
  const top = ranking[0];
  const surfaced = v.causal[0];
  const upstream = v.displacement > 0 ? `, ${frameWord(v.displacement)} ${v.displacement === 1 ? "frame" : "frames"} upstream` : "";

  if (!top) {
    return `You submitted without opening a file. The bug was in ${v.mutatedPath}${upstream}.`;
  }

  const spent = timeSpent(top.ms, v.totalMs);
  const openedBug = ranking.some((r) => r.path === v.mutatedPath);

  if (top.path === v.mutatedPath) {
    return `You spent ${spent} in ${v.mutatedPath}, the file that was actually broken${upstream}. You were reading the right file.`;
  }

  const where = top.path === surfaced ? ", where the error surfaced" : "";
  const never = openedBug ? "" : ", which you never opened";
  return `You spent ${spent} in ${top.path}${where}. The bug was in ${v.mutatedPath}${upstream}${never}.`;
}

/** The lane every other file collapses into once the chart is full. "*" cannot appear in a path. */
export const ELSEWHERE = "*elsewhere*";

export interface LanePlan {
  lanes: string[];
  laneOf: (path: string) => number;
  othersCount: number;
}

/**
 * Lanes for the chart: the causal path always keeps its own, then the files
 * the learner spent longest in. Anything past the cap shares one lane, so a
 * long rummage still draws as one continuous line.
 */
export function planLanes(segments: Segment[], causal: string[], max = 12): LanePlan {
  const shown: string[] = [];
  for (const path of causal) if (!shown.includes(path)) shown.push(path);
  const rest = dwell(segments)
    .map((d) => d.path)
    .filter((p) => !shown.includes(p));

  const room = Math.max(0, max - shown.length);
  const kept = rest.slice(0, rest.length > room ? Math.max(0, room - 1) : room);
  const othersCount = rest.length - kept.length;
  const lanes = [...shown, ...kept, ...(othersCount > 0 ? [ELSEWHERE] : [])];
  const index = new Map(lanes.map((path, i) => [path, i]));
  return {
    lanes,
    laneOf: (path) => index.get(path) ?? index.get(ELSEWHERE) ?? 0,
    othersCount,
  };
}
