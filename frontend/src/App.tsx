import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import { Controls } from "./components/Controls";
import { OverlayView } from "./components/OverlayView";
import { TranscriptView } from "./components/TranscriptView";
import {
  DEFAULT_DIRECTION,
  directionById,
  displayDirection,
  type DirectionId,
} from "./directions";
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
  // Starts unset: the real choices come from the backend's GET /providers,
  // fetched below, and only ever a provider it actually offers.
  const [provider, setProvider] = useState<string | null>(null);
  // The PENDING UI selection, not necessarily what the current session
  // actually started with — see socket.direction (from useCaptionSocket)
  // for that. Passed to Controls; socket.direction is what's passed to the
  // two views so existing captions never re-render under a direction that
  // wasn't theirs.
  const [direction, setDirection] = useState<DirectionId>(DEFAULT_DIRECTION.id);

  const {
    providers,
    loading: providersLoading,
    error: providersError,
    refetch: refetchProviders,
  } = useProviders();
  const socket = useCaptionSocket(WS_URL);
  const audio = useAudioCapture();
  const sessionStartedRef = useRef(false);
  const providerRef = useRef(provider);
  const directionRef = useRef(direction);
  const overlayChannelRef = useRef<BroadcastChannel | null>(null);
  const snapshotRef = useRef<OverlaySnapshot>(
    createOverlaySnapshot([], "idle", fontSize, DEFAULT_DIRECTION.id),
  );

  // socket.direction is the direction the RUNNING SESSION actually started
  // with, not the pending toggle — same distinction as TranscriptView.
  snapshotRef.current = createOverlaySnapshot(
    socket.phrases,
    socket.status,
    fontSize,
    socket.direction.id,
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    providerRef.current = provider;
  }, [provider]);

  useEffect(() => {
    directionRef.current = direction;
  }, [direction]);

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

    // The initial GET /providers fetch has no automatic retry (see
    // useProviders), so if the frontend started before the backend came up,
    // the model list would otherwise stay stuck on its error message
    // forever. Starting a session is a natural point to retry it — by now
    // the backend is likely up even if it wasn't at page load.
    if (providersError) refetchProviders();

    socket.connect();
    let started = false;
    await audio.start((pcm, sampleRate) => {
      if (!started) {
        socket.sendStart(sampleRate, directionRef.current);
        // Only sent when a provider is actually selected (e.g. the server
        // has none configured otherwise) — 'config' expects a real
        // provider id when the field is present at all.
        if (providerRef.current) {
          socket.sendConfig({ provider: providerRef.current });
        }
        started = true;
        sessionStartedRef.current = true;
      }
      socket.sendAudio(pcm);
    });
  }, [audio, socket, providersError, refetchProviders]);

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

  // No phrases on screen yet: nothing to mislabel, so show the PENDING
  // selection (otherwise the empty state tells the user to speak the wrong
  // language). Once phrases exist, they must stay read under the direction
  // their session actually ran in. Safe because the toggle is disabled
  // while a session is active — pending and session direction can only
  // diverge while stopped. See directions.ts's displayDirection.
  const shownDirection = displayDirection(
    socket.phrases.length,
    directionById(direction),
    socket.direction,
  );

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
            <h1>
              {shownDirection.sourceLabel} <span>→</span>{" "}
              {shownDirection.targetLabel}
            </h1>
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
          direction={shownDirection}
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
          provider={provider}
          onProvider={changeProvider}
          providers={providers}
          providersLoading={providersLoading}
          providersError={providersError}
          phrases={socket.phrases}
          onOpenOverlay={openOverlay}
          direction={direction}
          onDirection={setDirection}
          exportDirection={shownDirection}
        />
      </div>
    </main>
  );
}
