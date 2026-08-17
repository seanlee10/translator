import { useCallback, useRef, useState } from "react";

// The provider set is now discovered from the backend (GET /providers) and
// changes with which API keys are configured server-side, so it's a plain
// string here rather than a hardcoded union — see src/providers.ts for the
// types describing the fetched list itself.
export type Provider = string;
// "error" is a terminal status distinct from "final": it means the phrase's
// English arrived (via a transcript event) but its translation failed, so
// there is no Korean to show — see phraseDisplay.ts for what "terminal"
// means and why it must include this case, not just "final".
export type PhraseStatus = "interim" | "provisional" | "final" | "error";

export interface Phrase {
  phraseId: number;
  en: string;
  ko: string | null;
  status: PhraseStatus;
  provider: Provider | null;
  latencyMs: number | null;
  // Set only when status is "error" — the message from the server's
  // phrase-scoped ErrorOut, shown as the row's error marker. Optional (not
  // just nullable) so existing Phrase literals elsewhere that predate this
  // field keep type-checking unchanged; treat a missing field the same as
  // null.
  errorMessage?: string | null;
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
// "error" ranks alongside "final": both are terminal outcomes of a phrase's
// single translation attempt and are mutually exclusive on the wire (the
// finals worker emits exactly one of CaptionOut(final) or a phrase-scoped
// ErrorOut per phrase), so they never need to be ranked against each other.
const STATUS_RANK: Record<PhraseStatus, number> = {
  interim: 0,
  provisional: 1,
  final: 2,
  error: 2,
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

      // Only an "interim" update (English-only, from a transcript event)
      // preserves whatever Korean/provider/latency is already there — it
      // never carries its own. Every other status (provisional, final,
      // error) is a real update and must replace those fields outright,
      // including replacing them with null — otherwise a phrase that
      // reaches "error" after an earlier provisional could keep showing
      // that provisional's stale Korean next to its error marker.
      copy[index] =
        next.status === "interim"
          ? {
              ...existing,
              en: next.en,
              ko: next.ko ?? existing.ko,
              provider: next.provider ?? existing.provider,
              latencyMs: next.latencyMs ?? existing.latencyMs,
            }
          : { ...existing, ...next };
      return copy;
    });
  }, []);

  // Merges a phrase-scoped translation failure into whatever's already
  // known about this phrase (its English, from the transcript event the
  // pipeline publishes before every final translation attempt) without
  // requiring a full Phrase — the wire's ErrorOut carries no English at
  // all, so building one here would mean fabricating or discarding text.
  // This is the path that keeps a failed translation's English on screen
  // instead of the phrase silently vanishing.
  const markPhraseError = useCallback((phraseId: number, message: string) => {
    setPhrases((current) => {
      const index = current.findIndex((p) => p.phraseId === phraseId);
      if (index === -1) {
        // Defensive only: the pipeline always publishes a transcript event
        // for a phrase before it can fail translation, so this phrase
        // should already exist. Still surface the error rather than drop
        // it silently if that invariant is ever violated.
        const errored: Phrase = {
          phraseId,
          en: "",
          ko: null,
          status: "error",
          provider: null,
          latencyMs: null,
          errorMessage: message,
        };
        return [...current, errored].sort((a, b) => a.phraseId - b.phraseId);
      }
      const copy = [...current];
      copy[index] = {
        ...copy[index],
        ko: null,
        provider: null,
        latencyMs: null,
        status: "error",
        errorMessage: message,
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
          // Interim text still arrives and still updates `en` (including
          // the final's own transcript event, which is what keeps the
          // English on screen even when translation later fails) — but per
          // phraseDisplay.ts, an "interim" status is never terminal, so
          // TranscriptView never renders it as changing text. It only
          // drives the loader's presence.
          upsert({
            phraseId: message.phrase_id,
            en: message.en,
            ko: null,
            status: "interim",
            provider: null,
            latencyMs: null,
            errorMessage: null,
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
            errorMessage: null,
          });
          break;
        case "error":
          setError(message.message);
          if (message.fatal) setFatal(true);
          // A phrase-scoped error is a failed final translation: the
          // phrase is terminal (it will never get another translation
          // attempt), so mark it rather than leaving it stuck "interim"
          // forever, which would leave its row invisible under the new
          // terminal-only reveal and silently lose the English — the exact
          // bug this row-level marker exists to prevent.
          if (message.phrase_id !== null) {
            markPhraseError(message.phrase_id, message.message);
          }
          break;
      }
    };

    ws.onerror = () => setError("Connection to the caption server failed.");
    ws.onclose = () => setStatus("closed");
  }, [url, upsert, markPhraseError]);

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
    // translate_interims is no longer sent from here — the "Finals only"
    // control that used to drive it is gone (the terminal-only reveal made
    // it a no-op for what the user sees), and TRANSLATE_INTERIMS is now an
    // env-only setting. The backend still accepts translate_interims in a
    // 'config' message (see main.py) — only this frontend plumbing for the
    // removed toggle was cut, not that server-side handling.
    (config: { provider?: Provider }) => {
      const payload: Record<string, unknown> = { type: "config" };
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
