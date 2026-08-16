import { useEffect, useState } from "react";
import type { OverlayMessage, OverlaySnapshot } from "../lib/overlay";
import { OVERLAY_CHANNEL } from "../lib/overlay";

const EMPTY_SNAPSHOT: OverlaySnapshot = {
  type: "snapshot",
  phrases: [],
  status: "idle",
  fontSize: 28,
};

export function OverlayView() {
  const [snapshot, setSnapshot] = useState(EMPTY_SNAPSHOT);

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
                className="overlay-en"
                style={{ fontSize: `${Math.max(20, snapshot.fontSize * 0.7)}px` }}
              >
                {phrase.en}
              </p>
              <p
                className="overlay-ko"
                style={{ fontSize: `${Math.max(32, snapshot.fontSize * 1.45)}px` }}
              >
                {phrase.ko ?? "번역 중…"}
              </p>
            </article>
          ))
        )}
      </section>
    </main>
  );
}
