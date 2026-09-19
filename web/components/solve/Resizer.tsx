"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * A 1px drag handle between two panels.
 *
 * The hit area is 5px wide and overhangs the border, so the hairline stays a
 * hairline while still being grabbable. Arrow keys move it too, in 16px steps,
 * because a drag-only control is unreachable from the keyboard.
 */
export function Resizer({
  width,
  onResize,
  side,
  min,
  max,
  label,
}: {
  width: number;
  onResize: (width: number) => void;
  /** which side of the handle the panel being sized is on */
  side: "left" | "right";
  min: number;
  max: number;
  label: string;
}) {
  const dragging = useRef<{ x: number; from: number } | null>(null);
  const clamp = useCallback((value: number) => Math.max(min, Math.min(max, value)), [min, max]);

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const drag = dragging.current;
      if (!drag) return;
      const delta = (e.clientX - drag.x) * (side === "left" ? 1 : -1);
      onResize(clamp(drag.from + delta));
    };
    const onUp = () => {
      if (!dragging.current) return;
      dragging.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [onResize, side, clamp]);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(width)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onPointerDown={(e) => {
        dragging.current = { x: e.clientX, from: width };
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
      }}
      onDoubleClick={() => onResize(clamp(side === "left" ? 260 : 300))}
      onKeyDown={(e) => {
        const step = e.key === "ArrowLeft" ? -16 : e.key === "ArrowRight" ? 16 : 0;
        if (step === 0) return;
        e.preventDefault();
        onResize(clamp(width + step * (side === "left" ? 1 : -1)));
      }}
      className="group relative z-10 -mx-[2px] hidden w-[5px] shrink-0 cursor-col-resize touch-none outline-none lg:block"
    >
      <span className="absolute inset-y-0 left-[2px] w-px bg-line-strong transition-colors duration-[120ms] group-hover:bg-frame group-focus-visible:bg-frame" />
    </div>
  );
}
