"use client";

import Link from "next/link";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError, getForge, type ForgeStatus, type StreamRow, type StreamVerdict } from "@/lib/api";
import { REPLAY } from "@/lib/forge-data";
import { clock, plural, repoDisplay, truncateLeft } from "@/lib/format";
import { Cursor } from "./Cursor";

/** A line printed before (or instead of) an execution: the command, a refusal, an error. */
export interface LocalLine {
  tone: "command" | "text" | "dim" | "error" | "success";
  text: string;
}

const TONE: Record<LocalLine["tone"], string> = {
  command: "text-text",
  text: "text-text",
  dim: "text-dim",
  error: "text-error",
  success: "text-success",
};

const POLL_MS = 1500;
const MAX_BACKOFF_MS = 15_000;

function useForgeStatus(executionId: string | null) {
  const [status, setStatus] = useState<ForgeStatus | null>(null);
  const [problem, setProblem] = useState<{ message: string; fatal: boolean } | null>(null);

  useEffect(() => {
    setStatus(null);
    setProblem(null);
    if (!executionId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    const tick = async () => {
      try {
        const next = await getForge(executionId);
        if (cancelled) return;
        failures = 0;
        setStatus(next);
        setProblem(null);
        if (next.status === "RUNNING") timer = setTimeout(tick, POLL_MS);
      } catch (error) {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 404) {
          setProblem({ message: `no forge named ${executionId}`, fatal: true });
          return;
        }
        failures += 1;
        const message = error instanceof Error ? error.message : String(error);
        setProblem({ message: `${message} — retrying`, fatal: false });
        timer = setTimeout(tick, Math.min(MAX_BACKOFF_MS, POLL_MS * 2 ** failures));
      }
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [executionId]);

  return { status, problem };
}

function useElapsed(status: ForgeStatus | null): string | null {
  const [now, setNow] = useState(() => Date.now());
  const running = status?.status === "RUNNING";
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);
  if (!status) return null;
  const end = status.stopped_at ? Date.parse(status.stopped_at) : now;
  return clock(end - Date.parse(status.started_at));
}


// ---------------------------------------------------------------------------
// replay
// ---------------------------------------------------------------------------

/** One row every 25ms, then a pause on the finished screen, then round again. */
const REPLAY_ROW_MS = 25;
const REPLAY_PAUSE_TICKS = 180;
/** rows revealed before the first mutation line, so the two setup steps land */
const REPLAY_LEAD_TICKS = 28;

/**
 * A real recorded forge, replayed.
 *
 * The stream box sits in the most important position on the landing page and
 * used to say "idle" over two comment lines. This plays back the run in
 * phase5_output -- the same rows, the same taxonomy, the same counts -- until
 * the viewer forges something themselves, at which point the live stream takes
 * over. It is labelled `replay` in the corner so it never pretends to be live.
 */
function useReplay(enabled: boolean): ForgeStatus | null {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setTick(0);
      return;
    }
    const last = REPLAY_LEAD_TICKS + REPLAY.rows.length + REPLAY_PAUSE_TICKS;
    const timer = setInterval(() => setTick((t) => (t >= last ? 0 : t + 1)), REPLAY_ROW_MS);
    return () => clearInterval(timer);
  }, [enabled]);

  return useMemo(() => {
    if (!enabled) return null;
    const shown = Math.max(0, Math.min(REPLAY.rows.length, tick - REPLAY_LEAD_TICKS));
    const rows = REPLAY.rows.slice(0, shown);
    const done = shown === REPLAY.rows.length;
    const counts: Record<StreamVerdict, number> = { keep: 0, drop: 0, gap: 0, scoring: 0 };
    for (const row of rows) counts[row.verdict] += 1;
    return {
      execution_id: "replay",
      repo_url: null,
      status: done ? "SUCCEEDED" : "RUNNING",
      phase: done ? "done" : tick < REPLAY_LEAD_TICKS / 2 ? "baseline" : tick < REPLAY_LEAD_TICKS ? "generate" : "run",
      started_at: new Date().toISOString(),
      stopped_at: null,
      error: null,
      cause: null,
      baseline: tick >= REPLAY_LEAD_TICKS / 2 ? REPLAY.baseline : null,
      candidates: REPLAY.candidates,
      batches: tick >= REPLAY_LEAD_TICKS ? REPLAY.batches : 0,
      batches_done: Math.round((shown / REPLAY.rows.length) * REPLAY.batches),
      rows,
      counts,
      summary: done ? { ...REPLAY.summary, repo: REPLAY.repo } : null,
    } satisfies ForgeStatus;
  }, [enabled, tick]);
}

