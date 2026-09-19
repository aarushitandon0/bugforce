"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  getChallenge,
  getSubmission,
  getTreeUrls,
  submitPatch,
  type ChallengeDetail,
  type Submission,
} from "@/lib/api";
import { countLines, type Attempt } from "@/lib/attempt";
import { Workspace, type FrameMark, type WorkspaceFile } from "@/lib/editor";
import { clock, plural, repoDisplay, repoShort, slug, thousands } from "@/lib/format";
import { rulesFor } from "@/lib/lang";
import { buildPatch, editorText, isModified, pathProblem } from "@/lib/patch";
import { markSolved, readLocal, writeLocal } from "@/lib/progress";
import { useSession } from "@/lib/session";
import type { Visit } from "@/lib/replay";
import { ancestorDirs, enclosingScope, findTestLine, parseNodeId, tabLabels } from "@/lib/solve";
import { gunzip, untar } from "@/lib/tar";
import { parseTraceback, resolveFramePath, type Frame } from "@/lib/traceback";
import { buildTree, toNodes, type ChallengeTree } from "@/lib/tree";
import { useTheme } from "@/lib/theme";
import { LABEL_COLOR } from "../ChallengeCard";
import { SignInToSubmit } from "../SignIn";
import { Cursor } from "../Cursor";
import { DifficultyBars, DifficultyLegend } from "../DifficultyBars";
import { SiteHeader } from "../Shell";
import { ActivityBar, type PanelId } from "../solve/ActivityBar";
import { BottomPanel, type BottomTab } from "../solve/BottomPanel";
import { Breadcrumbs } from "../solve/Breadcrumbs";
import { FileTree } from "../solve/FileTree";
import { GutterKey } from "../solve/GutterKey";
import { Resizer } from "../solve/Resizer";
import { Spine } from "../solve/Spine";

// ---------------------------------------------------------------------------
// loading the bundle
// ---------------------------------------------------------------------------

interface LogLine {
  tone: "command" | "dim" | "text" | "error" | "success";
  text: string;
}

const TONE: Record<LogLine["tone"], string> = {
  command: "text-text",
  text: "text-text",
  dim: "text-muted",
  error: "text-gap",
  success: "text-keep",
};

interface Bundle {
  detail: ChallengeDetail;
  tree: ChallengeTree;
  frames: Frame[];
  /** each frame's path in the tree, or null when it is outside the repo */
  resolved: (string | null)[];
}

function useBundle(id: string) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const log = (line: LogLine) => !cancelled && setLines((ls) => [...ls, line]);
    setLines([{ tone: "command", text: "$ bugforge checkout" }]);
    setBundle(null);
    setError(null);

    (async () => {
      try {
        const detail = await getChallenge(id);
        log({ tone: "dim", text: `· ${detail.title} · ${repoDisplay(detail.repo)}` });
        const urls = await getTreeUrls(id);
        const response = await fetch(urls.url, { cache: "no-store" });
        if (!response.ok) throw new Error(`tree download failed: HTTP ${response.status}`);
        const archive = await response.arrayBuffer();
        log({ tone: "dim", text: `· ${thousands(Math.round(archive.byteLength / 1024))} KB tree downloaded` });

        const tree = buildTree(untar(await gunzip(archive)));
        let traceback = tree.traceback;
        if (traceback === null) {
          const tb = await fetch(urls.traceback_url, { cache: "no-store" });
          traceback = tb.ok ? await tb.text() : "";
        }
        const frames = parseTraceback(traceback, detail.language);
        const paths = [...tree.files.keys()];
        const resolved = frames.map((f) => resolveFramePath(f.path, paths));
        log({ tone: "dim", text: `· ${plural(tree.files.size, "file")} unpacked` });
        log({
          tone: "dim",
          text: `· ${plural(frames.length, "frame")} in the trace, ${resolved.filter(Boolean).length} inside the repo`,
        });
        if (!cancelled) setBundle({ detail, tree, frames, resolved });
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) setError("no such bug");
        else setError(e instanceof Error ? e.message : String(e));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [id, attempt]);

  return { lines, bundle, error, retry: () => setAttempt((n) => n + 1) };
}

// ---------------------------------------------------------------------------
// submissions
// ---------------------------------------------------------------------------

const POLL_MS = 1500;
/** matches fn_api.MAX_VISITS: anything longer is trimmed server-side anyway */
const MAX_VISITS = 300;
const GIVE_UP_AFTER_MS = 5 * 60_000;

// ---------------------------------------------------------------------------
// layout
// ---------------------------------------------------------------------------

/** Panel widths, remembered per browser. The activity bar is fixed at 48px. */
interface Layout {
  side: number;
  right: number;
}

const LAYOUT_KEY = "bugforge:layout";
const DEFAULT_LAYOUT: Layout = { side: 260, right: 300 };
const SIDE_MIN = 180;
const SIDE_MAX = 520;
const RIGHT_MIN = 220;
const RIGHT_MAX = 480;
const BOTTOM_HEIGHT = 168;

