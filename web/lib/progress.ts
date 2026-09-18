/**
 * Which challenges have been solved.
 *
 * Two records, deliberately. localStorage is what an anonymous learner gets
 * and is a per-viewer convenience only. GET /me/progress is the signed-in
 * record, and it is the one that follows you between devices.
 *
 * They are merged, never chosen between, because the common path is solving a
 * few challenges signed out and then signing in: dropping the local set at
 * that moment would look like losing your work. Neither is trusted for
 * anything -- both drive ✓ marks and "next up", and the grader is the only
 * thing that decides whether a challenge is actually solved.
 */

import { getProgress } from "./api";

const SOLVED_KEY = "bugforge:solved";

export function readSolved(): Set<string> {
  try {
    const raw = window.localStorage.getItem(SOLVED_KEY);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

export function markSolved(challengeId: string): void {
  try {
    const solved = readSolved();
    solved.add(challengeId);
    window.localStorage.setItem(SOLVED_KEY, JSON.stringify([...solved]));
  } catch {
    // storage unavailable (private mode, blocked): nothing to remember
  }
}

/**
 * The local set plus the server's, when signed in. Never rejects: a failure
 * here degrades to the local record rather than to an empty course page.
 */
export async function loadSolved(repo?: string): Promise<Set<string>> {
  const local = readSolved();
  try {
    const remote = await getProgress(repo);
    const ids = remote.solved_ids ?? remote.solved.map((s) => s.challenge_id);
    // Fold the server's set back into localStorage so the marks survive the
    // next load even before /me/progress answers.
    ids.forEach((id) => local.add(id));
    if (remote.signed_in && ids.length) writeSolved(local);
  } catch {
    // signed out, API down, or auth not deployed: local is the answer
  }
  return local;
}

function writeSolved(solved: Set<string>): void {
  try {
    window.localStorage.setItem(SOLVED_KEY, JSON.stringify([...solved]));
  } catch {
    // storage unavailable
  }
}

export function readLocal<T>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function writeLocal(key: string, value: unknown): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage full or unavailable
  }
}