// ---------------------------------------------------------------------------
// rows
// ---------------------------------------------------------------------------

function Row({ row, locationWidth }: { row: StreamRow; locationWidth: number }) {
  const glyph = row.verdict === "keep" ? "✓" : row.verdict === "scoring" ? "·" : "✗";
  const glyphTone =
    row.verdict === "keep" ? "text-success" : row.verdict === "gap" ? "text-error" : "text-dim";
  const body = row.verdict === "drop" || row.verdict === "scoring" ? "text-dim" : "text-text";

  const tests =
    row.tests_red === null
      ? "  —" + " ".repeat(10)
      : `${String(row.tests_red).padStart(3)} ${row.tests_red === 1 ? "test red " : "tests red"}`;
  const redTone = row.tests_red && row.verdict !== "drop" ? "text-error" : "";

  return (
    <div className={`whitespace-pre animate-fade ${body}`}>
      <span className={glyphTone}>{glyph}</span>{" "}
      {truncateLeft(row.location, locationWidth).padEnd(locationWidth)}
      {"  "}
      <span className={redTone}>{tests}</span>
      {"   "}
      {row.verdict === "gap" ? (
        <>
          test gap <span className="text-error">→ report</span>
        </>
      ) : (
        <>
          {row.detail.padEnd(17)}
          {row.verdict === "keep" && <span className="font-bold text-success">KEEP</span>}
          {row.verdict === "drop" && "drop"}
          {row.verdict === "scoring" && "scoring"}
        </>
      )}
    </div>
  );
}

