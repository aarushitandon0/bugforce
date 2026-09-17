/**
 * Which challenges this browser has solved. There are no accounts, so this is
 * a per-viewer convenience only: it drives the ✓ marks and "next up" on a
 * course page and is never trusted for anything.
 */

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
