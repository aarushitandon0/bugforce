"use client";

import type { Breakdown } from "@/lib/api";
import { plural } from "@/lib/format";
import { Tip } from "./Tip";

interface Metric {
  short: string;
  name: string;
  fill: (b: Breakdown) => number;
  value: (b: Breakdown, failing: number, total: number) => string;
  explain: string;
}

/**
 * The three inputs to bugforge/select.py's score, each already normalised to
 * 0..1 there (d, s, n). Taller always means harder.
 */
const METRICS: Metric[] = [
  {
    short: "d",
    name: "displacement",
    fill: (b) => b.d,
    value: (b) =>
      b.displacement >= 4 ? "4+ frames, or not in the trace at all" : plural(b.displacement, "frame"),
    explain:
      "How far the defect sits from where the test fails, in stack frames. The trace doesn't point at the bug; you walk back up it.",
  },
  {
    short: "s",
    name: "search space",
    fill: (b) => b.s,
    value: (b) => `${plural(b.search_space, "source file")} executed`,
    explain: "How many source files the failing test runs through. Every one of them is a place the bug could be.",
  },
  {
    short: "n",
    name: "noise",
    fill: (b) => b.n,
    value: (_b, failing, total) => `${failing} of ${plural(total, "test")} red`,
    explain:
      "How much of the suite goes red. One quiet failure gives you less to triangulate from, so fewer red tests make this bar taller.",
  },
];

export function DifficultyBars({
  breakdown,
  failing,
  total,
  size = "sm",
}: {
  breakdown: Breakdown | null;
  failing: number;
  total: number;
  size?: "sm" | "lg";
}) {
  if (!breakdown) return <span className="text-[11px] text-dim">no breakdown</span>;
  const tall = size === "lg";

  return (
    <div className={`flex items-end ${tall ? "gap-3" : "gap-[6px]"}`}>
      {METRICS.map((metric) => {
        const fill = Math.max(0, Math.min(1, metric.fill(breakdown)));
        const value = metric.value(breakdown, failing, total);
        return (
          <Tip
            key={metric.short}
            align="end"
            label={`${metric.name}: ${value}`}
            content={
              <>
                <span className="block text-text">
                  {metric.name} <span className="text-dim">·</span> {value}
                </span>
                <span className="mt-1 block text-dim">{metric.explain}</span>
                <span className="mt-1.5 block text-dim">
                  this bar: {Math.round(fill * 100)}% of the hardest this input gets
                </span>
              </>
            }
          >
            <span className="flex flex-col items-center gap-1">
              <span className={`relative block border border-line ${tall ? "h-[52px] w-[14px]" : "h-7 w-[7px]"}`}>
                <span className="absolute inset-x-0 bottom-0 bg-text" style={{ height: `${fill * 100}%` }} />
              </span>
              {/* the full word needs ~54px a bar; below sm that overflows a card,
                  so the short letter stands in and the tooltip carries the name */}
              <span className={`leading-none text-dim ${tall ? "text-[10px]" : "text-[9px]"}`}>
                {tall ? (
                  <>
                    <span className="hidden sm:inline">{metric.name.split(" ")[0]}</span>
                    <span className="sm:hidden">{metric.short}</span>
                  </>
                ) : (
                  metric.short
                )}
              </span>
            </span>
          </Tip>
        );
      })}
    </div>
  );
}
