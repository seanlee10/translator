import { useEffect, useState } from "react";
import type { ProviderInfo } from "../providers";

const PROVIDERS_URL = "http://localhost:8000/providers";

/** Fetches the backend's advertised translation providers on mount. The
 * backend is the single source of truth (see backend/app/providers.py) and
 * only lists providers whose API key is actually configured, so an empty
 * result is a valid, expected state — not an error. */
export function useProviders() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    fetch(PROVIDERS_URL)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`GET /providers failed: ${response.status}`);
        }
        return response.json() as Promise<ProviderInfo[]>;
      })
      .then((data) => {
        if (cancelled) return;
        setProviders(data);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof Error
            ? err.message
            : "Could not reach the caption server to load translation models.",
        );
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return { providers, loading, error };
}
