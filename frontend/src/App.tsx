import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import { Controls } from "./components/Controls";
import { OverlayView } from "./components/OverlayView";
import { TranscriptView } from "./components/TranscriptView";
import { useAudioCapture } from "./hooks/useAudioCapture";
import { useCaptionSocket } from "./hooks/useCaptionSocket";
import { useProviders } from "./hooks/useProviders";
import {
  createOverlaySnapshot,
  OVERLAY_CHANNEL,
  type OverlayMessage,
  type OverlaySnapshot,
} from "./lib/overlay";
import { pickInitialProvider } from "./providers";
import {
  nextTheme,
  normalizeTheme,
  THEME_STORAGE_KEY,
  type Theme,
} from "./lib/theme";

const WS_URL = "ws://localhost:8000/ws/session";
const IS_OVERLAY = new URLSearchParams(window.location.search).has("overlay");

export default function App() {
  return IS_OVERLAY ? <OverlayView /> : <ControlCenter />;
}

function ControlCenter() {
  const [theme, setTheme] = useState<Theme>(() =>
    normalizeTheme(localStorage.getItem(THEME_STORAGE_KEY)),
  );
  const [fontSize, setFontSize] = useState(22);
  const [finalsOnly, setFinalsOnly] = useState(false);
  // Starts unset: the real choices come from the backend's GET /providers,
  // fetched below, and only ever a provider it actually offers.
  const [provider, setProvider] = useState<string | null>(null);

  const { providers, loading: providersLoading, error: providersError } =
    useProviders();
  const socket = useCaptionSocket(WS_URL);
  const audio = useAudioCapture();
  const sessionStartedRef = useRef(false);
  const finalsOnlyRef = useRef(finalsOnly);
  const providerRef = useRef(provider);
  const overlayChannelRef = useRef<BroadcastChannel | null>(null);
  const snapshotRef = useRef<OverlaySnapshot>(
    createOverlaySnapshot([], "idle", fontSize),
  );

  snapshotRef.current = createOverlaySnapshot(
    socket.phrases,
    socket.status,
    fontSize,
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    finalsOnlyRef.current = finalsOnly;
  }, [finalsOnly]);
  useEffect(() => {
    providerRef.current = provider;
  }, [provider]);

  // Selects the initial model once the server's list arrives. Only fires
  // while nothing has been picked yet, so it never overrides a user's
  // in-session choice if the list happens to refresh.
  useEffect(() => {
    if (provider === null && providers.length > 0) {
      setProvider(pickInitialProvider(providers));
    }
  }, [providers, provider]);

  useEffect(() => {
    const channel = new BroadcastChannel(OVERLAY_CHANNEL);
    overlayChannelRef.current = channel;
    channel.onmessage = (event: MessageEvent<OverlayMessage>) => {
      if (event.data.type === "ready") channel.postMessage(snapshotRef.current);
    };
    channel.postMessage(snapshotRef.current);

    return () => {
      channel.close();
      overlayChannelRef.current = null;
    };
  }, []);

  useEffect(() => {
    overlayChannelRef.current?.postMessage(snapshotRef.current);
    window.translatorDesktop?.publishOverlaySnapshot(snapshotRef.current);
  }, [socket.phrases, socket.status, fontSize]);

  const fatalHandledRef = useRef(false);
  useEffect(() => {
    if (!socket.fatal) {
      fatalHandledRef.current = false;
      return;
    }
    if (fatalHandledRef.current) return;
    fatalHandledRef.current = true;
    audio.stop();
    socket.disconnect();
    sessionStartedRef.current = false;
  }, [socket, audio]);

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
        socket.sendConfig({
          translateInterims: !finalsOnlyRef.current,
          // Omitted entirely when no provider is selected (e.g. the server
          // has none configured) rather than sending null/undefined —
          // 'config' expects a real provider id when the key is present.
          ...(providerRef.current ? { provider: providerRef.current } : {}),
        });
        started = true;
        sessionStartedRef.current = true;
      }
      socket.sendAudio(pcm);
    });
  }, [audio, socket]);

  const changeFinalsOnly = (value: boolean) => {
    setFinalsOnly(value);
    if (sessionStartedRef.current) {
      socket.sendConfig({ translateInterims: !value });
    }
  };

  const changeProvider = (value: string) => {
    setProvider(value);
    if (sessionStartedRef.current) {
      socket.sendConfig({ provider: value });
    }
  };

  const openOverlay = async () => {
    if (window.translatorDesktop) {
      await window.translatorDesktop.openOverlay();
      return;
    }

    window.open(
      `${window.location.pathname}?overlay=1`,
      "translator-presentation-overlay",
      "popup=yes,width=1280,height=360,left=80,top=620",
    );
  };

  const visibleError = audio.error ?? socket.error;

  return (
    <main className="app-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div>
            <p className="eyebrow">LIVE INTERPRETATION STUDIO</p>
            <h1>English <span>→</span> 한국어</h1>
          </div>
        </div>

        <div className="header-actions">
          <button
            className="ghost-button theme-button"
            onClick={() => setTheme((current) => nextTheme(current))}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            aria-pressed={theme === "light"}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            <span className={`theme-icon ${theme}`} aria-hidden="true" />
          </button>
          <div className={`status-pill ${socket.status}`}>
            <span className="status-dot" />
            {socket.status === "idle" ? "Ready" : socket.status}
          </div>
          <button className="ghost-button overlay-button" onClick={openOverlay}>
            <span className="screen-icon" aria-hidden="true" />
            Open overlay
          </button>
        </div>
      </header>

      {visibleError && (
        <div className="error-banner" role="alert">
          <span>!</span>
          <p>{visibleError}</p>
        </div>
      )}

      <div className="workspace">
        <TranscriptView
          phrases={socket.phrases}
          fontSize={fontSize}
          providers={providers}
        />
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
          providers={providers}
          providersLoading={providersLoading}
          providersError={providersError}
          phrases={socket.phrases}
          onOpenOverlay={openOverlay}
        />
      </div>
    </main>
  );
}
