/**
 * One submission attempt, from "sending" to a verdict. Split out of the solve
 * screen so the bottom panel can render the log without importing the screen.
 */

import type { Submission } from "./api";

export interface Attempt {
  n: number;
  files: string[];
  added: number;
  removed: number;
  sentAt: number;
  submissionId: string | null;
  state: "blocked" | "sending" | "grading" | "done" | "error";
  message: string | null;
  result: Submission | null;
}

/** How long before the log admits the grader is being slow. */
export const SLOW_AFTER_MS = 75_000;

export const REJECTION: Record<string, string> = {
  anti_cheat: "rejected before running: the patch breaks the rules",
  patch_did_not_apply: "rejected: the patch did not apply to the challenge tree",
};

export function countLines(patch: string): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const line of patch.split("\n")) {
    if (line.startsWith("+") && !line.startsWith("+++")) added++;
    if (line.startsWith("-") && !line.startsWith("---")) removed++;
  }
  return { added, removed };
}
