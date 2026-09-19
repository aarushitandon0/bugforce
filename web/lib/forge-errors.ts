/**
 * What went wrong, and what you can do about it.
 *
 * A failed forge used to render as `✗ forge failed · <whatever the state
 * machine said>`, which is a log line, not an answer. Each entry here says
 * what happened in the reader's terms and what the next move is.
 *
 * A note on what is NOT in this list, because it is the interesting part:
 * "no test suite", "not a Python repo", "dependency install failed" and "repo
 * too large" cannot reach a reader. BugForge never clones or installs at
 * request time -- every forgeable repo is a container image built ahead of
 * time on a trusted machine (see cloud/workspace.py), so those failures
 * happen weeks earlier, at image-build time, and the only runtime answer is
 * "there is no image for that repo", which the landing page already gives in
 * full. Entries for them are kept below anyway: if the pipeline ever grows a
 * clone-at-runtime path, the copy is already written and the matcher will
 * find it. `UNREACHABLE_TODAY` marks them so nobody mistakes them for live
 * behaviour.
 */

export interface ForgeError {
  /** the headline, lowercase, in the stream's voice */
  what: string;
  /** one line: what the reader can actually do */
  next: string;
}

const UNREACHABLE_TODAY: Record<string, ForgeError> = {
  no_test_suite: {
    what: "that repo has no test suite we can run",
    next: "BugForge grades with the repo's own tests, so a repo without them has nothing to grade against.",
  },
  not_python: {
    what: "that repo is not in a language BugForge mutates yet",
    next: "Python and Go are supported. Try one of the forgeable repos on the landing page.",
  },
  install_failed: {
    what: "the repo's dependencies would not install",
    next: "Nothing to retry — this is fixed when the repo's image is built, not at forge time.",
  },
  repo_too_large: {
    what: "that repo is too large to forge",
    next: "The tree has to be copied once per bug. Try a smaller repo.",
  },
};

const LIVE: Record<string, ForgeError> = {
  suite_timed_out: {
    what: "the repo's test suite ran out of time",
    next: "The suite is run once per candidate and a slow suite exhausts the budget. Forge it again — a warm image usually finishes.",
  },
  not_found: {
    what: "that repo is private, or does not exist",
    next: "BugForge only reads public repos, and only ones an image was built for.",
  },
  aborted: {
    what: "the forge was stopped before it finished",
    next: "Nothing was written. Start it again.",
  },
  failed: {
    what: "the forge failed part way through",
    next: "Nothing partial was written — a forge only publishes bugs once every batch has been graded. Start it again, and if it fails twice the image is the problem, not the repo.",
  },
};

const TABLE: Record<string, ForgeError> = { ...UNREACHABLE_TODAY, ...LIVE };

/* Matched against the state machine's error and cause, which are free text.
   Ordered: the first pattern that hits wins, so the specific ones come first. */
const PATTERNS: Array<[RegExp, string]> = [
  [/timed?[ _-]?out|timeout/i, "suite_timed_out"],
  [/unvetted|no image|not found|404/i, "not_found"],
  [/no tests? (were )?(collected|found)|no test suite/i, "no_test_suite"],
  [/not a python|unsupported language/i, "not_python"],
  [/pip|install|dependenc/i, "install_failed"],
  [/too large|disk|no space/i, "repo_too_large"],
];

/**
 * @param status  the state machine's terminal status
 * @param detail  error and cause, joined; either may be null
 */
export function forgeError(status: string, detail: string): ForgeError {
  if (status === "ABORTED") return TABLE.aborted;
  if (status === "TIMED_OUT") return TABLE.suite_timed_out;
  for (const [pattern, key] of PATTERNS) if (pattern.test(detail)) return TABLE[key];
  return TABLE.failed;
}
