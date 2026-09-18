"use client";

/**
 * The sign-in control in the site header, and the prompt the solve screen
 * shows in place of a submit button.
 *
 * Signing in changes exactly two things: submissions are attributed to you,
 * and your solved challenges follow you between devices. Browsing, reading a
 * traceback and editing all work signed out, so nothing here blocks a page.
 */

import { LogOut } from "lucide-react";
import { useEffect, useState } from "react";
import { apiConfigured } from "@/lib/api";
import { consumeAuthParams, useSession } from "@/lib/session";

/**
 * The GitHub mark, inline. lucide-react dropped its brand icons, and this is
 * the one place in the app where a recognisable logo does real work: "sign in"
 * next to an unfamiliar glyph is a worse button.
 */
function GithubMark({ size = 14 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

export function SignIn() {
  const { user, loading, signIn, signOut } = useSession();
  const [error, setError] = useState<string | null>(null);

  // The callback lands back here with ?signed_in=1 or ?auth_error=... . Strip
  // them from the URL so a reload does not replay the banner.
  useEffect(() => setError(consumeAuthParams().error), []);

  if (!apiConfigured) return null;

  // Hold the space rather than flashing "sign in" before the answer arrives.
  if (loading) return <span className="h-6 w-6" aria-hidden />;

  if (!user) {
    return (
      <div className="flex items-center gap-3">
        {error && (
          <span className="hidden text-[11px] text-error sm:inline" role="alert">
            {error}
          </span>
        )}
        <button
          type="button"
          onClick={signIn}
          className="flex items-center gap-1.5 border border-line px-2 py-0.5 text-dim outline-none transition-colors duration-[120ms] hover:border-text hover:text-text focus-visible:border-text focus-visible:text-text"
        >
          <GithubMark />
          sign in
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      {user.avatar_url ? (
        // A plain <img>: avatar hosts are arbitrary, and next/image would need
        // every GitHub CDN host allowlisted to render a 20px square.
        <img src={user.avatar_url} alt="" width={20} height={20} className="border border-line" />
      ) : null}
      <span className="hidden text-text sm:inline">{user.login}</span>
      <button
        type="button"
        onClick={signOut}
        title="sign out"
        aria-label="sign out"
        className="flex h-6 w-6 items-center justify-center text-dim outline-none transition-colors duration-[120ms] hover:text-text focus-visible:text-text"
      >
        <LogOut size={14} strokeWidth={1.5} />
      </button>
    </div>
  );
}

/**
 * Shown on the solve screen when submitting would 401. Deliberately explicit
 * about why an account is needed at all -- "sign in to continue" with no
 * reason reads as a growth tactic.
 */
export function SignInToSubmit({ className = "" }: { className?: string }) {
  const { signIn } = useSession();
  return (
    <div className={`border border-line bg-panel p-3 ${className}`}>
      <p className="text-dim">
        Grading runs the repo&apos;s suite against your patch and records the solve. Sign in with
        GitHub so it is yours — your progress then follows you between devices.
      </p>
      <button
        type="button"
        onClick={signIn}
        className="mt-2 flex items-center gap-1.5 border border-line px-2 py-1 text-text outline-none transition-colors duration-[120ms] hover:border-text focus-visible:border-text"
      >
        <GithubMark />
        sign in with github
      </button>
    </div>
  );
}
