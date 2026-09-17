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
import { Workspace, type FrameMark, type WorkspaceFile } from "@/lib/editor";
import { clock, plural, repoDisplay, repoShort, slug, thousands } from "@/lib/format";
import { buildPatch, editorText, isModified, isTestPath, pathProblem } from "@/lib/patch";
import { markSolved, readLocal, writeLocal } from "@/lib/progress";
import type { Visit } from "@/lib/replay";
import { ancestorDirs, findTestLine, parseNodeId, tabLabels } from "@/lib/solve";
import { gunzip, untar } from "@/lib/tar";
import { parseTraceback, resolveFramePath, type Frame } from "@/lib/traceback";
import { buildTree, toNodes, type ChallengeTree } from "@/lib/tree";
import { LABEL_COLOR } from "../ChallengeCard";
import { Cursor } from "../Cursor";
import { DifficultyBars } from "../DifficultyBars";
import { SiteHeader } from "../Shell";
import { FileTree } from "../solve/FileTree";
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
  dim: "text-dim",
  error: "text-error",
  success: "text-success",
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
        const frames = parseTraceback(traceback);
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
        if (e instanceof ApiError && e.status === 404) setError("no such challenge");
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

interface Attempt {
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

const POLL_MS = 1500;
/** matches fn_api.MAX_VISITS: anything longer is trimmed server-side anyway */
const MAX_VISITS = 300;
const SLOW_AFTER_MS = 75_000;
const GIVE_UP_AFTER_MS = 5 * 60_000;

function countLines(patch: string): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const line of patch.split("\n")) {
    if (line.startsWith("+") && !line.startsWith("+++")) added++;
    if (line.startsWith("-") && !line.startsWith("---")) removed++;
  }
  return { added, removed };
}