// ---------------------------------------------------------------------------
// drafts
// ---------------------------------------------------------------------------

/** Per-browser working copy, so a reload doesn't lose edits or the clock. */
interface Draft {
  startedAt: number;
  files: Record<string, string>;
  tabs: string[];
  active: string | null;
  visited: number[];
  /** every file opening, for the investigation replay */
  visits: Visit[];
}

/** What the result screen needs that the API cannot tell it. */
export interface SolveRecord {
  elapsedMs: number;
  patch: string;
  startedAt: number;
  endedAt: number;
  visits: Visit[];
  /** traceback frame paths inside the repo, outermost first */
  frames: string[];
  displacement: number;
}

export const draftKey = (id: string) => `bugforge:draft:${id}`;
export const solveKey = (id: string) => `bugforge:solve:${id}`;

// ---------------------------------------------------------------------------
// screen
// ---------------------------------------------------------------------------

export function Solve() {
  const id = useSearchParams().get("id") ?? "";
  const { lines, bundle, error, retry } = useBundle(id);

  if (!id) {
    return (
      <Bare>
        <p className="p-6 text-gap">✗ no bug named in the URL</p>
      </Bare>
    );
  }
  if (!bundle) {
    return (
      <Bare>
        <div className="mx-auto w-full max-w-[720px] px-6 pt-16 text-[12.5px] leading-[1.8]" role="status">
          {lines.map((line, i) => (
            <div key={i} className={`whitespace-pre-wrap animate-fade ${TONE[line.tone]}`}>
              {line.text}
            </div>
          ))}
          {error ? (
            <div className="text-gap animate-fade">
              ✗ {error}
              <button type="button" onClick={retry} className="link ml-4">
                retry
              </button>
            </div>
          ) : (
            <Cursor />
          )}
        </div>
      </Bare>
    );
  }
  return <Workbench key={id} id={id} bundle={bundle} />;
}

function Bare({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <SiteHeader />
      {children}
    </div>
  );
}

