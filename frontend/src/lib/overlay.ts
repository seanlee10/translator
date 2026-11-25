import type { Phrase, Status } from "../hooks/useCaptionSocket";
import type { DirectionId } from "../directions";

export const OVERLAY_CHANNEL = "live-caption-translator-overlay";
export const OVERLAY_PHRASE_LIMIT = 2;

export interface OverlaySnapshot {
  type: "snapshot";
  phrases: Phrase[];
  status: Status;
  fontSize: number;
  // The direction the SESSION that produced `phrases` actually started
  // with — not any pending UI selection. Travels atomically with the
  // phrases it describes (same message, same BroadcastChannel/IPC send),
  // so the overlay can never render one session's phrases against a
  // different session's direction.
  direction: DirectionId;
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
  directionId: DirectionId,
): OverlaySnapshot {
  return {
    type: "snapshot",
    phrases: selectOverlayPhrases(phrases),
    status,
    fontSize,
    direction: directionId,
  };
}
