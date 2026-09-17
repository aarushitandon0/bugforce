/** The blinking block cursor. Solid (not hidden) under prefers-reduced-motion. */
export function Cursor({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-[1.1em] w-[0.6em] translate-y-[0.18em] bg-text animate-blink ${className}`}
    />
  );
}
