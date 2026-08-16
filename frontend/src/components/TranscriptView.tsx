import { useEffect, useMemo, useRef, useState } from "react";
import type { Phrase } from "../hooks/useCaptionSocket";
import { toLabelMap, type ProviderInfo } from "../providers";

interface Props {
  phrases: Phrase[];
  fontSize: number;
  providers: ProviderInfo[];
}

export function TranscriptView({ phrases, fontSize, providers }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);
  // Labels come from the server now, not a hardcoded map. A caption whose
  // provider id isn't in the current list (e.g. the server's configured
  // keys changed since it was translated) falls back to the raw id below.
  const labelMap = useMemo(() => toLabelMap(providers), [providers]);

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
          <span><i className="legend-dot en-dot" />English</span>
          <span><i className="legend-dot ko-dot" />한국어</span>
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
              <p>Choose a model, start listening, and begin speaking in English.</p>
              <div className="empty-flow">
                <span>EN speech</span><i>→</i><span>Deepgram</span><i>→</i><span>한국어</span>
              </div>
            </div>
          ) : (
            phrases.map((phrase, index) => (
              <article
                className={`transcript-row ${
                  index === phrases.length - 1 ? "is-current" : ""
                }`}
                key={phrase.phraseId}
              >
                <div className="phrase-number">
                  {String(phrase.phraseId + 1).padStart(2, "0")}
                </div>
                <div className="caption-col en">
                  <p>{phrase.en}</p>
                  <span className="language-tag">EN</span>
                </div>
                <div className="caption-col ko-cell">
                  <p className={phrase.status !== "final" ? "ko provisional" : "ko"}>
                    {phrase.ko ?? "번역을 기다리는 중…"}
                  </p>
                  <div className="caption-meta">
                    <span className="language-tag ko-tag">KO</span>
                    {phrase.provider && (
                      <span className="provenance">
                        {labelMap[phrase.provider] ?? phrase.provider}
                        {phrase.latencyMs !== null && ` · ${phrase.latencyMs}ms`}
                      </span>
                    )}
                  </div>
                </div>
              </article>
            ))
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
