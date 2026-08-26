import { useEffect, useRef, useState } from "react";
import { sourceTextOf, targetTextOf, type DirectionInfo } from "../directions";
import type { Phrase } from "../hooks/useCaptionSocket";
import { hasInFlightPhrase, visiblePhrases } from "../phraseDisplay";
import { labelFor, type ProviderInfo } from "../providers";

interface Props {
  phrases: Phrase[];
  fontSize: number;
  providers: ProviderInfo[];
  direction: DirectionInfo;
}

export function TranscriptView({ phrases, fontSize, providers, direction }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  // A row only ever renders once its phrase is terminal (final or failed —
  // see phraseDisplay.ts); everything before that is represented by the
  // loader below, never by changing text. `phrases` (unfiltered) still
  // drives both the loader's presence and the auto-scroll effect, so the
  // transcript keeps tracking live even while nothing new has become
  // visible yet.
  const visible = visiblePhrases(phrases);
  const inFlight = hasInFlightPhrase(phrases);

  useEffect(() => {
    if (pinned && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [phrases, pinned]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
  };

  return (
    <section className="transcript-panel">
      <header className="transcript-header">
        <div>
          <p className="panel-kicker">LIVE TRANSCRIPT</p>
          <h2>Conversation</h2>
        </div>
        <div className="language-legend">
          <span>
            <i className={`legend-dot ${direction.sourceLang}-dot`} />
            {direction.sourceLabel}
          </span>
          <span>
            <i className={`legend-dot ${direction.targetLang}-dot`} />
            {direction.targetLabel}
          </span>
        </div>
      </header>

      <div className="column-headings" aria-hidden="true">
        <span>ORIGINAL</span>
        <span>TRANSLATION</span>
      </div>

      <div className="transcript-wrapper">
        <div
          className="transcript"
          ref={scrollRef}
          onScroll={onScroll}
          style={{ fontSize: `${fontSize}px` }}
        >
          {phrases.length === 0 ? (
            <div className="empty-state">
              <div className="sound-orbit" aria-hidden="true">
                <span className="orbit-ring ring-one" />
                <span className="orbit-ring ring-two" />
                <div className="wave-bars">
                  <i /><i /><i /><i /><i />
                </div>
              </div>
              <h3>Your live captions will appear here</h3>
              <p>
                Choose a model, start listening, and begin speaking in{" "}
                {direction.sourceLabel}.
              </p>
              <div className="empty-flow">
                <span>{direction.sourceLabel} speech</span><i>→</i>
                <span>Deepgram</span><i>→</i>
                <span>{direction.targetLabel}</span>
              </div>
            </div>
          ) : (
            <>
              {visible.map((phrase, index) => (
                <article
                  className={`transcript-row ${
                    index === visible.length - 1 ? "is-current" : ""
                  } ${phrase.status === "error" ? "is-error" : ""}`}
                  key={phrase.phraseId}
                >
                  <div className="phrase-number">
                    {String(phrase.phraseId + 1).padStart(2, "0")}
                  </div>
                  <div className="caption-col source">
                    <p>{sourceTextOf(phrase, direction) ?? ""}</p>
                    <span className={`language-tag lang-${direction.sourceLang}`}>
                      {direction.sourceLang.toUpperCase()}
                    </span>
                  </div>
                  <div className="caption-col target">
                    {phrase.status === "error" ? (
                      <>
                        <p className="error-marker">
                          <span className="error-mark" aria-hidden="true">!</span>
                          Translation unavailable
                        </p>
                        <div className="caption-meta">
                          <span className={`language-tag lang-${direction.targetLang}`}>
                            {direction.targetLang.toUpperCase()}
                          </span>
                          <span className="provenance error-text">
                            {phrase.errorMessage ?? "translation failed"}
                          </span>
                        </div>
                      </>
                    ) : (
                      <>
                        <p>{targetTextOf(phrase, direction) ?? ""}</p>
                        <div className="caption-meta">
                          <span className={`language-tag lang-${direction.targetLang}`}>
                            {direction.targetLang.toUpperCase()}
                          </span>
                          {phrase.provider && (
                            <span className="provenance">
                              {labelFor(providers, phrase.provider)}
                              {phrase.latencyMs !== null &&
                                ` · ${phrase.latencyMs}ms`}
                            </span>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </article>
              ))}
              {inFlight && (
                <div className="phrase-loader" role="status" aria-live="polite">
                  <span className="phrase-loader-dots" aria-hidden="true">
                    <i /><i /><i />
                  </span>
                  Listening…
                </div>
              )}
            </>
          )}
        </div>
        {!pinned && (
          <button className="jump-button" onClick={() => setPinned(true)}>
            Jump to live <span>↓</span>
          </button>
        )}
      </div>
    </section>
  );
}
