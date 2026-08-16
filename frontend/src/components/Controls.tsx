import type { Phrase, Status } from "../hooks/useCaptionSocket";
import type { ProviderInfo } from "../providers";

interface Props {
  active: boolean;
  status: Status;
  devices: MediaDeviceInfo[];
  deviceId: string | null;
  onDeviceChange: (id: string) => void;
  onToggle: () => void;
  fontSize: number;
  onFontSize: (px: number) => void;
  finalsOnly: boolean;
  onFinalsOnly: (v: boolean) => void;
  provider: string | null;
  onProvider: (p: string) => void;
  providers: ProviderInfo[];
  providersLoading: boolean;
  providersError: string | null;
  phrases: Phrase[];
  onOpenOverlay: () => void;
}

export function Controls(props: Props) {
  const exportTranscript = () => {
    const body = props.phrases
      .map((p) => {
        const tag = p.provider
          ? ` [${p.provider}${p.latencyMs !== null ? ` ${p.latencyMs}ms` : ""}]`
          : "";
        return `EN: ${p.en}\nKO: ${p.ko ?? ""}${tag}\n`;
      })
      .join("\n");
    const url = URL.createObjectURL(new Blob([body], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "transcript.txt";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <aside className="control-panel">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">SESSION</p>
          <h2>Control room</h2>
        </div>
        <span className="command-hint">⌘ ↵</span>
      </div>

      <button
        className={`listen-button ${props.active ? "is-active" : ""}`}
        onClick={props.onToggle}
      >
        <span className="listen-icon" aria-hidden="true">
          {props.active ? <i className="stop-square" /> : <i className="mic-stem" />}
        </span>
        <span>
          <strong>{props.active ? "Stop listening" : "Start listening"}</strong>
          <small>
            {props.active ? "Microphone is live" : "Begin live transcription"}
          </small>
        </span>
        <span className="button-arrow">{props.active ? "■" : "→"}</span>
      </button>

      <section className="control-section">
        <div className="section-label">
          <span>Translation model</span>
          <span className="section-value">
            {props.providers.find((p) => p.id === props.provider)?.label ?? ""}
          </span>
        </div>
        {props.providers.length === 0 ? (
          <p className="model-empty-message">
            {props.providersLoading
              ? "Loading translation models…"
              : props.providersError
                ? "Could not load translation models from the caption server."
                : "No translation model is configured on the server. Transcription will still work, but captions won't be translated."}
          </p>
        ) : (
          <div className="model-toggle" role="group" aria-label="Translation model">
            {props.providers.map((p) => (
              <button
                key={p.id}
                className={props.provider === p.id ? "selected" : ""}
                onClick={() => props.onProvider(p.id)}
              >
                <span className={`model-orb ${p.id}-orb`}>
                  {p.label.charAt(0).toUpperCase()}
                </span>
                <span>
                  <strong>{p.label}</strong>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="control-section">
        <label className="field-label" htmlFor="microphone">
          Microphone source
        </label>
        <div className="select-wrap">
          <span className="field-icon" aria-hidden="true">⌁</span>
          <select
            id="microphone"
            value={props.deviceId ?? ""}
            onChange={(e) => props.onDeviceChange(e.target.value)}
            disabled={props.active}
          >
            <option value="">Default microphone</option>
            {props.devices.map((d) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label || "Microphone"}
              </option>
            ))}
          </select>
        </div>
      </section>

      <section className="control-section">
        <div className="section-label">
          <label htmlFor="caption-size">Caption size</label>
          <span className="section-value">{props.fontSize}px</span>
        </div>
        <div className="range-row">
          <span>A</span>
          <input
            id="caption-size"
            type="range"
            min={16}
            max={40}
            value={props.fontSize}
            onChange={(e) => props.onFontSize(Number(e.target.value))}
          />
          <span className="range-large">A</span>
        </div>
      </section>

      <section className="control-section toggle-row">
        <div>
          <strong>Final captions only</strong>
          <p>Skip provisional translations</p>
        </div>
        <label className="switch">
          <input
            type="checkbox"
            checked={props.finalsOnly}
            onChange={(e) => props.onFinalsOnly(e.target.checked)}
          />
          <span />
        </label>
      </section>

      <div className="panel-spacer" />

      <div className="secondary-actions">
        <button className="secondary-button" onClick={props.onOpenOverlay}>
          <span className="screen-icon" aria-hidden="true" />
          Presentation overlay
        </button>
        <button
          className="secondary-button"
          onClick={exportTranscript}
          disabled={!props.phrases.length}
        >
          <span aria-hidden="true">⇩</span>
          Export transcript
        </button>
      </div>

      <footer className="panel-footer">
        <span className={`footer-light ${props.status}`} />
        Deepgram · {props.status === "listening" ? "streaming audio" : "standing by"}
      </footer>
    </aside>
  );
}
