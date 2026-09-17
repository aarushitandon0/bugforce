"use client";

import { useEffect, useRef } from "react";
import { truncateLeft } from "@/lib/format";
import type { Frame } from "@/lib/traceback";

/**
 * The traceback as a clickable spine: outermost call at the top, the frame
 * that raised at the bottom, joined by the violet causal rail. Rows for files
 * in the challenge tree open that file at that line; frames from the standard
 * library or site-packages stay on the rail, dimmed, so the shape of the call
 * stack is still honest.
 */
export function Spine({
  frames,
  resolved,
  visited,
  activeFrame,
  onOpen,
}: {
  frames: Frame[];
  resolved: (string | null)[];
  visited: ReadonlySet<number>;
  activeFrame: number | null;
  onOpen: (index: number) => void;
}) {
  const listRef = useRef<HTMLOListElement>(null);
  const deepest = frames.length - 1;
  const raised = frames[deepest];
  const outside = resolved.filter((p) => p === null).length;

  useEffect(() => {
    if (activeFrame === null) return;
    listRef.current
      ?.querySelector<HTMLElement>(`[data-frame="${activeFrame}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [activeFrame]);

  // Up/down moves between clickable rows; the global ⌥[ ⌥] also works from the editor.
  const onKeyDown = (e: React.KeyboardEvent<HTMLOListElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const buttons = [...(listRef.current?.querySelectorAll<HTMLButtonElement>("button[data-frame]") ?? [])];
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (at === -1) return;
    e.preventDefault();
    buttons[Math.max(0, Math.min(buttons.length - 1, at + (e.key === "ArrowDown" ? 1 : -1)))]?.focus();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-line px-4 pt-3 pb-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="label">traceback</h2>
          <span className="text-[11px] tabular-nums text-dim">
            {visited.size}/{resolved.length - outside} visited
          </span>
        </div>
        {raised ? (
          <div className="mt-2 border-l border-error pl-3 text-[12px] leading-[1.5]">
            {/* pytest usually repeats the name in the first E line; show it once */}
            {!(raised.exception && raised.errors[0]?.split(":")[0].endsWith(raised.exception)) && (
              <div className="font-bold text-error">{raised.exception ?? "error"}</div>
            )}
            {raised.errors.slice(0, 3).map((line, i) => {
              const colon = line.indexOf(":");
              const head = i === 0 && raised.exception && line.split(":")[0].endsWith(raised.exception);
              return (
                <div key={i} className="break-words text-text">
                  {head ? (
                    <>
                      <span className="font-bold text-error">{colon === -1 ? line : line.slice(0, colon + 1)}</span>
                      {colon === -1 ? "" : line.slice(colon + 1)}
                    </>
                  ) : (
                    line
                  )}
                </div>
              );
            })}
            {raised.errors.length > 3 && <div className="text-dim">+{raised.errors.length - 3} more lines</div>}
          </div>
        ) : (
          <p className="mt-2 text-[12px] text-dim">no stack frames could be read from this traceback.</p>
        )}
      </div>

      <ol
        ref={listRef}
        onKeyDown={onKeyDown}
        aria-label="stack frames, outermost first"
        className="min-h-0 flex-1 overflow-y-auto py-2"
      >
        {frames.map((frame, i) => {
          const path = resolved[i];
          const isVisited = visited.has(i);
          const isActive = activeFrame === i;
          const isRaised = i === deepest;
          const first = i === 0;
          const last = i === deepest;

          const marker = path === null
            ? "h-[5px] w-[5px] bg-line"
            : isRaised
              ? `h-[9px] w-[9px] border border-error ${isVisited ? "bg-error" : "bg-base"}`
              : `h-[9px] w-[9px] border border-causal ${isVisited ? "bg-causal" : "bg-base"}`;

          const body = (
            <>
              {/* rail */}
              <span aria-hidden className="relative flex w-[18px] shrink-0 justify-center">
                <span
                  className={`absolute left-1/2 w-px -translate-x-1/2 bg-causal/45 ${first ? "top-[11px]" : "top-0"} ${last ? "h-[11px]" : "bottom-0"}`}
                />
                <span className={`relative mt-[7px] block ${marker}`} />
              </span>

              <span className="min-w-0 flex-1 pb-2.5">
                <span className="flex items-baseline justify-between gap-2">
                  <span className={`truncate ${path === null ? "text-dim" : "text-text"} ${isRaised ? "font-bold" : ""}`}>
                    {frame.func ?? "<module>"}
                  </span>
                  <span className="shrink-0 text-[10px] tabular-nums text-dim">#{i + 1}</span>
                </span>
                <span className="block truncate text-[11px] text-dim" title={`${frame.path}:${frame.line}`}>
                  {path === null
                    ? `${truncateLeft(frame.path.split("/").slice(-2).join("/"), 34)}:${frame.line} · outside repo`
                    : `${truncateLeft(path, 36)}:${frame.line}`}
                </span>
                {frame.failingSource && path !== null && (
                  <span
                    className={`mt-1 block truncate border-l pl-2 text-[11.5px] ${
                      isRaised ? "border-error text-text" : isActive ? "border-causal text-text" : "border-line text-dim"
                    }`}
                  >
                    {frame.failingSource}
                  </span>
                )}
              </span>
            </>
          );

          return (
            <li key={i} className={isActive ? "bg-panel" : ""}>
              {path === null ? (
                <div className="flex gap-2 border-l border-transparent px-3 text-[12px] leading-[1.5]" title="not part of the repo: standard library or a dependency">
                  {body}
                </div>
              ) : (
                <button
                  type="button"
                  data-frame={i}
                  onClick={() => onOpen(i)}
                  aria-current={isActive ? "location" : undefined}
                  aria-label={`frame ${i + 1}: ${frame.func ?? "module"} at ${path} line ${frame.line}${isVisited ? ", visited" : ""}`}
                  className={`group flex w-full gap-2 border-l px-3 text-left text-[12px] leading-[1.5] outline-none transition-colors duration-[120ms] hover:bg-panel focus-visible:bg-panel ${
                    isActive ? "border-causal" : "border-transparent"
                  }`}
                >
                  {body}
                </button>
              )}
            </li>
          );
        })}
      </ol>

      <p className="border-t border-line px-4 py-2 text-[10.5px] leading-[1.5] text-dim">
        {resolved.length - outside <= 1
          ? "the trace never leaves the test. the bug is in something the test calls: read what it exercises and follow it into the library."
          : "the trace shows where it failed, not where it broke. walk it upward."}
      </p>
    </div>
  );
}
