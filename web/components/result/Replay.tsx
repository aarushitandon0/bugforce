"use client";

import { useEffect, useRef, useState } from "react";
import { clock, truncateLeft } from "@/lib/format";
import {
  causalPath,
  describeReplay,
  dwell,
  ELSEWHERE,
  planLanes,
  toSegments,
  type Visit,
} from "@/lib/replay";

/**
 * The investigation replay, drawn as plain SVG.
 *
 * One lane per file. The white line is where the learner was, over time; the
 * violet line is the path the defect actually took, from the frame that raised
 * up to the file that was mutated. The distance between them is the lesson,
 * so the two lines are drawn in the same lanes at the same scale.
 */

const ROW = 26;
const TOP = 30;
const BOTTOM = 26;
const GAP = 12;
/** right-hand gutter for per-file dwell times, kept out of the plot */
const DWELL_W = 46;

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Rendered at exact pixel width rather than scaled, so 11px labels stay 11px.
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    setWidth(el.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

export interface ReplayProps {
  visits: Visit[];
  /** traceback frame paths, outermost first, as pytest prints them */
  frames: string[];
  mutatedPath: string;
  displacement: number;
  startedAt: number;
  endedAt: number;
}

export function Replay({ visits, frames, mutatedPath, displacement, startedAt, endedAt }: ReplayProps) {
  const [ref, width] = useWidth<HTMLDivElement>();

  const totalMs = Math.max(1, endedAt - startedAt);
  const segments = toSegments(visits, endedAt);
  const causal = causalPath(frames, mutatedPath);
  const plan = planLanes(segments, causal);
  const ranking = dwell(segments);
  const sentence = describeReplay({ segments, causal, mutatedPath, displacement, totalMs });

  const labelW = Math.max(96, Math.min(230, width * 0.32));
  const plotX = labelW + GAP;
  const plotW = Math.max(40, width - plotX - GAP - DWELL_W);
  const height = TOP + plan.lanes.length * ROW + BOTTOM;
  const laneY = (lane: number) => TOP + lane * ROW + ROW / 2;
  const timeX = (at: number) => plotX + ((Math.min(endedAt, Math.max(startedAt, at)) - startedAt) / totalMs) * plotW;

  // the learner's line: horizontal while in a file, vertical when switching
  const learner: string[] = [];
  segments.forEach((segment, i) => {
    const y = laneY(plan.laneOf(segment.path));
    const x1 = timeX(segment.from);
    const x2 = timeX(segment.to);
    learner.push(i === 0 ? `M ${x1.toFixed(1)} ${y}` : `V ${y}`);
    learner.push(`H ${Math.max(x2, x1 + 1).toFixed(1)}`);
  });

  // the causal line: no timestamps of its own, so it is spread evenly across
  // the same axis -- it is a route, not a schedule.
  const stepW = plotW / Math.max(1, causal.length);
  const causalLine: string[] = [];
  causal.forEach((path, i) => {
    const y = laneY(plan.laneOf(path));
    const x1 = plotX + i * stepW;
    causalLine.push(i === 0 ? `M ${x1.toFixed(1)} ${y}` : `V ${y}`);
    causalLine.push(`H ${(x1 + stepW).toFixed(1)}`);
  });

  // a "00:00" label is ~34px wide, so thin out the axis rather than overlap it
  const ticks = plotW < 240 ? [0, 1] : plotW < 420 ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1];

  return (
    <section className="mt-16 border-t border-line pt-8" aria-label="investigation replay">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h2 className="label">where you looked · where it was</h2>
        <p className="flex items-center gap-4 text-[11px] text-muted">
          <span className="flex items-center gap-1.5">
            <svg width="16" height="8" aria-hidden>
              <line x1="0" y1="4" x2="16" y2="4" stroke="var(--color-text)" strokeWidth="1.5" />
            </svg>
            you
          </span>
          <span className="flex items-center gap-1.5">
            <svg width="16" height="8" aria-hidden>
              <line x1="0" y1="4" x2="16" y2="4" stroke="var(--color-causal)" strokeWidth="1.5" />
            </svg>
            the causal path
          </span>
        </p>
      </div>

      <div ref={ref} className="mt-5 w-full">
        {width > 0 && (
          <svg
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label={sentence}
            className="block"
          >
            <title>Investigation replay</title>

            {/* lanes */}
            {plan.lanes.map((path, i) => {
              const y = laneY(i);
              const isMutated = path === mutatedPath;
              const onCausal = causal.includes(path);
              const spent = ranking.find((r) => r.path === path)?.ms ?? 0;
              const label =
                path === ELSEWHERE
                  ? `${plan.othersCount} other files`
                  : truncateLeft(path, Math.max(8, Math.floor(labelW / 6.7)));
              return (
                <g key={path}>
                  <line x1={plotX} y1={y} x2={plotX + plotW} y2={y} stroke="var(--color-line)" strokeWidth="1" />
                  {isMutated && (
                    <rect
                      x={plotX}
                      y={y - ROW / 2}
                      width={plotW}
                      height={ROW}
                      fill="var(--color-error)"
                      opacity="0.06"
                    />
                  )}
                  <text
                    x={labelW}
                    y={y + 3.5}
                    textAnchor="end"
                    fontSize="11"
                    fill={isMutated ? "var(--color-error)" : onCausal ? "var(--color-causal)" : "var(--color-dim)"}
                  >
                    {label}
                  </text>
                  {spent > 0 && (
                    <text x={width} y={y + 3.5} textAnchor="end" fontSize="9.5" fill="var(--color-dim)">
                      {clock(spent)}
                    </text>
                  )}
                </g>
              );
            })}

            {/* the causal path, under the learner's line so both stay readable */}
            <path d={causalLine.join(" ")} fill="none" stroke="var(--color-causal)" strokeWidth="3" opacity="0.5" />
            {causal.map((path, i) => (
              <rect
                key={`c${i}`}
                x={plotX + i * stepW - 2.5}
                y={laneY(plan.laneOf(path)) - 2.5}
                width="5"
                height="5"
                fill="var(--color-causal)"
              />
            ))}
            <text x={plotX + 6} y={laneY(plan.laneOf(causal[0])) - 9} fontSize="9.5" fill="var(--color-causal)">
              raised here
            </text>
            <text
              x={plotX + plotW - 2}
              y={laneY(plan.laneOf(mutatedPath)) - 9}
              textAnchor="end"
              fontSize="9.5"
              fill="var(--color-causal)"
            >
              the bug
            </text>

            {/* the learner */}
            <path d={learner.join(" ")} fill="none" stroke="var(--color-text)" strokeWidth="1.6" />
            {segments.map((segment, i) => (
              <rect
                key={`s${i}`}
                x={timeX(segment.from) - 2.5}
                y={laneY(plan.laneOf(segment.path)) - 2.5}
                width="5"
                height="5"
                fill="var(--color-text)"
              />
            ))}

            {/* time */}
            {ticks.map((t) => (
              <g key={t}>
                <line
                  x1={plotX + t * plotW}
                  y1={TOP - 6}
                  x2={plotX + t * plotW}
                  y2={height - BOTTOM + 4}
                  stroke="var(--color-line)"
                  strokeWidth="1"
                  opacity={t === 0 || t === 1 ? 1 : 0.55}
                />
                <text
                  x={plotX + t * plotW}
                  y={height - BOTTOM + 17}
                  textAnchor={t === 0 ? "start" : t === 1 ? "end" : "middle"}
                  fontSize="10"
                  fill="var(--color-dim)"
                >
                  {clock(t * totalMs)}
                </text>
              </g>
            ))}
            <text x={labelW} y={height - BOTTOM + 17} textAnchor="end" fontSize="10" fill="var(--color-dim)">
              time
            </text>
          </svg>
        )}
      </div>

      <p className="mt-5 max-w-[80ch] text-[14px] leading-[1.7] text-text">{sentence}</p>
      {segments.length > 0 && (
        <p className="mt-2 text-[11px] text-muted">
          {segments.length} file {segments.length === 1 ? "opening" : "openings"} across {ranking.length}{" "}
          {ranking.length === 1 ? "file" : "files"} · logged in your browser and sent with the submission
        </p>
      )}
    </section>
  );
}
