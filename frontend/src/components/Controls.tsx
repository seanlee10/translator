import type { Phrase, Provider, Status } from "../hooks/useCaptionSocket";

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
  provider: Provider;
  onProvider: (p: Provider) => void;
  phrases: Phrase[];
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
    <div className="controls">
      <button className="primary" onClick={props.onToggle}>
        {props.active ? "Stop" : "Start listening"}
      </button>

      <span className={`status ${props.status}`}>{props.status}</span>

      <label>
        Model
        <select
          value={props.provider}
          onChange={(e) => props.onProvider(e.target.value as Provider)}
        >
          <option value="anthropic">Claude Haiku 4.5</option>
          <option value="groq">Qwen3.6-27B (Groq)</option>
        </select>
      </label>

      <select
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

      <label>
        Size
        <input
          type="range"
          min={14}
          max={40}
          value={props.fontSize}
          onChange={(e) => props.onFontSize(Number(e.target.value))}
        />
      </label>

      <label title="Translate only completed phrases — slower Korean, fewer API calls">
        <input
          type="checkbox"
          checked={props.finalsOnly}
          onChange={(e) => props.onFinalsOnly(e.target.checked)}
        />
        Finals only
      </label>

      <button onClick={exportTranscript} disabled={!props.phrases.length}>
        Export
      </button>
    </div>
  );
}
