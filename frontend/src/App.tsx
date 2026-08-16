import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import { Controls } from "./components/Controls";
import { TranscriptView } from "./components/TranscriptView";
import { useAudioCapture } from "./hooks/useAudioCapture";
import { useCaptionSocket, type Provider } from "./hooks/useCaptionSocket";

const WS_URL = "ws://localhost:8000/ws/session";

export default function App() {
  const [fontSize, setFontSize] = useState(22);
  const [finalsOnly, setFinalsOnly] = useState(false);
  const [provider, setProvider] = useState<Provider>("anthropic");

  const socket = useCaptionSocket(WS_URL);
  const audio = useAudioCapture();

  // Whether `start` has actually been sent for the current session. Config
  // changes made before this is true (e.g. while the mic permission prompt
  // is still up) must not be sent to the server — it has no session to
  // configure yet and rejects them. Set true at the same point sendStart()
  // fires, reset on stop.
  const sessionStartedRef = useRef(false);

  // Mirrors of the latest UI selections. The `onChunk` callback below is
  // created once per `audio.start()` call (its closure is fixed at that
  // moment), but the user can change these controls *after* Start is
  // clicked and *before* the first audio chunk arrives (e.g. while a real
  // permission dialog is up) — reading refs instead of the closed-over
  // state values ensures the initial config reflects whatever was selected
  // at the moment the session truly begins, not at the moment Start was
  // clicked.
  const finalsOnlyRef = useRef(finalsOnly);
  const providerRef = useRef(provider);
  useEffect(() => {
    finalsOnlyRef.current = finalsOnly;
  }, [finalsOnly]);
  useEffect(() => {
    providerRef.current = provider;
  }, [provider]);

  const toggle = useCallback(async () => {
    if (audio.active) {
      audio.stop();
      socket.disconnect();
      sessionStartedRef.current = false;
      return;
    }

    socket.connect();
    let started = false;
    await audio.start((pcm, sampleRate) => {
      if (!started) {
        socket.sendStart(sampleRate);
        // Apply current UI selections to the fresh session — read from
        // refs so a change made during the permission-prompt gap is what
        // actually gets sent, not a stale closure over the values at the
        // moment Start was clicked.
        socket.sendConfig({
          translateInterims: !finalsOnlyRef.current,
          provider: providerRef.current,
        });
        started = true;
        sessionStartedRef.current = true;
      }
      socket.sendAudio(pcm);
    });
  }, [audio, socket]);

  const changeFinalsOnly = (value: boolean) => {
    setFinalsOnly(value);
    // Before the session has actually started, there is nothing to
    // configure server-side yet (the backend rejects config before
    // start) — the state update above is enough; the initial sendConfig
    // in the onChunk callback will pick up finalsOnlyRef's latest value.
    if (sessionStartedRef.current) {
      socket.sendConfig({ translateInterims: !value });
    }
  };

  const changeProvider = (value: Provider) => {
    setProvider(value);
    if (sessionStartedRef.current) {
      socket.sendConfig({ provider: value });
    }
  };

  return (
    <div className="app">
      <h1>Live Caption Translator — English → 한국어</h1>

      <Controls
        active={audio.active}
        status={socket.status}
        devices={audio.devices}
        deviceId={audio.deviceId}
        onDeviceChange={audio.setDeviceId}
        onToggle={toggle}
        fontSize={fontSize}
        onFontSize={setFontSize}
        finalsOnly={finalsOnly}
        onFinalsOnly={changeFinalsOnly}
        provider={provider}
        onProvider={changeProvider}
        phrases={socket.phrases}
      />

      {(audio.error || socket.error) && (
        <p className="error">{audio.error ?? socket.error}</p>
      )}

      <TranscriptView phrases={socket.phrases} fontSize={fontSize} />
    </div>
  );
}
