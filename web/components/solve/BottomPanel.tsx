"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { useEffect, useRef } from "react";
import { REJECTION, SLOW_AFTER_MS, type Attempt } from "@/lib/attempt";
import { clock, plural, thousands } from "@/lib/format";
import { parseNodeId } from "@/lib/solve";
import { Cursor } from "../Cursor";

export type BottomTab = "problems" | "output";

/**
 * The editor's bottom dock: PROBLEMS (the tests that are red) and OUTPUT (the
 * submission log, which used to sit in the right rail and had nothing to do
 * with the brief). Collapsible; the status strip stays below it, unchanged.
 */
export function BottomPanel({
  tab,
  onTab,
  open,
  onToggle,
  failingTests,
  onOpenTest,
  attempts,
  now,
  height,
}: {
  tab: BottomTab;
  onTab: (tab: BottomTab) => void;
  open: boolean;
  onToggle: () => void;
  failingTests: string[];
  onOpenTest: (nodeId: string) => void;
  attempts: Attempt[];
  now: number;
  height: number;
}) {
  const TABS: { id: BottomTab; label: string; count: number }[] = [
    { id: "problems", label: "problems", count: failingTests.length },
    { id: "output", label: "output", count: attempts.length },
  ];

  return (
    <div className="flex shrink-0 flex-col border-t border-line bg-panel">
      <div className="flex h-7 shrink-0 items-stretch">
        {TABS.map(({ id, label, count }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={open && tab === id}
            onClick={() => {
              if (open && tab === id) onToggle();
              else {
                onTab(id);
                if (!open) onToggle();
              }
            }}
            className={`label flex items-center gap-1.5 border-b-2 px-3 outline-none transition-colors duration-[120ms] hover:!text-text focus-visible:!text-text ${
              open && tab === id ? "border-causal !text-text" : "border-transparent"
            }`}
          >
            {label}
            {count > 0 && <span className={`tabular-nums ${id === "problems" ? "text-error" : "text-dim"}`}>{count}</span>}
          </button>
        ))}
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          aria-label={open ? "collapse panel" : "expand panel"}
          className="ml-auto flex w-8 items-center justify-center text-dim outline-none transition-colors duration-[120ms] hover:text-text focus-visible:text-text"
        >
          {open ? <ChevronDown size={14} strokeWidth={1.5} /> : <ChevronUp size={14} strokeWidth={1.5} />}
        </button>
      </div>

      {open && (
        <div className="min-h-0 overflow-y-auto" style={{ height }}>
          {tab === "problems" ? (
            <Problems failingTests={failingTests} onOpenTest={onOpenTest} />
          ) : (
            <Output attempts={attempts} now={now} onOpenTest={onOpenTest} />
          )}
        </div>
      )}
    </div>
  );
}

function Problems({ failingTests, onOpenTest }: { failingTests: string[]; onOpenTest: (nodeId: string) => void }) {
  if (failingTests.length === 0) {
    return <p className="px-3 py-2 text-[12px] text-dim">no failing tests.</p>;
  }
  return (
    <ul className="py-1 text-[12px] leading-[22px]">
      {failingTests.map((nodeId) => {
        const { path, names } = parseNodeId(nodeId);
        return (
          <li key={nodeId}>
            <button
              type="button"
              onClick={() => onOpenTest(nodeId)}
              title={`open ${nodeId}`}
              className="flex w-full items-center gap-2 px-3 text-left outline-none transition-colors duration-[120ms] hover:bg-hover focus-visible:bg-hover"
            >
              <span className="shrink-0 text-error">✗</span>
              <span className="shrink-0 text-text">{names[names.length - 1] ?? path}</span>
              <span className="min-w-0 truncate text-dim">{[path, ...names.slice(0, -1)].join(" · ")}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function Output({
  attempts,
  now,
  onOpenTest,
}: {
  attempts: Attempt[];
  now: number;
  onOpenTest: (nodeId: string) => void;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const last = attempts[attempts.length - 1];

  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [attempts]);

  if (attempts.length === 0) {
    return (
      <p className="px-3 py-2 text-[12px] text-dim">
        nothing submitted yet. fix the source, then submit — the repo&apos;s own suite decides.
      </p>
    );
  }

  return (
    <div ref={bodyRef} role="log" aria-live="polite" className="px-3 py-2 text-[12px] leading-[1.7]">
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
              {now - a.sentAt > SLOW_AFTER_MS && " · slower than usual, still waiting"}{" "}
              <Cursor className="!h-[0.9em] !w-[0.45em]" />
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
  );
}
