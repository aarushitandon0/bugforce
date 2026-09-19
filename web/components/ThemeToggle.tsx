"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme";

/** Sun/moon, top-right of every screen. */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const [theme, set] = useTheme();
  const next = theme === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      onClick={() => set(next)}
      title={`switch to ${next} theme`}
      aria-label={`switch to ${next} theme`}
      className={`flex h-6 w-6 items-center justify-center text-muted outline-none transition-colors duration-[120ms] hover:text-text focus-visible:text-text ${className}`}
    >
      {theme === "dark" ? <Sun size={16} strokeWidth={1.5} /> : <Moon size={16} strokeWidth={1.5} />}
    </button>
  );
}