function Step({ done, name, children }: { done: boolean; name: string; children: React.ReactNode }) {
  return (
    <div className={`whitespace-pre animate-fade ${done ? "text-text" : "text-dim"}`}>
      <span className={done ? "text-success" : "text-dim"}>{done ? "✓" : "·"}</span> {name.padEnd(9)} {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// stream
// ---------------------------------------------------------------------------

export function ForgeStream({
  executionId,
  repoLabel,
  localLines,
  legend = false,
}: {
  executionId: string | null;
  repoLabel: string | null;
  localLines: LocalLine[];
  /** show the verdict key in the footer instead of as a block underneath */
  legend?: boolean;
}) {
  const { status: live, problem } = useForgeStatus(executionId);
  // the replay yields the moment there is anything real to show
  const replaying = executionId === null && localLines.length === 0;
  const replay = useReplay(replaying);
  const status = live ?? replay;
  const elapsed = useElapsed(replaying ? null : live);
  const bodyRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  const rows = status?.rows ?? [];
  const locationWidth = Math.max(14, Math.min(34, ...rows.map((r) => r.location.length)));

  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [rows.length, status?.phase, localLines.length, problem?.message]);

  const onScroll = () => {
    const el = bodyRef.current;
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  const running = status?.status === "RUNNING" || (executionId !== null && !status && !problem?.fatal);
  const finished = status && status.status !== "RUNNING";
  const repo = status?.summary?.repo ?? null;
  const headerRepo = repoLabel ?? (repo ? repoDisplay(repo) : null);

  return (
    /*
     * data-theme="dark" re-declares the whole dark palette for this subtree, so
     * the stream stays a terminal on a light page -- the way a terminal window
     * does on a light desktop. Without it every verdict colour in here would be
     * a dark ink on a white card and the page would lose its one anchor.
     */
    <div
      data-theme="dark"
      className="flex h-full min-h-0 flex-col border border-line bg-panel text-text"
    >
      <div className="flex shrink-0 items-center justify-between gap-4 border-b border-line px-4 py-2 text-[11px] text-dim">
        <span className="truncate">
          {replaying ? (
            <>
              <span className="text-text">{REPLAY.display}</span>
              {" \u00b7 "}
              {REPLAY.commit}
            </>
          ) : executionId ? (
            <>
              {headerRepo && <span className="text-text">{headerRepo}</span>}
              {headerRepo && " · "}
              {executionId}
            </>
          ) : (
            "generation stream"
          )}
        </span>
        <span className="shrink-0 tabular-nums">
          {elapsed && <>{elapsed} · </>}
          {replaying ? (
            <span className="border border-line px-1.5 py-px" title="a real recorded forge, not a live one">
              replay
            </span>
          ) : status ? (
            status.status.toLowerCase().replace("_", " ")
          ) : executionId ? (
            "connecting"
          ) : (
            "idle"
          )}
        </span>
      </div>

      <div
        ref={bodyRef}
        onScroll={onScroll}
        role="log"
        aria-live="polite"
        aria-label="generation stream"
        className="min-h-[240px] flex-1 overflow-auto px-4 py-3 text-[12.5px] leading-[1.75]"
      >
        {replaying && (
          <div className="whitespace-pre-wrap text-dim">
            $ forge github.com/{REPLAY.display}
          </div>
        )}

        {localLines.map((line, i) => (
          <div key={i} className={`whitespace-pre-wrap animate-fade ${TONE[line.tone]}`}>
            {line.text}
          </div>
        ))}

        {status && (
          <>
            <Step done={status.baseline !== null} name="baseline">
              {status.baseline
                ? `${plural(status.baseline.total_tests, "test")} green · ${plural(status.baseline.covered_lines, "line")} covered`
                : "running the full suite with per-test coverage…"}
            </Step>
            {status.baseline && (
              <Step done={status.batches > 0} name="generate">
                {status.batches > 0
                  ? `${plural(status.candidates, "mutation")} on covered lines · ${plural(status.batches, "batch", "batches")}`
                  : "locating AST mutations on covered lines…"}
              </Step>
            )}

            {rows.length > 0 && <div className="h-2" />}
            {rows.map((row) => (
              <Row key={row.id} row={row} locationWidth={locationWidth} />
            ))}

            {status.status === "RUNNING" && status.phase === "run" && (
              <div className="mt-2 whitespace-pre text-dim">
                · {status.batches_done} of {plural(status.batches, "batch", "batches")} run against their covering tests
              </div>
            )}
            {status.status === "RUNNING" && status.phase === "score" && (
              <div className="mt-2 whitespace-pre text-dim">
                · full-suite run for each survivor · {status.counts.scoring} left
              </div>
            )}
            {status.status === "RUNNING" && status.phase === "package" && (
              <div className="mt-2 whitespace-pre text-dim">· naming, packaging trees, writing the gap report…</div>
            )}

            {status.status === "SUCCEEDED" && status.summary && (
              <div className="mt-3 animate-fade">
                <div className="font-bold text-success">
                  {plural(status.summary.challenges_ready, "challenge")} ready ·{" "}
                  {plural(status.summary.test_gaps, "test gap")} found
                </div>
                <div className="mt-1 flex flex-wrap gap-x-6 text-text">
                  {repo && status.summary.challenges_ready > 0 && (
                    <Link className="link" href={`/repo/?name=${encodeURIComponent(repo)}`}>
                      → learn {repoDisplay(repo).split("/")[1]} in {plural(status.summary.challenges_ready, "bug")}
                    </Link>
                  )}
                  {repo && status.summary.test_gaps > 0 && (
                    <Link className="link" href={`/gaps/?repo=${encodeURIComponent(repo)}`}>
                      → read the gap report
                    </Link>
                  )}
                </div>
              </div>
            )}

            {finished && status.status !== "SUCCEEDED" && (
              <div className="mt-3 whitespace-pre-wrap text-error animate-fade">
                ✗ forge {status.status.toLowerCase().replace("_", " ")}
                {status.error && ` · ${status.error}`}
                {status.cause && `\n  ${status.cause.slice(0, 400)}`}
              </div>
            )}
          </>
        )}

        {problem && (
          <div className={`whitespace-pre-wrap ${problem.fatal ? "text-error" : "text-dim"}`}>
            {problem.fatal ? "✗ " : "· "}
            {problem.message}
          </div>
        )}

        {(running || replaying || (localLines.length > 0 && !executionId) || finished) && (
          <div className="mt-1 text-text">
            {running ? "" : "$ "}
            <Cursor />
          </div>
        )}
      </div>

      {(status || legend) && (
        <div className="shrink-0 border-t border-line px-4 py-2 text-[11px] text-dim">
          {status && (
            <div className="flex flex-wrap gap-x-5 tabular-nums">
              <span>
                <span className="text-success">{status.counts.keep}</span> kept
              </span>
              <span>{status.counts.drop} dropped</span>
              <span>
                <span className="text-error">{status.counts.gap}</span> test gaps
              </span>
              {status.counts.scoring > 0 && <span>{status.counts.scoring} scoring</span>}
            </div>
          )}
          {legend && (
            <dl className={`flex flex-wrap gap-x-5 gap-y-0.5 ${status ? "mt-1.5 border-t border-line pt-1.5" : ""}`}>
              <div>
                <dt className="inline text-success">&#10003; keep</dt>{" "}
                <dd className="inline">caught by the suite</dd>
              </div>
              <div>
                <dt className="inline">&#10007; drop</dt>{" "}
                <dd className="inline">too loud, too easy, timed out</dd>
              </div>
              <div>
                <dt className="inline text-error">&#10007; test gap</dt>{" "}
                <dd className="inline">no test noticed it</dd>
              </div>
            </dl>
          )}
        </div>
      )}
    </div>
  );
}
