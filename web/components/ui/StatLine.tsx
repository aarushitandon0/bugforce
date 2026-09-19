import type { ReactNode } from "react";

export interface Stat {
  value: ReactNode;
  label: string;
  /** colour the value, e.g. gaps in --term-gap. The label stays muted. */
  tone?: string;
}

/**
 * The `X · Y · Z` metadata row, used on the landing, repos and course pages.
 *
 * All three had their own version with different sizes, separators and
 * spacing. It is a description list rather than a paragraph so a screen reader
 * gets the pairing, and the middots are decorative.
 */
export function StatLine({ stats, className = "" }: { stats: Stat[]; className?: string }) {
  return (
    <dl className={`flex flex-wrap items-baseline gap-x-2 gap-y-1 t-small text-muted tabular-nums ${className}`}>
      {stats.map((stat, i) => (
        <span key={stat.label} className="flex items-baseline gap-2">
          {i > 0 && (
            <span aria-hidden className="text-faint">
              &middot;
            </span>
          )}
          <dt className="sr-only">{stat.label}</dt>
          <dd className="flex items-baseline gap-2">
            <span className={stat.tone ?? "text-text"}>{stat.value}</span>
            <span>{stat.label}</span>
          </dd>
        </span>
      ))}
    </dl>
  );
}
