import { useEffect, useState } from "react";
import type { OverlayMessage, OverlaySnapshot } from "../lib/overlay";
import { OVERLAY_CHANNEL } from "../lib/overlay";
import { DEFAULT_DIRECTION, directionById, sourceTextOf, targetTextOf } from "../directions";

const EMPTY_SNAPSHOT: OverlaySnapshot = {
  type: "snapshot",
  phrases: [],
  status: "idle",
  fontSize: 28,
  direction: DEFAULT_DIRECTION.id,
};

// No props: the overlay is a separate window/page load with no access to
// the control window's React state. Direction now travels atomically with
// the phrases it describes, inside the snapshot itself (see
// lib/overlay.ts) — derived below via directionById, which never throws
// and degrades quietly to the default on a malformed value.
export function OverlayView() {
  const [snapshot, setSnapshot] = useState(EMPTY_SNAPSHOT);
  const direction = directionById(snapshot.direction);

  useEffect(() => {
    document.body.classList.add("overlay-document");
    const channel = new BroadcastChannel(OVERLAY_CHANNEL);
    channel.onmessage = (event: MessageEvent<OverlayMessage>) => {
      if (event.data.type === "snapshot") setSnapshot(event.data);
    };
    channel.postMessage({ type: "ready" } satisfies OverlayMessage);
    const stopDesktopListener = window.translatorDesktop?.onOverlaySnapshot(
      (next) => setSnapshot(next),
    );
    window.translatorDesktop?.requestOverlaySnapshot();

    return () => {
      stopDesktopListener?.();
      channel.close();
      document.body.classList.remove("overlay-document");
    };
  }, []);

  const isWaiting = snapshot.phrases.length === 0;

  return (
    <main className="overlay-page" aria-label="Presentation captions">
      <section className={`caption-stack ${isWaiting ? "is-waiting" : ""}`}>
        {isWaiting ? (
          <div className="overlay-ready">
            <span className={`live-dot ${snapshot.status}`} />
            Caption overlay ready
          </div>
        ) : (
          snapshot.phrases.map((phrase, index) => (
            <article
              className={`overlay-caption ${
                index === snapshot.phrases.length - 1 ? "is-current" : ""
              } ${phrase.status !== "final" ? "is-provisional" : ""}`}
              key={phrase.phraseId}
            >
              <p
                className="overlay-source"
                style={{ fontSize: `${Math.max(20, snapshot.fontSize * 0.7)}px` }}
              >
                {sourceTextOf(phrase, direction) ?? ""}
              </p>
              <p
                className="overlay-target"
                style={{ fontSize: `${Math.max(32, snapshot.fontSize * 1.45)}px` }}
              >
                {targetTextOf(phrase, direction) ?? direction.translatingPlaceholder}
              </p>
            </article>
          ))
        )}
      </section>
    </main>
  );
}
