import { useEffect, useRef, useState } from "react";
import type { Phrase } from "../hooks/useCaptionSocket";

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: "Haiku 4.5",
  groq: "Qwen3.6",
};

interface Props {
  phrases: Phrase[];
  fontSize: number;
}

export function TranscriptView({ phrases, fontSize }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

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
    <div className="transcript-wrapper">
      <div
        className="transcript"
        ref={scrollRef}
        onScroll={onScroll}
        style={{ fontSize: `${fontSize}px` }}
      >
        {phrases.map((phrase) => (
          <div className="row" key={phrase.phraseId}>
            <div className="col en">{phrase.en}</div>
            <div className="col ko-cell">
              <span
                className={phrase.status !== "final" ? "ko provisional" : "ko"}
              >
                {phrase.ko ?? " "}
              </span>
              {phrase.provider && (
                <span className="provenance">
                  {PROVIDER_LABEL[phrase.provider] ?? phrase.provider}
                  {phrase.latencyMs !== null && ` · ${phrase.latencyMs}ms`}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
      {!pinned && (
        <button className="jump" onClick={() => setPinned(true)}>
          Jump to live ↓
        </button>
      )}
    </div>
  );
}
