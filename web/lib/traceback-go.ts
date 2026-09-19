/**
 * Parses `go test -v` failure output into the same Frame[] the pytest parser
 * produces, so the spine, the gutter markers and the replay chart need to
 * know nothing about which language they are showing.
 *
 * Go gives us two quite different shapes, and a real challenge can be either.
 *
 * 1. A panic, which carries a genuine goroutine stack:
 *
 *        --- FAIL: TestParser_Parse (0.00s)
 *        panic: runtime error: index out of range [2] with length 0 [recovered]
 *
 *        goroutine 35 [running]:
 *        testing.tRunner.func1.2({0x7ff7…, 0x1a06…})
 *        	C:/Program Files/Go/src/testing/testing.go:1974 +0x239
 *        github.com/golang-jwt/jwt/v5.(*Parser).ParseWithClaims(0x1a06…, …)
 *        	/tmp/tree/parser.go:80 +0x4a5
 *        github.com/golang-jwt/jwt/v5_test.TestParser_Parse.func1(0x1a06…)
 *        	/tmp/tree/parser_test.go:475 +0x1e8
 *
 *    Frames come in pairs -- a function line, then a TAB-indented
 *    `path:line +0xoffset` -- and the goroutine stack is ordered **innermost
 *    first**, which is the opposite of a Python traceback. They are reversed
 *    here so that, as everywhere else in this app, Frame[0] is outermost and
 *    the last frame is where it blew up.
 *
 * 2. A plain assertion, which carries no stack at all:
 *
 *        --- FAIL: TestVerifyAud (0.00s)
 *            validator_test.go:123: Expected true, got false
 *
 *    That is one location, in the test file, and nothing else. It yields a
 *    single frame -- which is honest: the trace really does stop at the test,
 *    and the solve screen already has a case that says so and tells the
 *    learner to follow what the test calls.
 *
 * Paths in a panic stack are absolute and point into the temp directory the
 * suite ran in, which is exactly what `resolveFramePath` already handles by
 * longest-suffix match. Frames in GOROOT (testing.go, panic.go) match nothing
 * in the tree and so stay on the rail, dimmed -- the same treatment
 * site-packages frames get on the Python side.
 */

import type { ExcerptLine, Frame } from "./traceback";

/** `\t/path/to/file.go:80 +0x4a5` -- the location half of a stack pair. */
const STACK_LOCATION = /^\t(.+\.go):(\d+)(?: \+0x[0-9a-f]+)?$/;
/** `    validator_test.go:123: Expected true, got false` -- a t.Errorf report. */
const REPORT = /^\s+(\S+\.go):(\d+): ?(.*)$/;
const GOROUTINE = /^goroutine \d+ \[[^\]]*\]:$/;
const PANIC = /^(?:panic|fatal error):\s*(.*)$/;
const CREATED_BY = /^created by /;

/**
 * Trims Go's fully-qualified function name down to what a person reads in a
 * stack: `github.com/golang-jwt/jwt/v5.(*Parser).Parse` -> `(*Parser).Parse`.
 * The argument list is dropped with it -- in a panic dump those are raw
 * pointer values, which tell a learner nothing.
 */
export function shortFuncName(line: string): string {
  // Only the trailing argument list, anchored at the end. Stripping from the
  // first "(" instead would eat a pointer receiver: the first parenthesis in
  // `…/v5.(*Parser).ParseWithClaims(0x1)` opens `(*Parser)`, not the args.
  const withoutArgs = line.replace(/\([^()]*\)$/, "");
  const lastSlash = withoutArgs.lastIndexOf("/");
  const tail = lastSlash === -1 ? withoutArgs : withoutArgs.slice(lastSlash + 1);
  // The package name is still on the front ("v5.(*Parser).Parse", "jwt.New").
  // Drop it only when what follows still looks like a name, so a bare
  // top-level function ("main.main") keeps something to show.
  const dot = tail.indexOf(".");
  if (dot === -1) return tail;
  const rest = tail.slice(dot + 1);
  return rest.length > 0 ? rest : tail;
}

function frame(index: number, path: string, line: number, func: string | null, excerpt: ExcerptLine[]): Frame {
  return {
    index,
    path,
    line,
    func,
    exception: null,
    excerpt,
    failingSource: null,
    errors: [],
  };
}

/** Pulls the frames out of the goroutine stack that follows a panic. */
function parseGoroutineStack(lines: string[], start: number): Frame[] {
  const frames: Frame[] = [];
  let pendingFunc: string | null = null;

  for (let i = start; i < lines.length; i++) {
    const raw = lines[i];
    if (raw.trim() === "") break; // the stack ends at the first blank line
    const location = STACK_LOCATION.exec(raw);
    if (location) {
      frames.push(
        frame(0, location[1].replace(/\\/g, "/"), Number(location[2]), pendingFunc, []),
      );
      pendingFunc = null;
      continue;
    }
    if (raw.startsWith("\t")) continue; // an indented line that is not a location
    // "created by testing.(*T).Run in goroutine 34" introduces the goroutine's
    // spawn site, which is real but always the same scaffolding.
    if (CREATED_BY.test(raw)) break;
    pendingFunc = shortFuncName(raw);
  }
  return frames;
}

/**
 * Frames outermost first, deepest (where it failed) last -- the same contract
 * as parseTraceback.
 */
export function parseGoTraceback(text: string): Frame[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");

  let panicMessage: string | null = null;
  let stack: Frame[] = [];
  const reports: Frame[] = [];

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];

    const panic = PANIC.exec(raw);
    if (panic && panicMessage === null) {
      panicMessage = panic[1].replace(/\s*\[recovered.*\]$/, "").trim();
      continue;
    }
    if (GOROUTINE.test(raw) && stack.length === 0) {
      stack = parseGoroutineStack(lines, i + 1);
      continue;
    }
    // Only collect t.Errorf reports; a location inside a stack is indented
    // with a tab and already handled above.
    if (!raw.startsWith("\t")) {
      const report = REPORT.exec(raw);
      if (report) {
        const message = report[3].trim();
        reports.push({
          ...frame(0, report[1].replace(/\\/g, "/"), Number(report[2]), null, []),
          errors: message ? [message] : [],
        });
      }
    }
  }

  // A panic stack is the better story whenever there is one: it reaches from
  // the test all the way down to the code that actually broke, which is the
  // whole point of the spine. The t.Errorf reports are the fallback.
  const ordered = stack.length > 0 ? stack.slice().reverse() : reports;
  if (ordered.length === 0) return [];

  return ordered.map((f, index) => ({
    ...f,
    index,
    exception:
      index === ordered.length - 1 && panicMessage
        ? panicMessage
        : index === ordered.length - 1
          ? f.errors[0] ?? null
          : null,
    errors: index === ordered.length - 1 && panicMessage ? [panicMessage, ...f.errors] : f.errors,
  }));
}
