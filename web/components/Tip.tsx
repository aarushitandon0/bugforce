"use client";

import { useId, useState } from "react";

/**
 * A trigger button with a hairline tooltip. Opens on hover and on keyboard
 * focus, closes on Escape; the bubble is linked with aria-describedby.
 */
export function Tip({
  content,
  label,
  align = "center",
  children,
}: {
  content: React.ReactNode;
  label: string;
  align?: "center" | "end";
  children: React.ReactNode;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const position = align === "end" ? "right-0" : "left-1/2 -translate-x-1/2";

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={label}
        aria-describedby={open ? id : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        className="inline-flex cursor-help"
      >
        {children}
      </button>
      {open && (
        <span
          role="tooltip"
          id={id}
          className={`pointer-events-none absolute bottom-full z-30 mb-2 w-[260px] border border-line bg-surface-2 px-3 py-2.5 text-left text-[11px] leading-[1.55] text-text animate-fade ${position}`}
        >
          {content}
        </span>
      )}
    </span>
  );
}
