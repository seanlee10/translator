import type { Phrase, Status } from "../hooks/useCaptionSocket";

export const OVERLAY_CHANNEL = "live-caption-translator-overlay";
export const OVERLAY_PHRASE_LIMIT = 2;

export interface OverlaySnapshot {
  type: "snapshot";
  phrases: Phrase[];
  status: Status;
  fontSize: number;
}

export type OverlayMessage = OverlaySnapshot | { type: "ready" };

export function selectOverlayPhrases(
  phrases: Phrase[],
  limit = OVERLAY_PHRASE_LIMIT,
): Phrase[] {
  return phrases.slice(-limit);
}

export function createOverlaySnapshot(
  phrases: Phrase[],
  status: Status,
  fontSize: number,
): OverlaySnapshot {
  return {
    type: "snapshot",
    phrases: selectOverlayPhrases(phrases),
    status,
    fontSize,
  };
}
