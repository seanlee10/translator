// The provider set is now discovered from the backend (GET /providers)
// instead of hardcoded on the frontend, and only ever lists providers whose
// API key is actually configured server-side. See
// backend/app/providers/__init__.py.

export interface ProviderInfo {
  id: string;
  label: string;
  model: string;
}

/** Which provider a fresh session should start on: the first one the
 * server actually offers, or null if none are configured. */
export function pickInitialProvider(providers: ProviderInfo[]): string | null {
  return providers.length > 0 ? providers[0].id : null;
}

/** Maps provider id -> human label, for O(1) lookup while rendering
 * captions that may reference a provider no longer in the current list. */
export function toLabelMap(providers: ProviderInfo[]): Record<string, string> {
  return Object.fromEntries(providers.map((p) => [p.id, p.label]));
}

/** A caption's provider id may not be in the currently fetched list (the
 * server config can change between sessions). Falls back to the raw id
 * rather than rendering blank. */
export function labelFor(providers: ProviderInfo[], id: string | null): string {
  if (!id) return "";
  return toLabelMap(providers)[id] ?? id;
}
