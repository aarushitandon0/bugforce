"use client";

import { useCallback, useEffect, useState } from "react";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Runs `load` on mount and whenever `key` changes; ignores responses that arrive after a newer request. */
export function useApi<T>(load: (() => Promise<T>) | null, key: string): Loaded<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({
    data: null,
    error: null,
    loading: load !== null,
  });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!load) return;
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    load().then(
      (data) => !cancelled && setState({ data, error: null, loading: false }),
      (error: unknown) =>
        !cancelled &&
        setState({ data: null, error: error instanceof Error ? error.message : String(error), loading: false }),
    );
    return () => {
      cancelled = true;
    };
    // `load` is recreated each render; `key` is what identifies the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...state, reload };
}