const REJECTION: Record<string, string> = {
  anti_cheat: "rejected before running: the patch breaks the rules",
  patch_did_not_apply: "rejected: the patch did not apply to the challenge tree",
};

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
        <p className="p-6 text-error">✗ no challenge named in the URL</p>
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
            <div className="text-error animate-fade">
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
  const [logOpen, setLogOpen] = useState(true);
  const [solvedAt, setSolvedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [flash, setFlash] = useState<string | null>(null);
  const [mac, setMac] = useState(true);

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
        readOnly: isTestPath(path) || !path.endsWith(".py"),
        marks: marksByPath.get(path) ?? [],
      };
    },
    [currentText, marksByPath],
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
    const ws = new Workspace(host, (path) => {
      pendingDocs.current.add(path);
      clearTimeout(docTimer.current);
      docTimer.current = setTimeout(flushDocs, 150);
    });
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
    setLogOpen(true);

    const problem = pathProblem(paths);
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
      const sent = await submitPatch(id, patch, visits.current);
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
      update({ state: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [grading, solvedAt, flushDocs, originalText, attempts.length, id, initial.startedAt, resolved, detail.breakdown]);

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
  const activeReadOnly = active !== null && (isTestPath(active) || !active.endsWith(".py"));
  const lastAttempt = attempts[attempts.length - 1];
  const statusName = `${repoShort(detail.repo)}/${slug(detail.title) || "challenge"}`;

  return (
    <div className="flex min-h-dvh flex-col lg:h-dvh">
      <SiteHeader />

      <div className="grid flex-1 grid-cols-1 lg:min-h-0 lg:grid-cols-[minmax(250px,300px)_minmax(0,1fr)_minmax(240px,290px)] xl:grid-cols-[320px_minmax(0,1fr)_310px]">
        {/* LEFT: the spine */}
        <aside className="max-h-[55vh] min-h-0 border-b border-line lg:max-h-none lg:border-r lg:border-b-0" aria-label="traceback">
          <Spine frames={frames} resolved={resolved} visited={visited} activeFrame={activeFrame} onOpen={openFrame} />
        </aside>

        {/* CENTER: tabs, editor, submissions */}
        <section className="flex h-[75vh] min-h-0 min-w-0 flex-col lg:h-auto" aria-label="editor">
          <div className="flex h-9 shrink-0 items-stretch border-b border-line bg-panel">
            <div role="tablist" aria-label="open files" className="flex min-w-0 flex-1 overflow-x-auto">
              {tabs.map((path) => {
                const label = labels.get(path)!;
                const isActive = path === active;
                return (
                  <div
                    key={path}
                    className={`group flex shrink-0 items-stretch border-r border-line ${
                      isActive ? "bg-base text-text" : "text-dim hover:text-text"
                    }`}
                  >
                    <button
                      type="button"
                      role="tab"
                      aria-selected={isActive}
                      title={path}
                      onClick={() => open(path)}
                      className={`flex items-center gap-1.5 border-t pl-3 pr-1 text-[12px] outline-none transition-colors duration-[120ms] ${
                        isActive ? "border-text" : "border-transparent"
                      }`}
                    >
                      {traced.has(path) && <span aria-hidden className="block h-[5px] w-[5px] bg-causal" />}
                      <span>{label.name}</span>
                      {label.hint && <span className="text-[10.5px] text-dim">{label.hint}</span>}
                    </button>
                    <button
                      type="button"
                      onClick={() => closeTab(path)}
                      aria-label={`close ${path}`}
                      className={`flex w-6 items-center justify-center border-t border-transparent text-[13px] outline-none transition-colors duration-[120ms] hover:text-text focus-visible:text-text ${
                        isActive ? "border-text" : ""
                      }`}
                    >
                      {modified.has(path) ? (
                        <>
                          <span className="text-error group-hover:hidden" aria-label="modified">●</span>
                          <span className="hidden group-hover:inline">×</span>
                        </>
                      ) : (
                        "×"
                      )}
                    </button>
                  </div>
                );
              })}
            </div>
            <div className="flex shrink-0 items-center gap-3 px-3 text-[11px] text-dim">
              {activeReadOnly && <span title="test files and non-Python files can't be patched">read-only · the suite is the judge</span>}
              {active && modified.has(active) && (
                <button type="button" onClick={() => revert(active)} className="link text-dim hover:text-text">
                  revert file
                </button>
              )}
            </div>
          </div>

          <div className="relative min-h-0 flex-1">
            <div ref={hostRef} className="absolute inset-0" />
            {active === null && (
              <div className="absolute inset-0 flex items-center justify-center bg-base p-6 text-center text-dim animate-fade">
                <p>
                  open a frame from the trace, or a file from the tree.
                  <br />
                  <span className="kbd mt-3">{alt}[</span> <span className="kbd">{alt}]</span> walks the trace.
                </p>
              </div>
            )}
          </div>

          {attempts.length > 0 && (
            <SubmitLog
              attempts={attempts}
              open={logOpen}
              onToggle={() => setLogOpen((o) => !o)}
              onOpenTest={openTest}
              now={now}
            />
          )}
        </section>

        {/* RIGHT: brief, timer, files */}
        <aside className="flex min-h-0 flex-col border-t border-line lg:border-t-0 lg:border-l" aria-label="challenge">
          <div className="shrink-0 border-b border-line px-4 pt-3 pb-4">
            <Link href={`/repo/?name=${encodeURIComponent(detail.repo)}`} className="text-[11px] text-dim transition-colors duration-[120ms] hover:text-text">
              ← {repoDisplay(detail.repo)} course
            </Link>
            <div className="mt-3 flex items-start justify-between gap-3">
              <p className="label">
                <span className={LABEL_COLOR[detail.difficulty_label]}>{detail.difficulty_label}</span>
                <span className="text-dim"> · {detail.language.toLowerCase()}</span>
              </p>
              <DifficultyBars breakdown={detail.breakdown} failing={detail.failing_test_count} total={detail.total_tests} />
            </div>
            <h1 className="mt-1 text-[16px] font-bold leading-snug text-text">{detail.title}</h1>
            <p className="mt-2 text-[12px] leading-[1.6] text-text/90">{detail.description}</p>
          </div>

          <div className="shrink-0 border-b border-line px-4 py-3">
            <h2 className="label">
              failing · {detail.failing_tests.length} of {thousands(detail.total_tests)}
            </h2>
            <ul className="mt-2 max-h-[132px] space-y-1 overflow-y-auto">
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
                        <span className="text-error">✗</span>
                        <span className="min-w-0 break-all text-text group-hover:underline group-focus-visible:underline">
                          {names[names.length - 1] ?? path}
                        </span>
                      </span>
                      <span className="block truncate pl-[2.2ch] text-[10.5px] text-dim">
                        {[...names.slice(0, -1), path.split("/").pop()].join(" · ")}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>

          <div className="flex shrink-0 items-end justify-between gap-3 border-b border-line px-4 py-3">
            <div>
              <h2 className="label">time</h2>
              <p
                className={`mt-0.5 text-[28px] font-bold leading-none tabular-nums tracking-[-0.02em] ${solvedAt ? "text-success" : "text-text"}`}
                role="timer"
                aria-label="time on this challenge"
              >
                {clock(elapsed)}
              </p>
            </div>
            <dl className="grid grid-cols-[auto_auto] gap-x-2 gap-y-0.5 text-[10.5px] text-dim">
              <dt className="text-right text-text">{mod}↵</dt>
              <dd>submit</dd>
              <dt className="text-right text-text">
                {alt}[ {alt}]
              </dt>
              <dd>walk trace</dd>
              <dt className="text-right text-text">{alt}w</dt>
              <dd>close tab</dd>
              <dt className="text-right text-text">{mod}f</dt>
              <dd>find</dd>
            </dl>
          </div>

          <div className="flex min-h-[240px] flex-1 flex-col lg:min-h-0">
            <h2 className="label shrink-0 px-4 pt-3 pb-1">
              files <span className="normal-case tracking-normal">· {thousands(tree.files.size)}</span>
            </h2>
            <div className="min-h-0 flex-1 overflow-y-auto pb-2">
              <FileTree
                nodes={nodes}
                expanded={expanded}
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
        </aside>
      </div>

      {/* BOTTOM: status bar */}
      <footer className="sticky bottom-0 z-20 flex h-7 shrink-0 items-center justify-between gap-4 border-t border-line bg-panel px-3 text-[11px] tabular-nums text-dim">
        <p className="flex min-w-0 items-center gap-2 truncate">
          <span className="text-text">{statusName}</span>
          <span>·</span>
          <span className={solvedAt ? "text-success" : "text-text"}>{clock(elapsed)}</span>
          <span>·</span>
          <span>{plural(tabs.length, "file")} open</span>
          {modified.size > 0 && (
            <>
              <span>·</span>
              <span className="text-error">{modified.size} modified</span>
            </>
          )}
          <span>·</span>
          <button
            type="button"
            onClick={submit}
            disabled={grading || solvedAt !== null}
            className="text-text transition-colors duration-[120ms] hover:text-success disabled:text-dim"
          >
            {mod}↵ submit
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
// submission log
// ---------------------------------------------------------------------------

function AttemptSummary({ attempt, now }: { attempt: Attempt; now: number }) {
  if (attempt.state === "sending") return <span>sending patch…</span>;
  if (attempt.state === "grading")
    return (
      <span className="text-text">
        grading · {clock(now - attempt.sentAt)} <Cursor className="!h-[0.9em] !w-[0.45em]" />
      </span>
    );
  if (attempt.state === "blocked" || attempt.state === "error") return <span className="text-error">✗ {attempt.message}</span>;
  const r = attempt.result!;
  if (r.verdict === "PASS") return <span className="font-bold text-success">PASS · {thousands(r.tests_passed ?? 0)} green</span>;
  if (r.verdict === "FAIL") return <span className="text-error">FAIL · {plural(r.failing_tests?.length ?? 0, "test")} red</span>;
  return <span className="text-error">REJECTED · {r.reason}</span>;
}

function SubmitLog({
  attempts,
  open,
  onToggle,
  onOpenTest,
  now,
}: {
  attempts: Attempt[];
  open: boolean;
  onToggle: () => void;
  onOpenTest: (nodeId: string) => void;
  now: number;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const last = attempts[attempts.length - 1];

  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [attempts, open]);

  return (
    <div className="flex max-h-[40%] shrink-0 flex-col border-t border-line bg-panel">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex h-7 shrink-0 items-center justify-between px-3 text-[11px] text-dim outline-none hover:text-text focus-visible:text-text"
      >
        <span className="label !text-inherit">submissions · {attempts.length}</span>
        <span aria-hidden>{open ? "▾" : "▴"}</span>
      </button>
      {open && (
        <div ref={bodyRef} role="log" aria-live="polite" className="min-h-0 overflow-y-auto px-3 pb-3 text-[12px] leading-[1.7]">
          {attempts.slice(-4).map((a) => (
            <div key={a.n} className={`animate-fade ${a === last ? "" : "opacity-60"} ${a.n > 1 ? "mt-2" : ""}`}>
              <div className="whitespace-pre-wrap text-text">
                $ submit #{a.n}
                {a.files.length > 0 && (
                  <span className="text-dim">
                    {" "}
                    · {a.files.join(", ")} · <span className="text-success">+{a.added}</span>{" "}
                    <span className="text-error">−{a.removed}</span>
                  </span>
                )}
              </div>

              {a.state === "blocked" && <div className="text-error">✗ {a.message}</div>}
              {a.state === "error" && <div className="text-error">✗ {a.message}</div>}
              {a.state === "sending" && <div className="text-dim">· sending</div>}
              {(a.state === "grading" || a.state === "done") && (
                <div className="text-dim">· {a.submissionId} · patch applied in a clean tree, full suite running</div>
              )}
              {a.state === "grading" && (
                <div className="text-dim">
                  · {clock(now - a.sentAt)}
                  {now - a.sentAt > SLOW_AFTER_MS && " · slower than usual, still waiting"} <Cursor className="!h-[0.9em] !w-[0.45em]" />
                </div>
              )}

              {a.result?.verdict === "PASS" && (
                <div className="font-bold text-success">
                  ✓ PASS — {thousands(a.result.tests_passed ?? 0)} tests green · opening the reveal…
                </div>
              )}
              {a.result?.verdict === "FAIL" && (
                <>
                  <div className="text-error">
                    ✗ FAIL — {a.result.reason ?? `${plural(a.result.failing_tests?.length ?? 0, "test")} still red`}
                    {a.result.tests_passed !== undefined && (
                      <span className="text-dim"> · {thousands(a.result.tests_passed)} passed</span>
                    )}
                  </div>
                  {(a.result.failing_tests ?? []).slice(0, 12).map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => onOpenTest(t)}
                      className="block max-w-full truncate pl-4 text-left text-dim hover:text-text"
                    >
                      {t}
                    </button>
                  ))}
                  {(a.result.failing_tests?.length ?? 0) > 12 && (
                    <div className="pl-4 text-dim">+{(a.result.failing_tests?.length ?? 0) - 12} more</div>
                  )}
                </>
              )}
              {a.result?.verdict === "REJECTED" && (
                <>
                  <div className="text-error">✗ {REJECTION[a.result.reason ?? ""] ?? `rejected: ${a.result.reason}`}</div>
                  {a.result.detail && <div className="whitespace-pre-wrap pl-4 text-dim">{a.result.detail.slice(0, 600)}</div>}
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
