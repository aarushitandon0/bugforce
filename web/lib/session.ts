"use client";

/**
 * Who is signed in.
 *
 * The session cookie is HttpOnly, so the browser cannot read it and there is
 * nothing to decode client-side: the only way to know is to ask GET /auth/me.
 * That answer is cached in a module-level promise and shared by every
 * subscriber, so the header, the solve screen and the course page cost one
 * request between them rather than one each.
 *
 * `null` means signed out and `undefined` means "not asked yet" -- the
 * difference matters, because a header that renders "sign in" before the
 * answer arrives flickers on every navigation.
 */

import { useCallback, useEffect, useState } from "react";
import { apiConfigured, getMe, signInUrl, signOut, type User } from "./api";

type Known = User | null;

let cached: Promise<Known> | null = null;
const subscribers = new Set<(user: Known) => void>();

function load(): Promise<Known> {
  if (!cached) {
    cached = apiConfigured
      ? getMe()
          .then((r) => r.user)
          // A stack deployed without the auth secrets, or an API that is
          // simply down, reads as signed out. Sign-in is an enhancement; no
          // page should fail to render because of it.
          .catch(() => null)
      : Promise.resolve(null);
  }
  return cached;
}

function publish(user: Known): void {
  cached = Promise.resolve(user);
  subscribers.forEach((fn) => fn(user));
}

/** Drops the cache so the next read re-asks the API. */
export function invalidateSession(): void {
  cached = null;
  load().then(publish);
}

export interface Session {
  user: Known;
  /** true until the first answer arrives */
  loading: boolean;
  signIn: () => void;
  signOut: () => Promise<void>;
}

export function useSession(): Session {
  const [user, setUser] = useState<Known | undefined>(undefined);

  useEffect(() => {
    let live = true;
    const onChange = (next: Known) => live && setUser(next);
    subscribers.add(onChange);
    load().then(onChange);
    return () => {
      live = false;
      subscribers.delete(onChange);
    };
  }, []);

  const signIn = useCallback(() => {
    // A full-page navigation: the flow redirects through github.com, which no
    // fetch() can follow. Coming back here is what `return_to` is for.
    window.location.href = signInUrl(window.location.pathname + window.location.search);
  }, []);

  const out = useCallback(async () => {
    try {
      await signOut();
    } catch {
      // The cookie may already be gone; either way we are signed out locally.
    }
    publish(null);
  }, []);

  return { user: user ?? null, loading: user === undefined, signIn, signOut: out };
}

/**
 * Strips the `?signed_in=1` / `?auth_error=...` the callback appends, so a
 * reload does not re-show the banner and the URL stays shareable. Returns the
 * error message if there was one.
 */
export function consumeAuthParams(): { signedIn: boolean; error: string | null } {
  if (typeof window === "undefined") return { signedIn: false, error: null };
  const url = new URL(window.location.href);
  const signedIn = url.searchParams.get("signed_in") === "1";
  const error = url.searchParams.get("auth_error");
  if (!signedIn && !error) return { signedIn: false, error: null };
  url.searchParams.delete("signed_in");
  url.searchParams.delete("auth_error");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  return { signedIn, error };
}
