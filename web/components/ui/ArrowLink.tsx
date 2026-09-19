import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

/**
 * Text plus a trailing arrow: `solve →`, `next up →`, `full report →`.
 *
 * The arrow is a real character in the flow rather than an icon, because the
 * whole app is monospace and an SVG arrow sat off the baseline grid. It is
 * aria-hidden: "solve arrow" is not what a screen reader should say.
 */
export function ArrowLink({
  children,
  className = "",
  tone = "text-text",
  ...rest
}: Omit<ComponentProps<typeof Link>, "children"> & { children: ReactNode; tone?: string }) {
  return (
    <Link
      {...rest}
      className={`inline-flex items-center gap-2 t-small ${tone} underline-offset-4 hover:underline ${className}`}
    >
      {children}
      <span aria-hidden>&rarr;</span>
    </Link>
  );
}

/** The same treatment when the target is not a route -- a card's own overlay link. */
export function ArrowText({ children, tone = "text-muted" }: { children: ReactNode; tone?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 t-small ${tone}`}>
      {children}
      <span aria-hidden>&rarr;</span>
    </span>
  );
}
