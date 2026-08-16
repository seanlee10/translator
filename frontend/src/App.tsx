import { useCallback, useState } from "react";
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

  const toggle = useCallback(async () => {
    if (audio.active) {
      audio.stop();
      socket.disconnect();
      return;
    }

    socket.connect();
    let started = false;
    await audio.start((pcm, sampleRate) => {
      if (!started) {
        socket.sendStart(sampleRate);
        // Apply current UI selections to the fresh session.
        socket.sendConfig({ translateInterims: !finalsOnly, provider });
        started = true;
      }
      socket.sendAudio(pcm);
    });
  }, [audio, socket, finalsOnly, provider]);

  const changeFinalsOnly = (value: boolean) => {
    setFinalsOnly(value);
    socket.sendConfig({ translateInterims: !value });
  };

  const changeProvider = (value: Provider) => {
    setProvider(value);
    socket.sendConfig({ provider: value });
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
