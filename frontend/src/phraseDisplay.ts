import type { Phrase } from "./hooks/useCaptionSocket";

/**
 * A row becomes visible only once its phrase reaches a terminal state.
 * Until then it's represented by a loader, not by changing text — that's
 * the whole point of this change (see the 2026-08-16 feedback: constant
 * text churn from interim/provisional updates was tiring to read).
 *
 * "Terminal" is deliberately NOT "status === 'final'". A phrase whose
 * translation failed never reaches "final" — the pipeline emits a
 * phrase-scoped error instead (see useCaptionSocket's markPhraseError) —
 * and treating only "final" as visible would mean that phrase's English
 * never appears at all, silently losing it. That exact bug was found and
 * fixed once already in this project (the final's `transcript` event
 * exists precisely so the English survives a failed translation); "final
 * OR error" is what keeps it fixed here.
 */
export function isTerminalPhrase(phrase: Pick<Phrase, "status">): boolean {
  return phrase.status === "final" || phrase.status === "error";
}

/** The subset of phrases that should actually render as transcript rows. */
export function visiblePhrases(phrases: Phrase[]): Phrase[] {
  return phrases.filter(isTerminalPhrase);
}

/**
 * Whether the in-progress-phrase loader belongs on screen: true whenever a
 * phrase exists that hasn't reached a terminal state yet, i.e. speech is
 * still being captured or translated and its row isn't ready to show.
 */
export function hasInFlightPhrase(phrases: Phrase[]): boolean {
  return phrases.some((phrase) => !isTerminalPhrase(phrase));
}