function Workbench({ id, bundle }: { id: string; bundle: Bundle }) {
  const router = useRouter();
  const { detail, tree, frames, resolved } = bundle;
  // Everything about what may be edited, and what counts as a test file,
  // comes from here -- one lookup, mirroring cloud/anti_cheat.py.
  const rules = rulesFor(detail.language);

  // ----- derived, fixed for the life of the bundle -----
  const nodes = useMemo(() => toNodes(tree.files.keys()), [tree]);
  const binary = useMemo(
    () => new Set([...tree.files.values()].filter((f) => f.text === null).map((f) => f.path)),
    [tree],
  );
  const traced = useMemo(() => new Set(resolved.filter((p): p is string => p !== null)), [resolved]);
  const marksByPath = useMemo(() => {
    const byPath = new Map<string, Map<number, FrameMark>>();
    const deepest = frames.length - 1;
    frames.forEach((frame, i) => {
      const path = resolved[i];
      if (!path) return;
      const lines = byPath.get(path) ?? new Map<number, FrameMark>();
      const existing = lines.get(frame.line);
      const exception = i === deepest;
      const title = exception
        ? `#${i + 1} ${frame.func ?? "<module>"} · raised ${frame.exception ?? "here"}`
        : `#${i + 1} ${frame.func ?? "<module>"}`;
      lines.set(frame.line, {
        line: frame.line,
        exception: exception || (existing?.exception ?? false),
        frames: [...(existing?.frames ?? []), i],
        title: existing ? `${existing.title}\n${title}` : title,
      });
      byPath.set(path, lines);
    });
    return new Map([...byPath].map(([path, lines]) => [path, [...lines.values()]]));
  }, [frames, resolved]);

  // ----- restored draft -----
  const initial = useMemo(() => {
    const draft = readLocal<Draft>(draftKey(id));
    const openable = (p: string) => tree.files.get(p)?.text != null;
    const files: Record<string, string> = {};
    for (const [path, text] of Object.entries(draft?.files ?? {})) {
      const original = tree.files.get(path)?.text;
      if (original != null && isModified({ path, original, current: text })) files[path] = text;
    }
    return {
      startedAt: draft?.startedAt ?? Date.now(),
      files,
      tabs: (draft?.tabs ?? []).filter(openable),
      active: draft?.active && openable(draft.active) ? draft.active : null,
      visited: new Set((draft?.visited ?? []).filter((i) => i >= 0 && i < frames.length && resolved[i] !== null)),
      visits: (draft?.visits ?? []).filter((v) => typeof v?.path === "string" && typeof v?.at === "number"),
    };
  }, [id, tree, frames.length, resolved]);

  // ----- state -----
  const [tabs, setTabs] = useState<string[]>(initial.tabs);
  const [active, setActive] = useState<string | null>(null);
  const [visited, setVisited] = useState<ReadonlySet<number>>(initial.visited);
  const [activeFrame, setActiveFrame] = useState<number | null>(null);
  const [modified, setModified] = useState<ReadonlySet<string>>(new Set(Object.keys(initial.files)));
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set([...ancestorDirs([...traced, ...initial.tabs])]),
  );
  const [draftVersion, setDraftVersion] = useState(0);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [panel, setPanel] = useState<PanelId | null>("trace");
  const [layout, setLayout] = useState<Layout>(DEFAULT_LAYOUT);
  const [bottomTab, setBottomTab] = useState<BottomTab>("problems");
  const [bottomOpen, setBottomOpen] = useState(false);
  const [cursorLine, setCursorLine] = useState(1);
  const [reveal, setReveal] = useState<{ path: string; n: number } | undefined>(undefined);
  const [solvedAt, setSolvedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [flash, setFlash] = useState<string | null>(null);
  // Set when a submit was refused for want of a session, so the prompt appears
  // where you pressed the key rather than only in the header.
  const [needsSignIn, setNeedsSignIn] = useState(false);
  const { user } = useSession();
  const [mac, setMac] = useState(true);
  const [theme] = useTheme();

  const hostRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<Workspace | null>(null);
  const draftFiles = useRef<Record<string, string>>({ ...initial.files });
  // {file, opened_at} in the order the learner opened them
  const visits = useRef<Visit[]>([...initial.visits]);
  const pendingDocs = useRef(new Set<string>());
  const docTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const mod = mac ? "⌘" : "ctrl+";
  const alt = mac ? "⌥" : "alt+";
  const elapsed = (solvedAt ?? now) - initial.startedAt;
  const grading = attempts.some((a) => a.state === "sending" || a.state === "grading");

  useEffect(() => {
    setMac(/Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent));
    const saved = readLocal<Layout>(LAYOUT_KEY);
    if (saved) {
      setLayout({
        side: Math.max(SIDE_MIN, Math.min(SIDE_MAX, saved.side ?? DEFAULT_LAYOUT.side)),
        right: Math.max(RIGHT_MIN, Math.min(RIGHT_MAX, saved.right ?? DEFAULT_LAYOUT.right)),
      });
    }
  }, []);

  useEffect(() => {
    wsRef.current?.setDark(theme === "dark");
  }, [theme]);

  const resize = useCallback((next: Partial<Layout>) => {
    setLayout((prev) => {
      const merged = { ...prev, ...next };
      writeLocal(LAYOUT_KEY, merged);
      return merged;
    });
  }, []);

  useEffect(() => {
    if (solvedAt) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [solvedAt]);

  const showFlash = useCallback((text: string) => {
    setFlash(text);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), 1800);
  }, []);

  // ----- files -----
  const originalText = useCallback((path: string) => tree.files.get(path)?.text ?? null, [tree]);

  const currentText = useCallback(
    (path: string): string | null => {
      const original = originalText(path);
      if (original === null) return null;
      return wsRef.current?.text(path) ?? draftFiles.current[path] ?? editorText(original);
    },
    [originalText],
  );

  const workspaceFile = useCallback(
    (path: string): WorkspaceFile | null => {
      const text = currentText(path);
      if (text === null) return null;
      return {
        path,
        text,
        // Test files and files of another language open read-only, because
        // the grader would reject a patch touching them anyway. The rule has
        // to come from the challenge's language: with ".py" hard-coded, every
        // file in a Go challenge was read-only and it could not be solved.
        readOnly: rules.isTestPath(path) || !path.endsWith(rules.sourceSuffix),
        marks: marksByPath.get(path) ?? [],
      };
    },
    [currentText, marksByPath, rules],
  );

  const flushDocs = useCallback(() => {
    const ws = wsRef.current;
    if (!ws) return;
    for (const path of pendingDocs.current) {
      const original = originalText(path);
      const current = ws.text(path);
      if (original === null || current === null) continue;
      if (isModified({ path, original, current })) draftFiles.current[path] = current;
      else delete draftFiles.current[path];
    }
    pendingDocs.current.clear();
    setModified((prev) => {
      const next = Object.keys(draftFiles.current);
      return next.length === prev.size && next.every((p) => prev.has(p)) ? prev : new Set(next);
    });
    setDraftVersion((v) => v + 1);
  }, [originalText]);

  const open = useCallback(
    (path: string, line?: number, frameIndex?: number) => {
      const ws = wsRef.current;
      const file = workspaceFile(path);
      if (!ws || !file) return;
      ws.show(file, line);
      if (visits.current[visits.current.length - 1]?.path !== path) {
        visits.current = [...visits.current, { path, at: Date.now() }].slice(-MAX_VISITS);
        setDraftVersion((v) => v + 1);
      }
      setTabs((ts) => (ts.includes(path) ? ts : [...ts, path]));
      setActive(path);
      setExpanded((prev) => {
        const needed = [...ancestorDirs([path])].filter((d) => !prev.has(d));
        return needed.length ? new Set([...prev, ...needed]) : prev;
      });
      if (frameIndex !== undefined) {
        setActiveFrame(frameIndex);
        setVisited((prev) => {
          if (prev.has(frameIndex)) return prev;
          const next = new Set(prev).add(frameIndex);
          ws.setVisited(next);
          return next;
        });
      } else {
        setActiveFrame(null);
      }
    },
    [workspaceFile],
  );

  const openFrame = useCallback(
    (index: number) => {
      const path = resolved[index];
      if (path) open(path, frames[index].line, index);
    },
    [open, resolved, frames],
  );

  const openTest = useCallback(
    (nodeId: string) => {
      const { path, names } = parseNodeId(nodeId);
      const text = originalText(path);
      if (text === null) return;
      open(path, findTestLine(text, names) ?? 1);
    },
    [open, originalText],
  );

  const closeTab = useCallback(
    (path: string) => {
      const at = tabs.indexOf(path);
      if (at === -1) return;
      const rest = tabs.filter((p) => p !== path);
      setTabs(rest);
      if (active === path) {
        const next = rest[at] ?? rest[at - 1] ?? null;
        if (next) open(next);
        else {
          setActive(null);
          setActiveFrame(null);
        }
      }
    },
    [tabs, active, open],
  );

  const revert = useCallback(
    (path: string) => {
      const original = originalText(path);
      const ws = wsRef.current;
      if (original === null || !ws) return;
      if (ws.has(path)) ws.replace(path, editorText(original));
      pendingDocs.current.add(path);
      delete draftFiles.current[path];
      flushDocs();
      showFlash(`reverted ${path.split("/").pop()}`);
    },
    [originalText, flushDocs, showFlash],
  );

  const stepFrame = useCallback(
    (direction: -1 | 1) => {
      const clickable = resolved.flatMap((p, i) => (p === null ? [] : [i]));
      if (clickable.length === 0) return;
      let target: number | undefined;
      if (activeFrame === null) target = clickable[clickable.length - 1];
      else if (direction === -1) target = [...clickable].reverse().find((i) => i < activeFrame);
      else target = clickable.find((i) => i > activeFrame);
      if (target !== undefined) openFrame(target);
      else showFlash(direction === -1 ? "already at the outermost frame" : "already at the frame that raised");
    },
    [resolved, activeFrame, openFrame, showFlash],
  );

  // ----- editor lifecycle -----
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const ws = new Workspace(
      host,
      (path) => {
        pendingDocs.current.add(path);
        clearTimeout(docTimer.current);
        docTimer.current = setTimeout(flushDocs, 150);
      },
      setCursorLine,
    );
    ws.setDark(document.documentElement.getAttribute("data-theme") !== "light");
    wsRef.current = ws;
    ws.setVisited(initial.visited);

    // where to start: the tab left open last time, else the frame that raised
    // (the deepest one inside the repo), else the first failing test.
    if (initial.active) {
      open(initial.active);
    } else {
      const deepest = resolved.findLastIndex((p) => p !== null);
      if (deepest !== -1) openFrame(deepest);
      else if (detail.failing_tests[0]) openTest(detail.failing_tests[0]);
    }
    return () => {
      clearTimeout(docTimer.current);
      ws.destroy();
      wsRef.current = null;
    };
    // mount once per bundle; the callbacks read refs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ----- draft persistence -----
  useEffect(() => {
    if (solvedAt) return;
    writeLocal(draftKey(id), {
      startedAt: initial.startedAt,
      files: draftFiles.current,
      tabs,
      active,
      visited: [...visited],
      visits: visits.current,
    } satisfies Draft);
  }, [id, initial.startedAt, tabs, active, visited, draftVersion, solvedAt]);

  // ----- submit -----
  const submit = useCallback(async () => {
    if (grading || solvedAt) return;
    // Checked here as well as by the API, so ⌘↵ signed out says why instead of
    // building a patch and getting a 401 back. The server is still the one
    // that decides; this only saves the round trip.
    if (!user) {
      setBottomTab("output");
      setBottomOpen(true);
      setNeedsSignIn(true);
      showFlash("sign in with github to submit");
      return;
    }
    flushDocs();
    const changes = Object.keys(draftFiles.current).map((path) => ({
      path,
      original: originalText(path)!,
      current: draftFiles.current[path],
    }));
    const paths = changes.filter(isModified).map((c) => c.path).sort();
    const patch = buildPatch(changes);
    const { added, removed } = countLines(patch);
    const base: Attempt = {
      n: attempts.length + 1,
      files: paths,
      added,
      removed,
      sentAt: Date.now(),
      submissionId: null,
      state: "sending",
      message: null,
      result: null,
    };
    setBottomTab("output");
    setBottomOpen(true);

    const problem = pathProblem(paths, detail.language);
    if (problem) {
      setAttempts((as) => [
        ...as,
        {
          ...base,
          state: "blocked",
          message: problem === "no files changed" ? "nothing to submit: no files changed yet" : `not sent: ${problem}`,
        },
      ]);
      return;
    }

    setAttempts((as) => [...as, base]);
    const update = (fields: Partial<Attempt>) =>
      setAttempts((as) => as.map((a) => (a.n === base.n ? { ...a, ...fields } : a)));
    try {
      const sent = await submitPatch(
        id,
        patch,
        visits.current,
        Math.round((Date.now() - initial.startedAt) / 1000),
      );
      writeLocal(solveKey(id), {
        elapsedMs: Date.now() - initial.startedAt,
        patch,
        startedAt: initial.startedAt,
        endedAt: Date.now(),
        visits: visits.current,
        frames: resolved.filter((p): p is string => p !== null),
        displacement: detail.breakdown?.displacement ?? 0,
      } satisfies SolveRecord);
      update({ submissionId: sent.submission_id, state: "grading", sentAt: Date.now() });
    } catch (e) {
      // A session can expire between loading the page and submitting it.
      if (e instanceof ApiError && e.status === 401) setNeedsSignIn(true);
      update({ state: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [grading, solvedAt, flushDocs, originalText, attempts.length, id, initial.startedAt, resolved, detail.breakdown, user, showFlash]);

  // poll the attempt that is grading
  const gradingAttempt = attempts.find((a) => a.state === "grading");
  useEffect(() => {
    if (!gradingAttempt?.submissionId) return;
    const { n, submissionId, sentAt } = gradingAttempt;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const update = (fields: Partial<Attempt>) =>
      setAttempts((as) => as.map((a) => (a.n === n ? { ...a, ...fields } : a)));

    const tick = async () => {
      try {
        const result = await getSubmission(submissionId);
        if (cancelled) return;
        if (result.status === "COMPLETE") {
          update({ state: "done", result });
          if (result.verdict === "PASS") {
            const stoppedAt = Date.now();
            setSolvedAt(stoppedAt);
            markSolved(id);
            const record = readLocal<SolveRecord>(solveKey(id));
            if (record) writeLocal(solveKey(id), { ...record, elapsedMs: stoppedAt - initial.startedAt });
            writeLocal(draftKey(id), null);
            setTimeout(() => router.push(`/result/?submission=${encodeURIComponent(submissionId)}`), 900);
          }
          return;
        }
      } catch {
        // transient: keep polling until the deadline
      }
      if (cancelled) return;
      if (Date.now() - sentAt > GIVE_UP_AFTER_MS) {
        update({ state: "error", message: `no verdict after ${clock(GIVE_UP_AFTER_MS)}. the grader may have crashed; submit again.` });
        return;
      }
      timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [gradingAttempt?.submissionId, gradingAttempt?.n, gradingAttempt?.sentAt, id, initial.startedAt, router]);

  // ----- keybindings -----
  const keys = useRef({ submit, stepFrame, closeTab, active, flushDocs, showFlash });
  keys.current = { submit, stepFrame, closeTab, active, flushDocs, showFlash };

  useEffect(() => {
    // Capture phase, so these win over CodeMirror's own bindings (Mod-Enter
    // would otherwise insert a blank line).
    const onKey = (e: KeyboardEvent) => {
      const k = keys.current;
      const modKey = e.metaKey || e.ctrlKey;
      let handled = true;
      if (modKey && !e.altKey && e.key === "Enter") k.submit();
      else if (modKey && !e.altKey && !e.shiftKey && e.code === "KeyS") {
        k.flushDocs();
        k.showFlash("draft saved in this browser");
      } else if (e.altKey && !modKey && e.code === "BracketLeft") k.stepFrame(-1);
      else if (e.altKey && !modKey && e.code === "BracketRight") k.stepFrame(1);
      else if (e.altKey && !modKey && e.code === "KeyW") {
        if (k.active) k.closeTab(k.active);
      } else handled = false;
      if (handled) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  // ----- render -----
  const labels = useMemo(() => tabLabels(tabs), [tabs]);
  const activeReadOnly =
    active !== null && (rules.isTestPath(active) || !active.endsWith(rules.sourceSuffix));
  const lastAttempt = attempts[attempts.length - 1];
  const statusName = `${repoShort(detail.repo)}/${slug(detail.title) || "bug"}`;

  // the breadcrumb's tail: the def/class the caret sits inside
  const scope = useMemo(() => {
    const text = active ? (wsRef.current?.text(active) ?? currentText(active)) : null;
    return text && active?.endsWith(".py") ? enclosingScope(text, cursorLine) : [];
    // draftVersion is in here so the crumb follows edits, not just the caret
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, cursorLine, currentText, draftVersion]);

  const revealInTree = useCallback((path: string) => {
    setPanel("files");
    setExpanded((prev) => {
      const needed = [...ancestorDirs([path]), path].filter((d) => !prev.has(d));
      return needed.length ? new Set([...prev, ...needed]) : prev;
    });
    setReveal((r) => ({ path, n: (r?.n ?? 0) + 1 }));
  }, []);

  return (
    <div className="flex min-h-dvh flex-col lg:h-dvh">
      {/* the same site nav as every other route, in its compact box, with the
          back link and the breadcrumbs riding in the middle of it */}
      <div className="shrink-0">
        <SiteHeader compact>
          <Link
            href={`/repo/?name=${encodeURIComponent(detail.repo)}`}
            className="shrink-0 text-muted transition-colors duration-[120ms] hover:text-text"
          >
            &larr; {repoShort(detail.repo)}
          </Link>
          <span aria-hidden className="h-4 w-px shrink-0 bg-line-strong" />
          <Breadcrumbs path={active} scope={scope} onReveal={revealInTree} />
        </SiteHeader>
      </div>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <ActivityBar
          active={panel}
          onSelect={(next) => setPanel((prev) => (prev === next ? null : next))}
          badges={{ tests: detail.failing_tests.length }}
        />

        {/* SIDE: whichever panel the activity bar selected */}
        {panel !== null && (
          <aside
            className="flex max-h-[45vh] min-h-0 shrink-0 flex-col border-b border-line lg:max-h-none lg:border-b-0"
            style={{ width: layout.side, maxWidth: "100%" }}
            aria-label={panel}
          >
            {panel === "trace" && (
              <Spine frames={frames} resolved={resolved} visited={visited} activeFrame={activeFrame} onOpen={openFrame} />
            )}
            {panel === "files" && (
              <div className="flex min-h-0 flex-1 flex-col">
                <h2 className="label shrink-0 border-b border-line px-3 py-2">
                  files <span className="normal-case tracking-normal">&middot; {thousands(tree.files.size)}</span>
                </h2>
                <div className="min-h-0 flex-1 overflow-y-auto pb-2">
                  <FileTree
                    nodes={nodes}
                    expanded={expanded}
                    reveal={reveal}
                    onToggle={(dir) =>
                      setExpanded((prev) => {
                        const next = new Set(prev);
                        if (next.has(dir)) next.delete(dir);
                        else next.add(dir);
                        return next;
                      })
                    }
                    onOpen={(path) => open(path)}
                    marks={{ active, open: new Set(tabs), modified, traced, binary }}
                  />
                </div>
              </div>
            )}
            {panel === "tests" && (
              <div className="flex min-h-0 flex-1 flex-col">
                <h2 className="label shrink-0 border-b border-line px-3 py-2">
                  failing &middot; {detail.failing_tests.length} of {thousands(detail.total_tests)}
                </h2>
                <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto p-3">
                  {detail.failing_tests.map((nodeId) => {
                    const { path, names } = parseNodeId(nodeId);
                    return (
                      <li key={nodeId}>
                        <button
                          type="button"
                          onClick={() => openTest(nodeId)}
                          title={`open ${nodeId}`}
                          className="group block w-full text-left text-[11.5px] leading-[1.45] outline-none"
                        >
                          <span className="flex gap-1.5">
                            <span className="text-gap">&#10007;</span>
                            <span className="min-w-0 break-all text-text group-hover:underline group-focus-visible:underline">
                              {names[names.length - 1] ?? path}
                            </span>
                          </span>
                          <span className="block truncate pl-[2.2ch] text-[10.5px] text-muted">
                            {[...names.slice(0, -1), path.split("/").pop()].join(" · ")}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
            {panel === "brief" && (
              <div className="min-h-0 flex-1 overflow-y-auto">
                <h2 className="label border-b border-line px-3 py-2">the brief</h2>
                <div className="px-3 py-3">
                  <h3 className="text-[15px] font-bold leading-snug text-text">{detail.title}</h3>
                  <p className="mt-2 text-[12px] leading-[1.6] text-text/90">{detail.description}</p>
                  <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
                    <dt className="text-muted">repo</dt>
                    <dd className="truncate text-text">{repoDisplay(detail.repo)}</dd>
                    <dt className="text-muted">license</dt>
                    <dd className="truncate text-text">{detail.license || "—"}</dd>
                    <dt className="text-muted">language</dt>
                    <dd className="text-text">{detail.language.toLowerCase()}</dd>
                    <dt className="text-muted">suite</dt>
                    <dd className="text-text">{thousands(detail.total_tests)} tests</dd>
                  </dl>
                </div>
              </div>
            )}
          </aside>
        )}
        {panel !== null && (
          <Resizer
            label="resize the side panel"
            side="left"
            width={layout.side}
            min={SIDE_MIN}
            max={SIDE_MAX}
            onResize={(side) => resize({ side })}
          />
        )}

        {/* CENTER: tabs, editor, bottom dock */}
        <section className="flex h-[72vh] min-h-0 min-w-0 flex-1 flex-col lg:h-auto" aria-label="editor">
          <div className="flex h-9 shrink-0 items-stretch border-b border-line bg-surface-2">
            <div role="tablist" aria-label="open files" className="flex min-w-0 flex-1 overflow-x-auto">
              {tabs.map((path) => {
                const label = labels.get(path)!;
                const isActive = path === active;
                const isDirty = modified.has(path);
                return (
                  <div
                    key={path}
                    onAuxClick={(e) => {
                      // middle click closes, the way every editor does
                      if (e.button === 1) {
                        e.preventDefault();
                        closeTab(path);
                      }
                    }}
                    className={`group flex shrink-0 items-stretch border-r border-line transition-colors duration-[120ms] ${
                      isActive ? "bg-surface-1 text-text" : "bg-surface-2 text-muted hover:bg-surface-3 hover:text-text"
                    }`}
                  >
                    <button
                      type="button"
                      role="tab"
                      aria-selected={isActive}
                      title={path}
                      onClick={() => open(path)}
                      className={`flex items-center gap-1.5 border-t-2 pr-1 pl-3 text-[12px] outline-none transition-colors duration-[120ms] ${
                        isActive ? "border-frame" : "border-transparent"
                      }`}
                    >
                      {traced.has(path) && <span aria-hidden className="block h-[5px] w-[5px] shrink-0 bg-frame" />}
                      <span>{label.name}</span>
                      {label.hint && <span className="text-[10.5px] text-muted">{label.hint}</span>}
                    </button>
                    <button
                      type="button"
                      onClick={() => closeTab(path)}
                      aria-label={`close ${path}`}
                      className={`flex w-6 items-center justify-center border-t-2 text-[13px] outline-none transition-colors duration-[120ms] hover:text-text focus-visible:text-text ${
                        isActive ? "border-frame" : "border-transparent"
                      }`}
                    >
                      {/* a dot for unsaved changes; the close X takes over on hover */}
                      {isDirty ? (
                        <>
                          <span className="text-gap group-hover:hidden" aria-label="modified">
                            &#9679;
                          </span>
                          <span className="hidden group-hover:inline">&times;</span>
                        </>
                      ) : (
                        <span className="opacity-0 transition-opacity duration-[120ms] group-hover:opacity-100 group-focus-within:opacity-100">
                          &times;
                        </span>
                      )}
                    </button>
                  </div>
                );
              })}
            </div>
            <div className="flex shrink-0 items-center gap-3 px-3 t-small text-muted">
              {/* the key, beside the gutter it describes */}
              {active !== null && (marksByPath.get(active)?.length ?? 0) > 0 && (
                <GutterKey inline className="hidden md:flex" />
              )}
              {activeReadOnly && (
                <span title="test files and non-Python files can't be patched">read-only &middot; the suite is the judge</span>
              )}
              {active && modified.has(active) && (
                <button type="button" onClick={() => revert(active)} className="link text-muted hover:text-text">
                  revert file
                </button>
              )}
            </div>
          </div>

          <div className="relative min-h-0 flex-1">
            <div ref={hostRef} className="absolute inset-0" />
            {active === null && (
              <div className="absolute inset-0 flex items-center justify-center bg-surface-1 p-6 text-center text-muted animate-fade">
                <p>
                  open a frame from the trace, or a file from the tree.
                  <br />
                  <span className="kbd mt-3">{alt}[</span> <span className="kbd">{alt}]</span> walks the trace.
                </p>
              </div>
            )}
          </div>

          <BottomPanel
            tab={bottomTab}
            onTab={setBottomTab}
            open={bottomOpen}
            onToggle={() => setBottomOpen((o) => !o)}
            failingTests={detail.failing_tests}
            onOpenTest={openTest}
            attempts={attempts}
            now={now}
            height={BOTTOM_HEIGHT}
          />
        </section>

        <Resizer
          label="resize the detail panel"
          side="right"
          width={layout.right}
          min={RIGHT_MIN}
          max={RIGHT_MAX}
          onResize={(right) => resize({ right })}
        />

        {/*
         * RIGHT rail, in the order a reader needs it: what the bug is, how to
         * read the gutter, how to drive the keyboard, the clock, and -- last --
         * sign-in. Sign-in used to be the largest, highest-contrast block on a
         * screen whose job is reading a stack trace.
         */}
        <aside
          className="flex min-h-0 shrink-0 flex-col overflow-y-auto border-t border-line lg:border-t-0 lg:border-l"
          style={{ width: layout.right, maxWidth: "100%" }}
          aria-label="bug"
        >
          <div className="shrink-0 border-b border-line px-4 pt-3 pb-4">
            <div className="flex items-start justify-between gap-3">
              <p className="t-label">
                <span className={LABEL_COLOR[detail.difficulty_label]}>{detail.difficulty_label}</span>
                <span className="text-muted"> &middot; {detail.language.toLowerCase()}</span>
              </p>
              {/* the three measured inputs, with their tooltips: removed from
                  the course grid, kept here, where you are choosing how to
                  attack the bug rather than which bug to take */}
              <DifficultyBars breakdown={detail.breakdown} failing={detail.failing_test_count} total={detail.total_tests} />
            </div>
            <h1 className="t-h2 mt-1 text-text">{detail.title}</h1>
            <p className="mt-2 t-small text-muted">{detail.description}</p>
            <DifficultyLegend
              className="mt-3"
              breakdown={detail.breakdown}
              failing={detail.failing_test_count}
              total={detail.total_tests}
            />
          </div>

          <div className="shrink-0 border-b border-line px-4 py-3">
            <h2 className="label">the gutter</h2>
            <GutterKey className="mt-2" />
            <p className="mt-3 t-small text-muted">
              the trace shows where it failed, not where it broke. the amber row is where it raised; the violet marks
              are the frames above it.
            </p>
          </div>

          {/* muted rather than faint, and a two-column grid that wraps instead
              of running off the right edge of the rail */}
          <div className="shrink-0 border-b border-line px-4 py-3">
            <h2 className="label">keys</h2>
            <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 t-small text-muted">
              <dt className="whitespace-nowrap text-text">{mod}&crarr;</dt>
              <dd className="min-w-0">submit</dd>
              <dt className="whitespace-nowrap text-text">
                {alt}[ {alt}]
              </dt>
              <dd className="min-w-0">walk trace</dd>
              <dt className="whitespace-nowrap text-text">{alt}w</dt>
              <dd className="min-w-0">close tab</dd>
              <dt className="whitespace-nowrap text-text">{mod}f</dt>
              <dd className="min-w-0">find</dd>
            </dl>
          </div>

          {/* the clock is not scored -- the leaderboard ranks on score and
              solved count -- so it is a fact about your session, at the size
              every other fact on this rail gets */}
          <div className="shrink-0 border-b border-line px-4 py-3">
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="label">time</h2>
              <p
                className={`t-small tabular-nums ${solvedAt ? "text-keep" : "text-text"}`}
                role="timer"
                aria-label="time on this bug"
              >
                {clock(elapsed)}
              </p>
            </div>
            <p className="mt-2 t-small text-muted">
              {visited.size} of {resolved.filter(Boolean).length} frames visited &middot; {plural(tabs.length, "file")} open
              {modified.size > 0 && <> &middot; {modified.size} modified</>}
            </p>
          </div>

          {!user && !solvedAt && (
            <div className="shrink-0 px-4 py-3">
              <SignInToSubmit className="border-0 bg-transparent p-0" />
            </div>
          )}
        </aside>
      </div>

      {/* BOTTOM: the status strip, one line, unchanged */}
      <footer className="sticky bottom-0 z-20 flex h-7 shrink-0 items-center justify-between gap-4 border-t border-line bg-surface-2 px-3 text-[11px] tabular-nums text-muted">
        <p className="flex min-w-0 items-center gap-2 truncate">
          <span className="text-text">{statusName}</span>
          <span>&middot;</span>
          <span className={solvedAt ? "text-keep" : "text-text"}>{clock(elapsed)}</span>
          <span>&middot;</span>
          <span>{plural(tabs.length, "file")} open</span>
          {modified.size > 0 && (
            <>
              <span>&middot;</span>
              <span className="text-gap">{modified.size} modified</span>
            </>
          )}
          <span>&middot;</span>
          <button
            type="button"
            onClick={submit}
            disabled={grading || solvedAt !== null}
            className="text-text transition-colors duration-[120ms] hover:text-keep disabled:text-muted"
          >
            {mod}&crarr; submit
          </button>
        </p>
        <p className="shrink-0 truncate" aria-live="polite">
          {flash ? (
            <span className="text-text animate-fade">{flash}</span>
          ) : lastAttempt ? (
            <AttemptSummary attempt={lastAttempt} now={now} />
          ) : (
            <span>
              {visited.size} of {resolved.filter(Boolean).length} frames visited
            </span>
          )}
        </p>
      </footer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// status strip summary
// ---------------------------------------------------------------------------

function AttemptSummary({ attempt, now }: { attempt: Attempt; now: number }) {
  if (attempt.state === "sending") return <span>sending patch…</span>;
  if (attempt.state === "grading")
    return (
      <span className="text-text">
        grading · {clock(now - attempt.sentAt)} <Cursor className="!h-[0.9em] !w-[0.45em]" />
      </span>
    );
  if (attempt.state === "blocked" || attempt.state === "error") return <span className="text-gap">✗ {attempt.message}</span>;
  const r = attempt.result!;
  if (r.verdict === "PASS") return <span className="font-bold text-keep">PASS · {thousands(r.tests_passed ?? 0)} green</span>;
  if (r.verdict === "FAIL") return <span className="text-gap">FAIL · {plural(r.failing_tests?.length ?? 0, "test")} red</span>;
  return <span className="text-gap">REJECTED · {r.reason}</span>;
}

