import { useCallback, useRef, useState } from "react";

export type Provider = "anthropic" | "groq";
export type PhraseStatus = "interim" | "provisional" | "final";

export interface Phrase {
  phraseId: number;
  en: string;
  ko: string | null;
  status: PhraseStatus;
  provider: Provider | null;
  latencyMs: number | null;
}

export type Status =
  | "idle"
  | "connected"
  | "listening"
  | "closed"
  | "reconnecting";

type ServerEvent =
  | { type: "status"; state: Exclude<Status, "idle"> }
  | { type: "transcript"; phrase_id: number; en: string; status: "interim" }
  | {
      type: "caption";
      phrase_id: number;
      en: string;
      ko: string;
      status: "provisional" | "final";
      provider: Provider;
      latency_ms: number;
    }
  | { type: "error"; phrase_id: number | null; message: string; fatal: boolean };

// Rank used to guard against out-of-order server messages: translation
// responses (and, less commonly, transcript refinements) can arrive out of
// sequence, and a stale lower-rank message must never regress a phrase that
// has already reached a higher-rank status (e.g. a late "provisional"
// arriving after "final" must not overwrite the final Korean text).
const STATUS_RANK: Record<PhraseStatus, number> = {
  interim: 0,
  provisional: 1,
  final: 2,
};

export function useCaptionSocket(url: string) {
  const [phrases, setPhrases] = useState<Phrase[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  // Set when the server reports an error it cannot continue past. The
  // session is over at that point, so App tears the capture down; without
  // this the mic kept running and the UI still claimed to be listening.
  const [fatal, setFatal] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  // Messages that must go out before any audio (start, initial config) but
  // were requested while the handshake was still in flight. Queued here and
  // flushed, in order, from onopen — this closes the startup race where
  // sendStart()/sendConfig() would otherwise throw InvalidStateError (or,
  // if silently guarded without queueing, be dropped) when the WebSocket
  // hasn't finished connecting yet.
  const pendingRef = useRef<(() => void)[]>([]);

  const upsert = useCallback((next: Phrase) => {
    setPhrases((current) => {
      const index = current.findIndex((p) => p.phraseId === next.phraseId);
      if (index === -1) {
        return [...current, next].sort((a, b) => a.phraseId - b.phraseId);
      }
      const copy = [...current];
      const existing = copy[index];

      if (STATUS_RANK[next.status] < STATUS_RANK[existing.status]) {
        // A lower-rank message arrived after a higher-rank one for this
        // phrase (out-of-order translation response, or a transcript
        // refinement arriving after translation already started). Never
        // let it regress status or clobber Korean text already shown; at
        // most take the newer English text from a transcript update.
        if (next.status === "interim") {
          copy[index] = { ...existing, en: next.en };
        }
        return copy;
      }

      copy[index] = {
        ...existing,
        ...next,
        // English updates faster than Korean; never blank existing Korean.
        ko: next.ko ?? existing.ko,
        provider: next.provider ?? existing.provider,
        latencyMs: next.latencyMs ?? existing.latencyMs,
      };
      return copy;
    });
  }, []);

  const connect = useCallback(() => {
    const existing = wsRef.current;
    if (existing && existing.readyState !== WebSocket.CLOSED) {
      // Already connecting/open/closing — avoid leaking a socket and its
      // listeners by overwriting the ref out from under it.
      return;
    }

    setError(null);
    setFatal(false);
    // Phrase IDs restart at 0 for every new backend session, so without
    // this the next session's phrase 0 merges into the previous session's
    // phrase 0 — new English shown beside unrelated stale Korean, in solid
    // "final" styling, with the old session's provider and latency.
    setPhrases([]);
    pendingRef.current = [];

    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      const queued = pendingRef.current;
      pendingRef.current = [];
      for (const send of queued) send();
    };

    ws.onmessage = (event) => {
      const message: ServerEvent = JSON.parse(event.data as string);
      switch (message.type) {
        case "status":
          setStatus(message.state);
          break;
        case "transcript":
          upsert({
            phraseId: message.phrase_id,
            en: message.en,
            ko: null,
            status: "interim",
            provider: null,
            latencyMs: null,
          });
          break;
        case "caption":
          upsert({
            phraseId: message.phrase_id,
            en: message.en,
            ko: message.ko,
            status: message.status,
            provider: message.provider,
            latencyMs: message.latency_ms,
          });
          break;
        case "error":
          setError(message.message);
          if (message.fatal) setFatal(true);
          break;
      }
    };

    ws.onerror = () => setError("Connection to the caption server failed.");
    ws.onclose = () => setStatus("closed");
  }, [url, upsert]);

  const sendStart = useCallback((sampleRate: number) => {
    const send = () =>
      wsRef.current?.send(
        JSON.stringify({ type: "start", sample_rate: sampleRate }),
      );
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) send();
    // Only queue while a connection is actually in flight (CONNECTING).
    // With no socket at all — connect() never called, or already
    // disconnected — there is nothing to flush this onto later, so treat
    // it as a no-op rather than letting the queue grow unbounded.
    else if (ws) pendingRef.current.push(send);
  }, []);

  const sendAudio = useCallback((pcm: ArrayBuffer) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(pcm);
  }, []);

  const sendConfig = useCallback(
    (config: { translateInterims?: boolean; provider?: Provider }) => {
      const payload: Record<string, unknown> = { type: "config" };
      if (config.translateInterims !== undefined) {
        payload.translate_interims = config.translateInterims;
      }
      if (config.provider !== undefined) payload.provider = config.provider;
      const send = () => wsRef.current?.send(JSON.stringify(payload));
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) send();
      // See sendStart: only queue while a connection is actually in
      // flight; with no socket at all this is a no-op.
      else if (ws) pendingRef.current.push(send);
    },
    [],
  );

  const disconnect = useCallback(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop" }));
    }
    if (ws) {
      // Detach listeners first: closing is async, and a user-initiated
      // disconnect must not surface as a connection error via a stray
      // onerror/onclose firing after we've already moved on.
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      ws.close();
    }
    wsRef.current = null;
    pendingRef.current = [];
    setStatus("closed");
  }, []);

  return {
    connect,
    disconnect,
    sendAudio,
    sendStart,
    sendConfig,
    phrases,
    status,
    error,
    fatal,
  };
}
