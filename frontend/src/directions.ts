import type { Phrase } from "./hooks/useCaptionSocket";

export type DirectionId = "en>ko" | "ko>en";

export interface DirectionInfo {
  id: DirectionId;
  /** Shown on the direction toggle. */
  label: string;
  sourceLang: "en" | "ko";
  targetLang: "en" | "ko";
  /** Column and legend copy. */
  sourceLabel: string;
  targetLabel: string;
  /** Overlay placeholder, written in the language being produced. */
  translatingPlaceholder: string;
}

/**
 * A deliberate mirror of the backend's DIRECTIONS registry, not a fetched
 * endpoint.
 *
 * `useProviders` fetches GET /providers because WHICH providers exist varies
 * at runtime with which API keys are configured. Directions have no such
 * variance — there are exactly two, always — so an endpoint would imitate a
 * pattern whose justification does not apply. The accepted cost is that the
 * set is declared in two places; this file is the second one.
 */
export const DIRECTIONS: DirectionInfo[] = [
  {
    id: "en>ko",
    label: "English → 한국어",
    sourceLang: "en",
    targetLang: "ko",
    sourceLabel: "English",
    targetLabel: "한국어",
    translatingPlaceholder: "번역 중…",
  },
  {
    id: "ko>en",
    label: "한국어 → English",
    sourceLang: "ko",
    targetLang: "en",
    sourceLabel: "한국어",
    targetLabel: "English",
    translatingPlaceholder: "Translating…",
  },
];

export const DEFAULT_DIRECTION = DIRECTIONS[0];

/** Never throws: an unknown id must not blank the UI. */
export function directionById(id: string): DirectionInfo {
  return DIRECTIONS.find((d) => d.id === id) ?? DEFAULT_DIRECTION;
}

/**
 * The spoken side of a phrase. The wire's `en`/`ko` are LANGUAGE slots, so
 * role is derived here — the frontend mirror of Direction.to_slots, and the
 * only place the frontend decides it.
 */
export function sourceTextOf(
  phrase: Pick<Phrase, "en" | "ko">,
  direction: DirectionInfo,
): string | null {
  return direction.sourceLang === "en" ? phrase.en : phrase.ko;
}

/** The produced side of a phrase, or null before it arrives. */
export function targetTextOf(
  phrase: Pick<Phrase, "en" | "ko">,
  direction: DirectionInfo,
): string | null {
  return direction.targetLang === "en" ? phrase.en : phrase.ko;
}

/**
 * Which direction the transcript header and empty state should describe.
 *
 * With no phrases on screen there is nothing to mislabel, so show the
 * PENDING selection — otherwise the empty state tells the user to speak the
 * wrong language. Once phrases exist they must be read under the direction
 * their session actually ran in, or every column swaps.
 *
 * Safe because the direction toggle is disabled while a session is active:
 * pending and session direction can only diverge while stopped.
 */
export function displayDirection(
  phraseCount: number,
  pending: DirectionInfo,
  session: DirectionInfo,
): DirectionInfo {
  return phraseCount === 0 ? pending : session;
}
