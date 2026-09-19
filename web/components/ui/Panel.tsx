import type { ReactNode } from "react";

/**
 * A bordered object sitting on the page: header slot, body slot, optional
 * footer slot, one border, one radius, --card-padding on all three.
 *
 * Elevation is the surface step, never a shadow: the page is --surface-1 and a
 * panel is --surface-2, which is what makes it read as a separate object in
 * both themes. The landing terminal, the gap summary card and every solve-rail
 * section are the same component.
 */
export function Panel({
  header,
  footer,
  children,
  className = "",
  bodyClassName = "",
  bodyProps,
  padded = true,
  ...rest
}: {
  header?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  /** the body is a live region / scroller often enough to be worth passing through */
  bodyProps?: React.ComponentProps<"div">;
  /** off when the body is a scroller that has to own its own padding */
  padded?: boolean;
} & Omit<React.ComponentProps<"section">, "children" | "className">) {
  return (
    <section
      {...rest}
      className={`flex min-h-0 flex-col rounded border border-line bg-surface-2 ${className}`}
    >
      {header !== undefined && (
        <div className="flex shrink-0 flex-wrap items-baseline justify-between gap-3 border-b border-line p-card">
          {header}
        </div>
      )}
      <div {...bodyProps} className={`min-h-0 flex-1 ${padded ? "p-card" : ""} ${bodyClassName}`}>
        {children}
      </div>
      {footer !== undefined && <div className="shrink-0 border-t border-line p-card">{footer}</div>}
    </section>
  );
}
