import { useCallback, useEffect, useRef, useState } from "react";

export type ChunkHandler = (pcm: ArrayBuffer, sampleRate: number) => void;

export function useAudioCapture() {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(false);

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const nodeRef = useRef<AudioWorkletNode | null>(null);

  const refreshDevices = useCallback(async () => {
    const all = await navigator.mediaDevices.enumerateDevices();
    setDevices(all.filter((d) => d.kind === "audioinput"));
  }, []);

  const start = useCallback(
    async (onChunk: ChunkHandler) => {
      setError(null);
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            deviceId: deviceId ? { exact: deviceId } : undefined,
            echoCancellation: true,
            noiseSuppression: true,
            channelCount: 1,
          },
        });
        streamRef.current = stream;
        await refreshDevices(); // labels populate only after permission

        // Browsers may ignore the requested rate; read back what we got.
        const ctx = new AudioContext({ sampleRate: 16000 });
        ctxRef.current = ctx;
        await ctx.audioWorklet.addModule("/pcm-worklet.js");

        const node = new AudioWorkletNode(ctx, "pcm-worklet");
        nodeRef.current = node;
        node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
          onChunk(event.data, ctx.sampleRate);
        };

        ctx.createMediaStreamSource(stream).connect(node);
        setActive(true);
      } catch (e) {
        const message =
          e instanceof DOMException && e.name === "NotAllowedError"
            ? "Microphone permission denied. Allow access in your browser's site settings and try again."
            : `Could not start audio capture: ${String(e)}`;
        setError(message);
        setActive(false);
      }
    },
    [deviceId, refreshDevices],
  );

  const stop = useCallback(() => {
    nodeRef.current?.disconnect();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    void ctxRef.current?.close();
    nodeRef.current = null;
    streamRef.current = null;
    ctxRef.current = null;
    setActive(false);
  }, []);

  useEffect(() => {
    void refreshDevices();
    return stop;
  }, [refreshDevices, stop]);

  return { start, stop, devices, deviceId, setDeviceId, error, active };
}
