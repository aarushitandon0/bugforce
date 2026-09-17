/**
 * Parses pytest --tb=long output into stack frames for the spine.
 *
 * Each frame in that format is a source excerpt followed by its location line:
 *
 *         async def __anext__(self) -> AttemptManager:
 *     >           do = await self.iter(retry_state=self._retry_state)
 *                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
 *
 *     tenacity/asyncio/__init__.py:204:
 *     _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
 *
 * The deepest frame's location line ends with the exception name instead
 * ("tests/test_asyncio.py:436: CustomException"), and its excerpt carries the
 * "E   ..." lines. Paths are relative to the tree root, with backslashes when
 * the suite ran on Windows; frames outside the repo have absolute paths.
 */

export type ExcerptKind = "code" | "failing" | "caret" | "local" | "error";

export interface ExcerptLine {
  text: string;
  kind: ExcerptKind;
}

export interface Frame {
  index: number;
  path: string;
  line: number;
  func: string | null;
  exception: string | null;
  excerpt: ExcerptLine[];
  failingSource: string | null;
  errors: string[];
}

const LOCATION = /^(\S+?\.py):(\d+):(?: in (\S+)| ?(.*))$/;
const SEPARATOR = /^(?:_ )+_?\s*$/;
const DEF = /^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)/;
const CARET = /^\s*[\^~]+\s*$/;
const LOCAL = /^[A-Za-z_]\w* = /;
const EXCEPTION_NAME = /^[A-Za-z_][\w.<>]*$/;

function classify(raw: string, seenCode: boolean): ExcerptLine {
  if (raw.startsWith(">")) return { text: " " + raw.slice(1), kind: "failing" };
  if (raw === "E" || raw.startsWith("E ")) return { text: raw.slice(1), kind: "error" };
  if (CARET.test(raw)) return { text: raw, kind: "caret" };
  if (!seenCode && LOCAL.test(raw)) return { text: raw, kind: "local" };
  return { text: raw, kind: "code" };
}

function buildFrame(index: number, match: RegExpExecArray, chunk: string[]): Frame {
  let start = 0;
  let end = chunk.length;
  while (start < end && chunk[start].trim() === "") start++;
  while (end > start && chunk[end - 1].trim() === "") end--;

  const excerpt: ExcerptLine[] = [];
  let seenCode = false;
  for (const raw of chunk.slice(start, end)) {
    const line = classify(raw, seenCode);
    if (line.kind === "code" && raw.trim() !== "") seenCode = true;
    if (line.kind === "failing") seenCode = true;
    excerpt.push(line);
  }

  const tail = (match[4] ?? "").trim();
  let func = match[3] ?? null;
  if (!func) {
    for (const line of excerpt) {
      const def = DEF.exec(line.text);
      if (def) {
        func = def[1];
        break;
      }
    }
  }
  const failing = excerpt.find((l) => l.kind === "failing");

  return {
    index,
    path: match[1].replace(/\\/g, "/"),
    line: Number(match[2]),
    func,
    exception: tail && EXCEPTION_NAME.test(tail) ? tail.split(".").pop()! : null,
    excerpt,
    failingSource: failing ? failing.text.trim() : null,
    errors: excerpt.filter((l) => l.kind === "error").map((l) => l.text.trim()).filter(Boolean),
  };
}

/** Frames outermost first, deepest (where the exception surfaced) last. */
export function parseTraceback(text: string): Frame[] {
  const frames: Frame[] = [];
  let chunk: string[] = [];
  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const match = LOCATION.exec(raw);
    if (match) {
      frames.push(buildFrame(frames.length, match, chunk));
      chunk = [];
    } else if (!SEPARATOR.test(raw)) {
      chunk.push(raw);
    }
  }
  return frames;
}

/**
 * Maps a frame path onto a file in the tree. Relative paths match directly;
 * an absolute path (a different rootdir, or a vendored copy) matches the
 * longest tree path it ends with. Frames in site-packages match nothing.
 */
export function resolveFramePath(framePath: string, treePaths: Iterable<string>): string | null {
  const paths = [...treePaths];
  if (paths.includes(framePath)) return framePath;
  let best: string | null = null;
  for (const path of paths) {
    if (framePath.endsWith("/" + path) && (best === null || path.length > best.length)) best = path;
  }
  return best;
}
