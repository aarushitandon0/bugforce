/**
 * The pre-paint theme script. Kept out of lib/theme.ts (which is a client
 * module) so the server-rendered <head> can import it directly.
 */

export const THEME_KEY = "bugforge:theme";

/** Runs before first paint: stored choice, else prefers-color-scheme. Without
 * it the page paints dark and then flips for a light viewer. */
export const THEME_BOOT = `(function(){try{var t=localStorage.getItem(${JSON.stringify(THEME_KEY)});if(t!=="light"&&t!=="dark")t=window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";document.documentElement.setAttribute("data-theme",t);}catch(e){document.documentElement.setAttribute("data-theme","dark");}})();`;
