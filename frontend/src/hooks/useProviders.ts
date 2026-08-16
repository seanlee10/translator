import { useCallback, useEffect, useRef, useState } from "react";
import type { ProviderInfo } from "../providers";

const PROVIDERS_URL = "http://localhost:8000/providers";

/** Fetches the backend's advertised translation providers on mount, with a
 * manual `refetch` for callers to retry later. The backend is the single
 * source of truth (see backend/app/providers/__init__.py) and only lists
 * providers whose API key is actually configured, so an empty result is a
 * valid, expected state — not an error.
 *
 * There is no automatic retry/poll: if the frontend starts before the
 * backend, the initial fetch fails and the UI would otherwise stay stuck on
 * the error message until a manual page reload, even after the backend
 * comes up. `refetch` exists so a caller can retry at a natural trigger —
 * e.g. when the user starts a session — instead of requiring that reload. */
export function useProviders() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Guards against a stale in-flight request (mount fetch or a prior
  // refetch) clobbering state after a newer one has already resolved.
  const requestIdRef = useRef(0);

  const refetch = useCallback(() => {
    const requestId = ++requestIdRef.current;
    setLoading(true);

    fetch(PROVIDERS_URL)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`GET /providers failed: ${response.status}`);
        }
        return response.json() as Promise<ProviderInfo[]>;
      })
      .then((data) => {
        if (requestId !== requestIdRef.current) return;
        setProviders(data);
        setError(null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (requestId !== requestIdRef.current) return;
        setError(
          err instanceof Error
            ? err.message
            : "Could not reach the caption server to load translation models.",
        );
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { providers, loading, error, refetch };
}
