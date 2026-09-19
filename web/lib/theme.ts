"use client";

/**
 * The theme, as one attribute on <html>.
 *
 * Every colour in the app resolves through the design tokens that
 * app/globals.css defines under :root and [data-theme="light"], so flipping
 * this attribute is the whole mechanism -- no component re-renders for colour,
 * and CodeMirror follows because its stylesheet is written in the same
 * variables.
 */

import { useEffect, useState } from "react";
import { THEME_KEY } from "./theme-boot";

export type Theme = "dark" | "light";

export function readTheme(): Theme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  // the browser chrome (address bar, form controls) follows too
  document.querySelector('meta[name="theme-color"]')?.setAttribute(
    "content",
    theme === "light" ? "#FAFAF8" : "#0A0B0D",
  );
}

/** Subscribers that want to know about a change (the editor, mainly). */
const listeners = new Set<(theme: Theme) => void>();

export function setTheme(theme: Theme): void {
  applyTheme(theme);
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    // storage unavailable: the choice just won't survive a reload
  }
  for (const listener of listeners) listener(theme);
}

/** Current theme plus a setter. Reads the attribute the boot script set, so
 * the first client render already agrees with what is on screen. */
export function useTheme(): [Theme, (theme: Theme) => void] {
  // Starts at "dark" to match the server-rendered HTML, then syncs on mount --
  // a static export has no way to know the viewer's choice at build time.
  const [theme, setLocal] = useState<Theme>("dark");

  useEffect(() => {
    setLocal(readTheme());
    const listener = (next: Theme) => setLocal(next);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  return [theme, setTheme];
}
